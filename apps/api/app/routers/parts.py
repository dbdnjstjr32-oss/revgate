"""[2. 부품 & R2 Presigned 업로드 / 파싱 트리거]

presign -> client PUTs the STEP straight to R2/MinIO (10 min URL, signed Content-Length, <= 50 MB)
complete -> the API re-hashes the stored object (SHA-256 must equal the declared file_sha256),
            then q-ingest. For a ZERO_RETENTION revision whose raw file was purged, presigning the
            same file_sha256 again starts a *hash-verified re-drop* of that revision.
"""

from __future__ import annotations

import json
from uuid import UUID

from arq import ArqRedis
from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from .. import serializers as S
from ..config import Settings
from ..deps import CurrentUser, current_user, get_arq, get_db, get_redis, get_settings_dep, get_storage, part_for_user
from ..errors import ApiError, not_found
from ..ids import uuid7
from ..models import DiffRun, Part, Revision
from ..queue import enqueue_ingest
from ..schemas import CompleteIn, PartIn, PresignIn
from ..storage import Storage, raw_key

router = APIRouter(prefix="/v1", tags=["parts"])


def _redrop_key(rev_id) -> str:
    return f"rg:redrop:{rev_id}"


def check_size(settings: Settings, size: int) -> None:
    if size > settings.max_upload_bytes:
        raise ApiError(413, "FILE_TOO_LARGE", f"max {settings.max_upload_bytes} bytes", max_bytes=settings.max_upload_bytes)


async def verify_upload(storage: Storage, key: str, size: int, sha256: str) -> str | None:
    """None if the stored object matches (size + SHA-256), else the failure code (object deleted)."""
    head = await storage.head(storage.raw, key)
    if head is None:
        return "UPLOAD_NOT_FOUND"
    if int(head["ContentLength"]) != size:
        await storage.delete(storage.raw, key)
        return "SIZE_MISMATCH"
    actual, _n = await storage.sha256(storage.raw, key)
    if actual != sha256:
        await storage.delete(storage.raw, key)
        return "SHA256_MISMATCH"
    return None


@router.post("/parts")
async def create_part(body: PartIn, request: Request, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db),
                      settings: Settings = Depends(get_settings_dep)):
    dup = await db.execute(select(Part.id).where(Part.workspace_id == cu.workspace.id, Part.part_number == body.part_number,
                                                 Part.shop_inbox_id.is_(None)))
    if dup.first():
        raise ApiError(409, "PART_EXISTS", f"part {body.part_number} already exists")
    p = Part(workspace_id=cu.workspace.id, part_number=body.part_number, name=body.name,
             default_material=body.default_material, default_finish=body.default_finish)
    db.add(p)
    await db.flush()
    audit.record(db, settings, workspace_id=cu.workspace.id, entity_type="part", entity_id=p.id, actor=cu.user.email,
                 action="PART_CREATED", payload=body.model_dump(), request=request)
    await db.commit()
    return {"part": S.part(p)}


@router.get("/parts")
async def list_parts(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Part).where(Part.workspace_id == cu.workspace.id).order_by(Part.created_at.desc()))).scalars()
    return {"items": [S.part(p) for p in rows]}


@router.get("/parts/{part_id}")
async def get_part(part_id: UUID, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db),
                   storage: Storage = Depends(get_storage)):
    """Web read model: the part with its revisions (+ SpecCardV2, READY 2D attachments) and diff runs (newest first)."""
    p = await part_for_user(db, part_id, cu)
    revs = (await db.execute(select(Revision).where(Revision.part_id == p.id).order_by(Revision.created_at.desc()))).scalars().all()
    runs = (await db.execute(select(DiffRun).where(DiffRun.part_id == p.id).order_by(DiffRun.created_at.desc()))).scalars()
    atts = await S.ready_attachments(db, [r.id for r in revs])
    return {"part": S.part(p),
            "revisions": [{**S.revision(r, p), "attachments": S.attachment_list(storage, atts[r.id])} for r in revs],
            "diff_runs": [S.diff_run(d) for d in runs]}


