"""FastAPI dependencies: DB session, shared clients, internal (rg_sid) / external (rg_ext_sid) actors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from arq import ArqRedis
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .errors import ApiError, not_found
from .models import DiffRun, OtpSession, Part, ReviewLink, User, Workspace
from .security import EXTERNAL_COOKIE, INTERNAL_COOKIE, sha256_hex
from .storage import Storage


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_arq(request: Request) -> ArqRedis:
    return request.app.state.arq


def session_key(token: str) -> str:
    return f"rg:sess:{sha256_hex(token)}"


@dataclass
class CurrentUser:
    user: User
    workspace: Workspace


async def _internal(request: Request, db: AsyncSession) -> CurrentUser | None:
    token = request.cookies.get(INTERNAL_COOKIE)
    if not token:
        return None
    uid = await request.app.state.redis.get(session_key(token))
    if not uid:
        return None
    u = await db.get(User, UUID(uid))
    if u is None:
        return None
    return CurrentUser(u, await db.get(Workspace, u.workspace_id))


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> CurrentUser:
    cu = await _internal(request, db)
    if cu is None:
        raise ApiError(401, "UNAUTHENTICATED", "rg_sid session required")
    return cu


@dataclass
class ExternalSession:
    otp: OtpSession
    link: ReviewLink


async def external_session(request: Request, db: AsyncSession) -> ExternalSession | None:
    token = request.cookies.get(EXTERNAL_COOKIE)
    if not token:
        return None
    otp = (await db.execute(select(OtpSession).where(OtpSession.session_token_hash == sha256_hex(token)))).scalar_one_or_none()
    if otp is None or otp.verified_at is None or otp.session_expires_at is None or otp.session_expires_at <= utcnow():
        return None
    return ExternalSession(otp, await db.get(ReviewLink, otp.review_link_id))


@dataclass
class ReviewActor:
    kind: Literal["INTERNAL", "EXTERNAL"]
    contact: str
    label: str
    workspace_id: UUID | None = None
    user: User | None = None
    link: ReviewLink | None = None


async def review_actor(request: Request, db: AsyncSession = Depends(get_db)) -> ReviewActor:
    """`/v1/review/*` accepts an internal rg_sid session or an external rg_ext_sid session."""
    cu = await _internal(request, db)
    if cu is not None:
        return ReviewActor("INTERNAL", cu.user.email, cu.user.name, cu.workspace.id, user=cu.user)
    ext = await external_session(request, db)
    if ext is not None:
        return ReviewActor("EXTERNAL", ext.link.recipient_contact, ext.link.recipient_name, link=ext.link)
    raise ApiError(401, "UNAUTHENTICATED", "rg_sid or rg_ext_sid session required")


async def run_for_actor(db: AsyncSession, run_id: UUID, actor: ReviewActor, *, write: bool = False) -> tuple[DiffRun, Part]:
    run = await db.get(DiffRun, run_id)
    if run is None:
        raise not_found("diff run")
    p = await db.get(Part, run.part_id)
    if actor.kind == "INTERNAL":
        if p.workspace_id != actor.workspace_id:
            raise not_found("diff run")
    else:
        # rg_ext_sid is scoped to exactly one review_link_id -> one diff run
        if actor.link.diff_run_id != run.id:
            raise ApiError(403, "LINK_SCOPE", "session is scoped to another review link")
        if actor.link.status not in ("ACTIVE", "APPROVED") or actor.link.expires_at <= utcnow():
            raise ApiError(410, f"LINK_{actor.link.status}", "review link is no longer valid")
        if write and actor.link.status != "ACTIVE":
            raise ApiError(409, "LINK_LOCKED", "review link is approved; the snapshot is locked")
    return run, p


async def part_for_user(db: AsyncSession, part_id: UUID, cu: CurrentUser) -> Part:
    p = await db.get(Part, part_id)
    if p is None or p.workspace_id != cu.workspace.id:
        raise not_found("part")
    return p
