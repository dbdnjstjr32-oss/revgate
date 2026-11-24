"""Phase 4 endpoints (docs/PHASE4_MANUFACTURING_SPEC.md §4.1) — internal session (rg_sid) only.

PATCH  /v1/revisions/{rev_id}/spec                           SpecCardV2 -> spec_version++ (+ SPEC_UPDATED supersede)
POST   /v1/revisions/{rev_id}/attachments/presign            2D PDF / DXF / spec sheet (<= 25 MB, 10 min PUT URL)
POST   /v1/revisions/{rev_id}/attachments/{att_id}/complete  size + SHA-256 + content sniff -> READY (+ DRAWING_UPDATED)
DELETE /v1/revisions/{rev_id}/attachments/{att_id}           status DELETED (+ DRAWING_UPDATED when it was READY)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from revgate_contracts import SpecCardV2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from .. import serializers as S
from .. import spec as SP
from ..config import Settings
from ..deps import CurrentUser, current_user, get_db, get_settings_dep, get_storage, utcnow
from ..errors import ApiError, not_found
from ..ids import uuid7
from ..models import Part, Revision, RevisionAttachment
from ..schemas import ATTACHMENT_MAX_BYTES, AttachmentPresignIn
from ..storage import Storage, attachment_key
from ..supersede import supersede_target_links
from .parts import verify_upload

router = APIRouter(prefix="/v1/revisions", tags=["manufacturing"])

# kind -> (allowed extensions, allowed content types)
KIND_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "2D_PDF": ((".pdf",), ("application/pdf",)),
    "2D_DXF": ((".dxf",), ("application/dxf", "image/vnd.dxf", "image/x-dxf", "application/octet-stream")),
    "SPEC_SHEET": ((".pdf", ".xlsx", ".xls", ".csv", ".txt", ".docx", ".doc"), (
        "application/pdf", "text/csv", "text/plain", "application/octet-stream", "application/vnd.ms-excel",
        "application/msword", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
}


async def _revision(db: AsyncSession, rev_id: UUID, cu: CurrentUser, *, lock: bool = False) -> tuple[Revision, Part]:
    stmt = select(Revision).where(Revision.id == rev_id)
    if lock:
        stmt = stmt.with_for_update()
    rev = (await db.execute(stmt)).scalar_one_or_none()
    if rev is None:
        raise not_found("revision")
    part = await db.get(Part, rev.part_id)
    if part.workspace_id != cu.workspace.id:
        raise not_found("revision")
    return rev, part


async def _attachment(db: AsyncSession, rev: Revision, att_id: UUID) -> RevisionAttachment:
    att = (await db.execute(select(RevisionAttachment).where(RevisionAttachment.id == att_id).with_for_update())).scalar_one_or_none()
    if att is None or att.revision_id != rev.id:
        raise not_found("attachment")
    return att


@router.patch("/{rev_id}/spec")
async def update_spec(rev_id: UUID, body: SpecCardV2, request: Request, cu: CurrentUser = Depends(current_user),
                      db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    rev, part = await _revision(db, rev_id, cu, lock=True)
    card = SP.canonical(body)
    if card == S.spec_card(rev, part):
        return {"revision": S.revision(rev, part), "changed": False, "superseded_links": 0}
    rev.spec_card_json = SP.stored_card(rev.spec_card_json, card)
    rev.spec_version = rev.spec_version + 1
    rev.spec_updated_at = utcnow()
    audit.record(db, settings, workspace_id=part.workspace_id, entity_type="revision", entity_id=rev.id, actor=cu.user.email,
                 action="SPEC_UPDATED", payload={"spec_version": rev.spec_version, "spec_card": card}, request=request)
    superseded = await supersede_target_links(db, settings, target=rev, part=part, reason="SPEC_UPDATED", actor=cu.user.email,
                                              request=request, extra={"spec_version": rev.spec_version})
    await db.commit()
    await db.refresh(rev)
    return {"revision": S.revision(rev, part), "changed": True, "superseded_links": superseded}


@router.post("/{rev_id}/attachments/presign")
async def presign_attachment(rev_id: UUID, body: AttachmentPresignIn, request: Request, cu: CurrentUser = Depends(current_user),
                             db: AsyncSession = Depends(get_db), storage: Storage = Depends(get_storage),
                             settings: Settings = Depends(get_settings_dep)):
    rev, part = await _revision(db, rev_id, cu)
    if body.file_size_bytes > ATTACHMENT_MAX_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", f"max {ATTACHMENT_MAX_BYTES} bytes", max_bytes=ATTACHMENT_MAX_BYTES)
    extensions, content_types = KIND_RULES[body.kind]
    if not body.filename.lower().endswith(extensions):
        raise ApiError(422, "ATTACHMENT_EXTENSION", f"{body.kind} accepts {', '.join(extensions)}", allowed=list(extensions))
    content_type = body.content_type.split(";")[0].strip().lower()
    if content_type not in content_types:
        raise ApiError(422, "ATTACHMENT_CONTENT_TYPE", f"{body.kind} accepts {', '.join(content_types)}",
                       allowed=list(content_types))
    att_id = uuid7()
    att = RevisionAttachment(id=att_id, revision_id=rev.id, kind=body.kind, filename=body.filename, content_type=content_type,
                             file_size_bytes=body.file_size_bytes, file_sha256=body.file_sha256, uploaded_by=cu.user.id,
                             r2_key=attachment_key(part.workspace_id, part.id, rev.id, att_id))
    db.add(att)
    await db.flush()
    audit.record(db, settings, workspace_id=part.workspace_id, entity_type="revision_attachment", entity_id=att.id,
                 actor=cu.user.email, action="ATTACHMENT_PRESIGNED", payload=body.model_dump(), request=request)
    await db.commit()
    return {"attachment_id": str(att.id), "put_url": storage.presign_put(storage.raw, att.r2_key, att.file_size_bytes, content_type),
            "expires_in": settings.put_url_ttl_sec, "headers": {"Content-Type": content_type}}


async def _sniff(storage: Storage, att: RevisionAttachment) -> str | None:
    """None when the stored bytes look like the declared kind, else an error code."""
    if att.kind == "2D_PDF":
        return None if b"%PDF-" in await storage.read_prefix(storage.raw, att.r2_key, 1024) else "CONTENT_MISMATCH"
    if att.kind == "2D_DXF":
        head = await storage.read_prefix(storage.raw, att.r2_key, 256)
        if head.startswith(b"AutoCAD Binary DXF"):
            return "DXF_BINARY_UNSUPPORTED"  # the browser preview (dxf-parser) reads ASCII DXF only
        return None if b"SECTION" in head else "CONTENT_MISMATCH"
    return None


@router.post("/{rev_id}/attachments/{att_id}/complete")
async def complete_attachment(rev_id: UUID, att_id: UUID, request: Request, cu: CurrentUser = Depends(current_user),
                              db: AsyncSession = Depends(get_db), storage: Storage = Depends(get_storage),
                              settings: Settings = Depends(get_settings_dep)):
    rev, part = await _revision(db, rev_id, cu)
    att = await _attachment(db, rev, att_id)
    if att.status != "PENDING_UPLOAD":
        raise ApiError(409, "INVALID_STATE", f"attachment is {att.status}")
    failure = await verify_upload(storage, att.r2_key, att.file_size_bytes, att.file_sha256)
    if failure == "UPLOAD_NOT_FOUND":
        raise ApiError(409, failure, "no object was uploaded for this attachment yet")
    if failure is None:
        failure = await _sniff(storage, att)
        if failure:
            await storage.delete(storage.raw, att.r2_key)
    if failure:
        att.status = "DELETED"
        audit.record(db, settings, workspace_id=part.workspace_id, entity_type="revision_attachment", entity_id=att.id,
                     actor=cu.user.email, action="ATTACHMENT_REJECTED", payload={"reason": failure}, request=request)
        await db.commit()
        raise ApiError(409, failure, "uploaded drawing does not match the declared size / SHA-256 / file type")
    att.status = "READY"
    audit.record(db, settings, workspace_id=part.workspace_id, entity_type="revision_attachment", entity_id=att.id,
                 actor=cu.user.email, action="ATTACHMENT_ADDED",
                 payload={"revision_id": str(rev.id), "kind": att.kind, "filename": att.filename, "sha256": att.file_sha256},
                 request=request)
    superseded = await supersede_target_links(db, settings, target=rev, part=part, reason="DRAWING_UPDATED", actor=cu.user.email,
                                              request=request, extra={"attachment_id": str(att.id), "change": "ADDED"})
    await db.commit()
    return {"attachment": S.attachment(att, storage.presign_get(storage.raw, att.r2_key)), "superseded_links": superseded}


@router.delete("/{rev_id}/attachments/{att_id}")
async def delete_attachment(rev_id: UUID, att_id: UUID, request: Request, cu: CurrentUser = Depends(current_user),
                            db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    rev, part = await _revision(db, rev_id, cu)
    att = await _attachment(db, rev, att_id)
    if att.status == "DELETED":
        raise ApiError(409, "INVALID_STATE", "attachment is already DELETED")
    was_ready = att.status == "READY"
    att.status = "DELETED"  # the object is kept: approval snapshots may reference its SHA-256
    audit.record(db, settings, workspace_id=part.workspace_id, entity_type="revision_attachment", entity_id=att.id,
                 actor=cu.user.email, action="ATTACHMENT_DELETED", payload={"revision_id": str(rev.id), "kind": att.kind},
                 request=request)
    superseded = 0
    if was_ready:
        superseded = await supersede_target_links(db, settings, target=rev, part=part, reason="DRAWING_UPDATED",
                                                  actor=cu.user.email, request=request,
                                                  extra={"attachment_id": str(att.id), "change": "DELETED"})
    await db.commit()
    return {"attachment": S.attachment(att), "superseded_links": superseded}
