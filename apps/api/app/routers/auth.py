"""[1. 발주처 인증 (Internal Auth)] — magic link -> rg_sid (HttpOnly + Secure + SameSite=Lax, 7 days).

Magic-link tokens and sessions are short-lived secrets, so they live in Redis (only SHA-256 of the
token is stored); the 11-table schema stays exactly as specified.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, mailer
from .. import serializers as S
from ..config import Settings
from ..deps import CurrentUser, current_user, get_db, get_redis, get_settings_dep, session_key
from ..errors import ApiError
from ..models import User, Workspace
from ..schemas import MagicLinkIn, VerifyIn
from ..security import INTERNAL_COOKIE, clear_cookie, new_token, set_cookie, sha256_hex

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _magic_key(token: str) -> str:
    return f"rg:magic:{sha256_hex(token)}"


@router.post("/magic-link")
async def magic_link(body: MagicLinkIn, redis: Redis = Depends(get_redis), settings: Settings = Depends(get_settings_dep)):
    token = new_token()
    await redis.set(_magic_key(token), json.dumps({"email": body.email}), ex=settings.magic_link_ttl_sec)
    url = f"{settings.web_base_url}/auth/verify?token={token}"
    await mailer.send(redis, settings, to=body.email, kind="MAGIC_LINK", subject="RevGate 로그인 링크",
                      body=f"아래 링크로 로그인하세요 ({settings.magic_link_ttl_sec // 60}분 유효): {url}",
                      data={"token": token, "url": url})
    return {"sent": True}  # same answer whether or not the address exists (no enumeration)


@router.post("/verify")
async def verify(body: VerifyIn, request: Request, response: Response, db: AsyncSession = Depends(get_db),
                 redis: Redis = Depends(get_redis), settings: Settings = Depends(get_settings_dep)):
    raw = await redis.getdel(_magic_key(body.token))  # single use
    if not raw:
        raise ApiError(401, "INVALID_OR_EXPIRED_TOKEN", "magic link is invalid, used or expired")
    email = json.loads(raw)["email"]
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        local = email.split("@")[0]
        ws = Workspace(name=f"{local} workspace")
        db.add(ws)
        await db.flush()
        user = User(workspace_id=ws.id, email=email, name=local, role="OWNER")
        db.add(user)
        await db.flush()
        audit.record(db, settings, workspace_id=ws.id, entity_type="user", entity_id=user.id, actor=email,
                     action="USER_SIGNED_UP", payload={"email": email}, request=request)
    else:
        ws = await db.get(Workspace, user.workspace_id)
    audit.record(db, settings, workspace_id=ws.id, entity_type="user", entity_id=user.id, actor=email, action="LOGIN",
                 request=request)
    await db.commit()

    session = new_token()
    await redis.set(session_key(session), str(user.id), ex=settings.session_ttl_sec)
    set_cookie(response, INTERNAL_COOKIE, session, settings.session_ttl_sec, settings)
    return {"verified": True, "user": S.user(user), "workspace": S.workspace(ws)}


@router.get("/me")
async def me(cu: CurrentUser = Depends(current_user)):
    return {"user": S.user(cu.user), "workspace": S.workspace(cu.workspace)}


@router.post("/logout")
async def logout(request: Request, response: Response, redis: Redis = Depends(get_redis),
                 settings: Settings = Depends(get_settings_dep)):
    token = request.cookies.get(INTERNAL_COOKIE)
    if token:
        await redis.delete(session_key(token))
    clear_cookie(response, INTERNAL_COOKIE, settings)
    return {"logged_out": True}