@router.post("/parts/{part_id}/revisions/presign")
async def presign_revision(part_id: UUID, body: PresignIn, request: Request, cu: CurrentUser = Depends(current_user),
                           db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis),
                           storage: Storage = Depends(get_storage), settings: Settings = Depends(get_settings_dep)):
    p = await part_for_user(db, part_id, cu)
    check_size(settings, body.file_size_bytes)
    existing = (await db.execute(select(Revision).where(Revision.part_id == p.id, Revision.file_sha256 == body.file_sha256)
                                 .order_by(Revision.created_at.desc()).limit(1))).scalar_one_or_none()
    if existing is not None:
        if existing.raw_r2_key is None and existing.raw_purged_at is not None:
            key = raw_key(p.workspace_id, p.id, existing.id)
            await redis.set(_redrop_key(existing.id), json.dumps({"key": key, "size": body.file_size_bytes}),
                            ex=settings.put_url_ttl_sec + 120)
            audit.record(db, settings, workspace_id=p.workspace_id, entity_type="revision", entity_id=existing.id,
                         actor=cu.user.email, action="REDROP_REQUESTED", payload={"file_sha256": body.file_sha256}, request=request)
            await db.commit()
            return {"revision_id": str(existing.id), "put_url": storage.presign_put(storage.raw, key, body.file_size_bytes),
                    "r2_key": key, "redrop": True, "expires_in": settings.put_url_ttl_sec,
                    "headers": {"Content-Type": "application/octet-stream"}}
        raise ApiError(409, "REVISION_EXISTS", "this file is already a revision of the part", revision_id=str(existing.id))

    rid = uuid7()
    key = raw_key(p.workspace_id, p.id, rid)
    rev = Revision(id=rid, part_id=p.id, uploaded_by=cu.user.id, rev_label=body.rev_label, file_sha256=body.file_sha256,
                   file_size_bytes=body.file_size_bytes, raw_r2_key=key, spec_card_json={"filename": body.filename})
    db.add(rev)
    await db.flush()
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="revision", entity_id=rid, actor=cu.user.email,
                 action="REVISION_PRESIGNED", payload=body.model_dump(), request=request)
    await db.commit()
    return {"revision_id": str(rid), "put_url": storage.presign_put(storage.raw, key, body.file_size_bytes), "r2_key": key,
            "redrop": False, "expires_in": settings.put_url_ttl_sec, "headers": {"Content-Type": "application/octet-stream"}}


async def _revision_for_user(db: AsyncSession, rev_id: UUID, cu: CurrentUser) -> tuple[Revision, Part]:
    rev = await db.get(Revision, rev_id)
    if rev is None:
        raise not_found("revision")
    p = await db.get(Part, rev.part_id)
    if p.workspace_id != cu.workspace.id:
        raise not_found("revision")
    return rev, p


@router.post("/revisions/{rev_id}/complete")
async def complete_revision(rev_id: UUID, request: Request, body: CompleteIn | None = None, cu: CurrentUser = Depends(current_user),
                            db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis),
                            storage: Storage = Depends(get_storage), arq: ArqRedis = Depends(get_arq),
                            settings: Settings = Depends(get_settings_dep)):
    rev, p = await _revision_for_user(db, rev_id, cu)
    body = body or CompleteIn()

    redrop_raw = await redis.get(_redrop_key(rev.id))
    if redrop_raw and rev.raw_r2_key is None:
        redrop = json.loads(redrop_raw)
        failure = await verify_upload(storage, redrop["key"], redrop["size"], rev.file_sha256)
        if failure:
            await redis.delete(_redrop_key(rev.id))
            audit.record(db, settings, workspace_id=p.workspace_id, entity_type="revision", entity_id=rev.id, actor=cu.user.email,
                         action="REDROP_REJECTED", payload={"reason": failure}, request=request)
            await db.commit()
            raise ApiError(409, failure, "re-dropped file does not match the purged revision (hash-verified re-drop)")
        rev.raw_r2_key = redrop["key"]  # raw_purged_at stays: purge history is tracked orthogonally
        audit.record(db, settings, workspace_id=p.workspace_id, entity_type="revision", entity_id=rev.id, actor=cu.user.email,
                     action="RAW_REDROPPED", payload={"file_sha256": rev.file_sha256}, request=request)
        await db.commit()
        await redis.delete(_redrop_key(rev.id))
        return {"revision_id": str(rev.id), "status": rev.status, "redropped": True}

    if rev.status != "PENDING_UPLOAD":
        raise ApiError(409, "INVALID_STATE", f"revision is {rev.status}")
    failure = await verify_upload(storage, rev.raw_r2_key, rev.file_size_bytes, rev.file_sha256)
    if failure == "UPLOAD_NOT_FOUND":
        raise ApiError(409, failure, "no object was uploaded for this revision yet")
    if failure:
        rev.status, rev.error_code = "FAILED", failure
        audit.record(db, settings, workspace_id=p.workspace_id, entity_type="revision", entity_id=rev.id, actor=cu.user.email,
                     action="REVISION_REJECTED", payload={"reason": failure}, request=request)
        await db.commit()
        raise ApiError(409, failure, "uploaded object does not match the declared size / SHA-256")
    rev.status = "QUEUED"
    if body.spec_card_json:
        rev.spec_card_json = {**rev.spec_card_json, **body.spec_card_json}
    audit.record(db, settings, workspace_id=p.workspace_id, entity_type="revision", entity_id=rev.id, actor=cu.user.email,
                 action="REVISION_UPLOADED", payload={"file_sha256": rev.file_sha256, "size": rev.file_size_bytes}, request=request)
    await db.commit()
    await enqueue_ingest(arq, rev.id)
    return {"revision_id": str(rev.id), "status": "QUEUED"}


@router.get("/revisions/{rev_id}")
async def get_revision(rev_id: UUID, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db),
                       storage: Storage = Depends(get_storage)):
    rev, p = await _revision_for_user(db, rev_id, cu)
    atts = await S.ready_attachments(db, [rev.id])
    return {"revision": S.revision(rev, p), "signed_urls": S.revision_urls(storage, rev),
            "attachments": S.attachment_list(storage, atts[rev.id])}
