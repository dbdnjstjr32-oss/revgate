"""[7. 공장 전용 무료 도면 접수함 (Shop Inbox PLG)]

A client of the machine shop submits two STEP revisions without an account:
presign (2 PUT URLs, ids pre-allocated) -> PUT -> complete (hash-verified) -> Part + 2 Revisions +
DiffRun are created in the shop's workspace and q-ingest x2 + q-diff-batch are enqueued.
The pending submission lives in Redis (24 h) — the 11-table schema has no submission table.
"""

from __future__ import annotations

import json
from uuid import UUID

from arq import ArqRedis
from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, mailer
from .. import serializers as S
from ..config import Settings
from ..deps import CurrentUser, current_user, get_arq, get_db, get_redis, get_settings_dep, get_storage
from ..errors import ApiError, not_found
from ..ids import uuid7
from ..models import IDENTITY_4X4, DiffRun, Part, Revision, ShopInbox
from ..queue import enqueue_diff, enqueue_ingest
from ..schemas import InboxIn, SubmissionCompleteIn, SubmissionPresignIn
from ..storage import Storage, raw_key
from .parts import check_size, verify_upload

router = APIRouter(prefix="/v1", tags=["shop-inbox"])
SUBMISSION_TTL = 24 * 3600


def _sub_key(submission_id) -> str:
    return f"rg:shop:sub:{submission_id}"


async def _active_inbox(db: AsyncSession, slug: str) -> ShopInbox:
    inbox = (await db.execute(select(ShopInbox).where(ShopInbox.slug == slug))).scalar_one_or_none()
    if inbox is None:
        raise not_found("shop inbox")
    if not inbox.is_active:
        raise ApiError(410, "INBOX_INACTIVE", "this shop inbox is closed")
    return inbox


@router.post("/shop-inboxes")
async def create_inbox(body: InboxIn, request: Request, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db),
                       settings: Settings = Depends(get_settings_dep)):
    if (await db.execute(select(ShopInbox.id).where(ShopInbox.slug == body.slug))).first():
        raise ApiError(409, "SLUG_TAKEN", f"{body.slug} is already used")
    inbox = ShopInbox(workspace_id=cu.workspace.id, slug=body.slug, shop_name=body.shop_name, owner_contact=body.owner_contact)
    db.add(inbox)
    await db.flush()
    audit.record(db, settings, workspace_id=cu.workspace.id, entity_type="shop_inbox", entity_id=inbox.id, actor=cu.user.email,
                 action="INBOX_CREATED", payload=body.model_dump(), request=request)
    await db.commit()
    return {"inbox": S.inbox(inbox), "public_url": f"{settings.web_base_url}/shop/{inbox.slug}"}


@router.get("/public/shop/{slug}")
async def public_inbox(slug: str, db: AsyncSession = Depends(get_db)):
    inbox = (await db.execute(select(ShopInbox).where(ShopInbox.slug == slug))).scalar_one_or_none()
    if inbox is None:
        raise not_found("shop inbox")
    return {"shop_name": inbox.shop_name, "is_active": inbox.is_active}


@router.post("/public/shop/{slug}/submissions/presign")
async def submission_presign(slug: str, body: SubmissionPresignIn, request: Request, db: AsyncSession = Depends(get_db),
                             redis: Redis = Depends(get_redis), storage: Storage = Depends(get_storage),
                             settings: Settings = Depends(get_settings_dep)):
    inbox = await _active_inbox(db, slug)
    for f in body.files:
        check_size(settings, f.file_size_bytes)
    if body.files[0].file_sha256 == body.files[1].file_sha256:
        raise ApiError(422, "IDENTICAL_FILES", "v1 and v2 are the same file")
    submission_id, part_id = uuid7(), uuid7()
    uploads = []
    for idx, f in enumerate(body.files):
        rid = uuid7()
        uploads.append({"revision_id": str(rid), "rev_label": f.rev_label or f"v{idx + 1}", "filename": f.filename,
                        "file_size_bytes": f.file_size_bytes, "file_sha256": f.file_sha256,
                        "r2_key": raw_key(inbox.workspace_id, part_id, rid)})
    record = {"slug": slug, "inbox_id": str(inbox.id), "workspace_id": str(inbox.workspace_id), "part_id": str(part_id),
              "sender_name": body.sender_name, "sender_contact": body.sender_contact, "part_number": body.part_number,
              "uploads": uploads}
    await redis.set(_sub_key(submission_id), json.dumps(record), ex=SUBMISSION_TTL)
    audit.record(db, settings, workspace_id=inbox.workspace_id, entity_type="shop_inbox", entity_id=inbox.id,
                 actor=body.sender_contact, action="SUBMISSION_PRESIGNED",
                 payload={"submission_id": str(submission_id), "part_number": body.part_number}, request=request)
    await db.commit()
    return {"submission_id": str(submission_id), "expires_in": settings.put_url_ttl_sec,
            "uploads": [{**u, "put_url": storage.presign_put(storage.raw, u["r2_key"], u["file_size_bytes"]),
                         "headers": {"Content-Type": "application/octet-stream"}} for u in uploads]}


