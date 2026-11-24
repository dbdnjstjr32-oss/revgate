"""[3. Diff 생성 / SSE / 3-2-1 재정합] + [4. 공유 링크 (internal)] + [6. 승인 증빙 PDF (internal)]

DiffRunStatus: QUEUED -> COMPUTING -> READY | NEEDS_ALIGN (confidence < 0.5) | FAILED;
               READY / NEEDS_ALIGN --realign--> COMPUTING (q-diff-interactive).
Policy: diff_items exist only while the run is READY (written by the worker).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from uuid import UUID

from arq import ArqRedis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from .. import serializers as S
from ..config import Settings
from ..context import spec_context
from ..deps import CurrentUser, current_user, get_arq, get_db, get_redis, get_settings_dep, get_storage, part_for_user, utcnow
from ..errors import ApiError, not_found
from ..events import STATUS_EVENT, TERMINAL_EVENTS, diff_channel, sse
from ..models import IDENTITY_4X4, DiffItem, DiffRun, Part, Revision, ReviewLink
from ..queue import enqueue_diff
from ..schemas import DiffIn, LinkIn, RealignIn
from ..security import is_rigid_transform, new_token, sha256_hex
from ..storage import Storage
from ..supersede import latest_revision_label

router = APIRouter(prefix="/v1", tags=["diffs"])


async def run_for_user(db: AsyncSession, diff_id: UUID, cu: CurrentUser) -> tuple[DiffRun, Part]:
    run = await db.get(DiffRun, diff_id)
    if run is None:
        raise not_found("diff run")
    p = await db.get(Part, run.part_id)
    if p.workspace_id != cu.workspace.id:
        raise not_found("diff run")
    return run, p


def _require_raw(rev: Revision, side: str) -> None:
    if rev.status in ("PENDING_UPLOAD", "FAILED"):
        raise ApiError(409, "REVISION_NOT_READY", f"{side} revision is {rev.status}", revision_id=str(rev.id))
    if rev.raw_r2_key is None:
        raise ApiError(409, "RAW_PURGED_REDROP_REQUIRED",
                       f"{side} raw STEP was purged (zero-retention); re-drop the original file first", revision_id=str(rev.id))


@router.post("/parts/{part_id}/diffs")
async def create_diff(part_id: UUID, body: DiffIn, request: Request, cu: CurrentUser = Depends(current_user),
                      db: AsyncSession = Depends(get_db), arq: ArqRedis = Depends(get_arq),
                      settings: Settings = Depends(get_settings_dep)):
    p = await part_for_user(db, part_id, cu)
    if body.base_rev_id == body.target_rev_id:
        raise ApiError(422, "SAME_REVISION", "base and target must differ")
    base, target = await db.get(Revision, body.base_rev_id), await db.get(Revision, body.target_rev_id)
    if base is None or target is None or base.part_id != p.id or target.part_id != p.id:
        raise not_found("revision")
    _require_raw(base, "base")
    _require_raw(target, "target")
    run = DiffRun(part_id=p.id, base_rev_id=base.id, target_rev_id=target.id, align_method="IDENTITY", transform_4x4=IDENTITY_4X4)
    db.add(run)
    await db.flush()
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="diff_run", entity_id=run.id, actor=cu.user.email,
                 action="DIFF_REQUESTED", payload={"base": str(base.id), "target": str(target.id)}, request=request)
    await db.commit()
    await enqueue_diff(arq, run.id)
    return {"diff_run_id": str(run.id), "status": run.status}


@router.get("/diffs/{diff_id}")
async def get_diff(diff_id: UUID, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db),
                   redis: Redis = Depends(get_redis), storage: Storage = Depends(get_storage)):
    run, p = await run_for_user(db, diff_id, cu)
    items = []
    if run.status == "READY":
        items = (await db.execute(select(DiffItem).where(DiffItem.diff_run_id == run.id))).scalars().all()
    base, target = await db.get(Revision, run.base_rev_id), await db.get(Revision, run.target_rev_id)
    ctx = await spec_context(db, p, base, target, items)
    return {"diff_run": S.diff_run(run), "items": [S.item(i) for i in sorted(items, key=_item_order)],
            "signed_urls": await S.diff_urls(storage, redis, run, base, target),
            # Phase 4: manufacturing spec + 2D drawings of both revisions
            "revisions": {"base": S.revision(base, p), "target": S.revision(target, p)},
            "attachments": ctx.attachments_json(storage), "spec_diffs": ctx.spec_diffs, "spec_warnings": ctx.spec_warnings}


SEVERITY_RANK = {"CRITICAL": 0, "MAJOR": 1, "INFO": 2}


def _item_order(i: DiffItem):
    return (SEVERITY_RANK.get(i.severity, 9), i.category, i.change_key)


@router.get("/diffs/{diff_id}/events")
async def diff_events(diff_id: UUID, request: Request, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db),
                      redis: Redis = Depends(get_redis)):
    """SSE: progress | ready | needs_align | failed (Redis Pub/Sub fan-out from the CAD worker)."""
    await run_for_user(db, diff_id, cu)
    sessionmaker = request.app.state.sessionmaker

    async def stream():
        pubsub = redis.pubsub()
        await pubsub.subscribe(diff_channel(diff_id))  # subscribe first, then snapshot: no lost events
        try:
            async with sessionmaker() as s:
                run = await s.get(DiffRun, diff_id)
                snapshot = S.diff_run(run)
            yield "retry: 2000\n\n"
            if run.status in STATUS_EVENT:
                yield sse(STATUS_EVENT[run.status], {"diff_run_id": str(diff_id), "status": run.status, "snapshot": True,
                                                     "diff_run": snapshot})
                return
            yield sse("progress", {"diff_run_id": str(diff_id), "status": run.status, "snapshot": True})
            last_beat = time.monotonic()
            while not await request.is_disconnected():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    evt = json.loads(msg["data"])
                    yield sse(evt["event"], evt["data"])
                    if evt["event"] in TERMINAL_EVENTS:
                        return
                elif time.monotonic() - last_beat > 15:
                    last_beat = time.monotonic()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0)
        finally:
            await pubsub.unsubscribe(diff_channel(diff_id))
            await pubsub.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.post("/diffs/{diff_id}/realign")
async def realign(diff_id: UUID, body: RealignIn, request: Request, cu: CurrentUser = Depends(current_user),
                  db: AsyncSession = Depends(get_db), arq: ArqRedis = Depends(get_arq),
                  settings: Settings = Depends(get_settings_dep)):
    run, p = await run_for_user(db, diff_id, cu)
    if not is_rigid_transform(body.transform_4x4):
        raise ApiError(422, "INVALID_TRANSFORM", "transform_4x4 must be a rigid row-major Target->Base matrix")
    approved = await db.execute(select(ReviewLink.id).where(ReviewLink.diff_run_id == run.id, ReviewLink.status == "APPROVED"))
    if approved.first():
        raise ApiError(409, "DIFF_LOCKED", "an approval snapshot exists for this diff run")
    for rid, side in ((run.base_rev_id, "base"), (run.target_rev_id, "target")):
        _require_raw(await db.get(Revision, rid), side)
    status_before = run.status  # read now: the ORM state is expired by the rollback below
    res = await db.execute(
        update(DiffRun).where(DiffRun.id == run.id, DiffRun.status.in_(("READY", "NEEDS_ALIGN")))
        .values(status="COMPUTING", align_method="MANUAL_321", transform_4x4=list(body.transform_4x4), updated_at=utcnow())
        .returning(DiffRun.id))
    if res.first() is None:
        await db.rollback()
        raise ApiError(409, "INVALID_STATE", f"diff run is {status_before}; realign needs READY or NEEDS_ALIGN")
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="diff_run", entity_id=run.id, actor=cu.user.email,
                 action="DIFF_REALIGN_REQUESTED", payload={"transform_4x4": body.transform_4x4}, request=request)
    await db.commit()
    await enqueue_diff(arq, run.id, interactive=True)
    return {"diff_run_id": str(run.id), "status": "COMPUTING"}


# ---- [4] share links (internal side) --------------------------------------------------------
@router.post("/diffs/{diff_id}/links")
async def create_link(diff_id: UUID, body: LinkIn, request: Request, cu: CurrentUser = Depends(current_user),
                      db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    run, p = await run_for_user(db, diff_id, cu)
    if run.status != "READY":
        raise ApiError(409, "DIFF_NOT_READY", f"diff run is {run.status}")
    token = new_token()
    link = ReviewLink(diff_run_id=run.id, created_by=cu.user.id, token_hash=sha256_hex(token), recipient_name=body.recipient_name,
                      recipient_contact=body.recipient_contact.strip(), allow_raw_download=body.allow_raw_download,
                      expires_at=utcnow() + timedelta(days=body.expires_in_days))
    db.add(link)
    await db.flush()
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="review_link", entity_id=link.id, actor=cu.user.email,
                 action="LINK_CREATED", payload={"recipient": body.recipient_name, "expires_in_days": body.expires_in_days},
                 request=request)
    await db.commit()
    items = (await db.execute(select(DiffItem).where(DiffItem.diff_run_id == run.id))).scalars().all()
    ctx = await spec_context(db, p, await db.get(Revision, run.base_rev_id), await db.get(Revision, run.target_rev_id), items)
    # Phase 4 §3.2: missing material / tolerance is surfaced when the link is created (acknowledged at approval)
    return {"link_id": str(link.id), "share_url": f"{settings.web_base_url}/r/{token}", "link": S.link(link),
            "spec_warnings": ctx.spec_warnings}


@router.get("/diffs/{diff_id}/links")
async def list_links(diff_id: UUID, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Phase 3 read model: share links of a diff run (tokens are never stored, so no URLs here)."""
    run, p = await run_for_user(db, diff_id, cu)
    links = (await db.execute(select(ReviewLink).where(ReviewLink.diff_run_id == run.id).order_by(ReviewLink.created_at.desc()))).scalars()
    latest = await latest_revision_label(db, p.id)
    return {"items": [S.link(l, latest) for l in links]}


