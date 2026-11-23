"""[5. 변경점 체크 & 3D 핀 코멘트] — rg_sid (internal) or rg_ext_sid (external, one link) accepted.

Every mutation is written to audit_events (append-only).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from .. import serializers as S
from ..config import Settings
from ..deps import ReviewActor, get_db, get_settings_dep, review_actor, run_for_actor, utcnow
from ..errors import ApiError, not_found
from ..models import DiffItem, PinComment
from ..schemas import CheckIn, PinIn, ResolveIn

router = APIRouter(prefix="/v1/review", tags=["review"])


@router.patch("/diff-items/{item_id}/check")
async def check_item(item_id: UUID, body: CheckIn, request: Request, actor: ReviewActor = Depends(review_actor),
                     db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    it = await db.get(DiffItem, item_id)
    if it is None:
        raise not_found("diff item")
    _run, p = await run_for_actor(db, it.diff_run_id, actor, write=True)
    if body.checked:
        it.checked_at, it.checked_by_contact = utcnow(), actor.contact
    else:
        it.checked_at, it.checked_by_contact = None, None
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="diff_item", entity_id=it.id, actor=actor.contact,
                 action="DIFF_ITEM_CHECKED" if body.checked else "DIFF_ITEM_UNCHECKED",
                 payload={"change_key": it.change_key, "actor_type": actor.kind}, request=request)
    await db.commit()
    return {"item": S.item(it)}


@router.get("/diffs/{diff_id}/pins")
async def list_pins(diff_id: UUID, actor: ReviewActor = Depends(review_actor), db: AsyncSession = Depends(get_db)):
    run, _p = await run_for_actor(db, diff_id, actor)
    pins = (await db.execute(select(PinComment).where(PinComment.diff_run_id == run.id).order_by(PinComment.created_at))).scalars()
    return {"pins": [S.pin(x) for x in pins]}


@router.post("/diffs/{diff_id}/pins")
async def create_pin(diff_id: UUID, body: PinIn, request: Request, actor: ReviewActor = Depends(review_actor),
                     db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    run, p = await run_for_actor(db, diff_id, actor, write=True)
    if body.diff_item_id is not None:
        it = await db.get(DiffItem, body.diff_item_id)
        if it is None or it.diff_run_id != run.id:
            raise ApiError(422, "ITEM_NOT_IN_RUN", "diff_item_id does not belong to this diff run")
    pin = PinComment(diff_run_id=run.id, diff_item_id=body.diff_item_id,
                     review_link_id=actor.link.id if actor.link else None, author_type=actor.kind, author_label=actor.label,
                     face_id=body.face_id, rev_side=body.rev_side, pin_xyz=list(body.pin_xyz), body=body.body)
    db.add(pin)
    await db.flush()
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="pin_comment", entity_id=pin.id, actor=actor.contact,
                 action="PIN_CREATED", payload={"face_id": body.face_id, "rev_side": body.rev_side, "body": body.body}, request=request)
    await db.commit()
    return {"pin": S.pin(pin)}


@router.patch("/pins/{pin_id}/resolve")
async def resolve_pin(pin_id: UUID, body: ResolveIn, request: Request, actor: ReviewActor = Depends(review_actor),
                      db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    pin = await db.get(PinComment, pin_id)
    if pin is None:
        raise not_found("pin")
    _run, p = await run_for_actor(db, pin.diff_run_id, actor, write=True)
    pin.resolved_at = utcnow() if body.resolved else None
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="pin_comment", entity_id=pin.id, actor=actor.contact,
                 action="PIN_RESOLVED" if body.resolved else "PIN_REOPENED", request=request)
    await db.commit()
    return {"pin": S.pin(pin)}