@router.post("/public/shop/{slug}/submissions/complete")
async def submission_complete(slug: str, body: SubmissionCompleteIn, request: Request, db: AsyncSession = Depends(get_db),
                              redis: Redis = Depends(get_redis), storage: Storage = Depends(get_storage),
                              arq: ArqRedis = Depends(get_arq), settings: Settings = Depends(get_settings_dep)):
    inbox = await _active_inbox(db, slug)
    raw = await redis.get(_sub_key(body.submission_id))
    if not raw:
        raise not_found("submission")
    sub = json.loads(raw)
    if sub["slug"] != slug:
        raise not_found("submission")
    for u in sub["uploads"]:
        failure = await verify_upload(storage, u["r2_key"], u["file_size_bytes"], u["file_sha256"])
        if failure:
            raise ApiError(409, failure, f"{u['rev_label']} upload is missing or does not match its SHA-256", rev_label=u["rev_label"])

    part = Part(id=UUID(sub["part_id"]), workspace_id=inbox.workspace_id, shop_inbox_id=inbox.id,
                part_number=sub["part_number"], name=sub["part_number"])
    db.add(part)
    await db.flush()
    submitted_by = {"name": sub["sender_name"], "contact": sub["sender_contact"], "source": "SHOP_INBOX",
                    "submission_id": str(body.submission_id)}
    revs = []
    for u in sub["uploads"]:
        rev = Revision(id=UUID(u["revision_id"]), part_id=part.id, rev_label=u["rev_label"], file_sha256=u["file_sha256"],
                       file_size_bytes=u["file_size_bytes"], raw_r2_key=u["r2_key"], status="QUEUED",
                       spec_card_json={**(body.spec_card_json or {}), "filename": u["filename"], "submitted_by": submitted_by})
        db.add(rev)
        revs.append(rev)
    await db.flush()
    run = DiffRun(part_id=part.id, base_rev_id=revs[0].id, target_rev_id=revs[1].id, align_method="IDENTITY",
                  transform_4x4=IDENTITY_4X4)
    db.add(run)
    await db.flush()
    audit.record(db, settings, workspace_id=inbox.workspace_id, entity_type="shop_inbox", entity_id=inbox.id,
                 actor=sub["sender_contact"], action="SUBMISSION_COMPLETED",
                 payload={"part_id": str(part.id), "diff_run_id": str(run.id), "revisions": [str(r.id) for r in revs]},
                 request=request)
    await db.commit()
    await redis.delete(_sub_key(body.submission_id))
    for rev in revs:
        await enqueue_ingest(arq, rev.id)
    await enqueue_diff(arq, run.id)
    await mailer.send(redis, settings, to=inbox.owner_contact, kind="INBOX_SUBMISSION",
                      subject=f"[{inbox.shop_name}] 새 도면 접수: {sub['part_number']}",
                      body=f"{sub['sender_name']}님이 {sub['part_number']} 수정 도면(v1→v2)을 올렸습니다.",
                      data={"part_id": str(part.id), "diff_run_id": str(run.id)})
    return {"part_id": str(part.id), "revision_ids": [str(r.id) for r in revs], "diff_run_id": str(run.id), "status": "QUEUED"}