async def _link_for_user(db: AsyncSession, link_id: UUID, cu: CurrentUser) -> tuple[ReviewLink, Part]:
    link = await db.get(ReviewLink, link_id)
    if link is None:
        raise not_found("link")
    run = await db.get(DiffRun, link.diff_run_id)
    p = await db.get(Part, run.part_id)
    if p.workspace_id != cu.workspace.id:
        raise not_found("link")
    return link, p


@router.delete("/links/{link_id}")
async def revoke_link(link_id: UUID, request: Request, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db),
                      settings: Settings = Depends(get_settings_dep)):
    link, p = await _link_for_user(db, link_id, cu)
    if link.status != "ACTIVE":
        raise ApiError(409, "LINK_TERMINAL", f"link is already {link.status}")
    link.status = "REVOKED"
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="review_link", entity_id=link.id, actor=cu.user.email,
                 action="LINK_REVOKED", request=request)
    await db.commit()
    return {"link_id": str(link.id), "status": "REVOKED"}


@router.get("/links/{link_id}/approval-pdf")
async def approval_pdf(link_id: UUID, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db),
                       storage: Storage = Depends(get_storage)):
    link, _p = await _link_for_user(db, link_id, cu)
    if link.status != "APPROVED" or not link.approval_pdf_r2_key:
        raise ApiError(404, "NO_APPROVAL", "link has not been approved")
    return {"pdf_signed_url": storage.presign_get(storage.audit, link.approval_pdf_r2_key, filename=f"approval-{link.id}.pdf"),
            "approved_at": S.iso(link.approved_at), "approval_snapshot_sha256": link.approval_snapshot_sha256,
            "effective_status": "SUPERSEDED" if link.superseded_at is not None else link.status}
