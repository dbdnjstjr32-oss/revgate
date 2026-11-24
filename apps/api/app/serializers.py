"""JSON views of the ORM rows + signed-URL bundles."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DiffItem, DiffRun, Part, PinComment, Revision, RevisionAttachment, ReviewLink, ShopInbox, User, Workspace
from .security import mask_contact, sha256_hex
from .spec import upgrade_spec_card
from .storage import Storage


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def user(u: User) -> dict[str, Any]:
    return {"id": str(u.id), "email": u.email, "name": u.name, "role": u.role, "workspace_id": str(u.workspace_id),
            "created_at": iso(u.created_at)}


def workspace(w: Workspace) -> dict[str, Any]:
    return {"id": str(w.id), "name": w.name, "plan": w.plan, "security_mode": w.security_mode,
            "retention_days": w.retention_days, "created_at": iso(w.created_at)}


def part(p: Part) -> dict[str, Any]:
    return {"id": str(p.id), "workspace_id": str(p.workspace_id), "shop_inbox_id": str(p.shop_inbox_id) if p.shop_inbox_id else None,
            "part_number": p.part_number, "name": p.name, "default_material": p.default_material,
            "default_finish": p.default_finish, "created_at": iso(p.created_at)}


def spec_card(r: Revision, part: Part | None = None) -> dict[str, Any]:
    """Normalized SpecCardV2 (Phase 1~3 cards are upgraded; the part's default material / finish fill gaps)."""
    return upgrade_spec_card(r.spec_card_json, default_material=part.default_material if part else None,
                             default_finish=part.default_finish if part else None)


def revision(r: Revision, part: Part | None = None) -> dict[str, Any]:
    return {"id": str(r.id), "part_id": str(r.part_id), "rev_label": r.rev_label, "file_sha256": r.file_sha256,
            "file_size_bytes": r.file_size_bytes, "status": r.status, "error_code": r.error_code,
            "raw_available": r.raw_r2_key is not None, "raw_purged_at": iso(r.raw_purged_at),
            "viewer_prefix": r.viewer_prefix, "spec_card_json": r.spec_card_json, "created_at": iso(r.created_at),
            # Phase 4
            "spec_version": r.spec_version, "spec_updated_at": iso(r.spec_updated_at), "spec_card": spec_card(r, part)}


def diff_run(d: DiffRun) -> dict[str, Any]:
    return {"id": str(d.id), "part_id": str(d.part_id), "base_rev_id": str(d.base_rev_id), "target_rev_id": str(d.target_rev_id),
            "align_method": d.align_method, "transform_4x4": list(d.transform_4x4), "align_confidence": d.align_confidence,
            "status": d.status, "error_code": d.error_code, "created_at": iso(d.created_at), "updated_at": iso(d.updated_at)}


def item(i: DiffItem) -> dict[str, Any]:
    return {"id": str(i.id), "diff_run_id": str(i.diff_run_id), "change_key": i.change_key, "category": i.category,
            "severity": i.severity, "title": i.title, "fact": i.fact_json, "hint_text": i.hint_text,
            "anchor_xyz": list(i.anchor_xyz), "checked": i.checked_at is not None, "checked_at": iso(i.checked_at),
            "checked_by_contact": i.checked_by_contact}


def link(l: ReviewLink, latest_revision_label: str | None = None) -> dict[str, Any]:
    return {"id": str(l.id), "diff_run_id": str(l.diff_run_id), "recipient_name": l.recipient_name,
            "masked_contact": mask_contact(l.recipient_contact), "allow_raw_download": l.allow_raw_download,
            "status": l.status, "expires_at": iso(l.expires_at), "approved_at": iso(l.approved_at),
            "created_at": iso(l.created_at),
            # Phase 4: status stays APPROVED; effective_status reports stale approvals
            "effective_status": "SUPERSEDED" if l.superseded_at is not None else l.status,
            "superseded_at": iso(l.superseded_at), "superseded_reason": l.superseded_reason,
            "superseded_by_revision_id": str(l.superseded_by_revision_id) if l.superseded_by_revision_id else None,
            "latest_revision_label": latest_revision_label, "approval_snapshot_sha256": l.approval_snapshot_sha256,
            "dfm_ack": l.dfm_ack_json or {}}


def pin(p: PinComment) -> dict[str, Any]:
    return {"id": str(p.id), "diff_run_id": str(p.diff_run_id), "diff_item_id": str(p.diff_item_id) if p.diff_item_id else None,
            "review_link_id": str(p.review_link_id) if p.review_link_id else None, "author_type": p.author_type,
            "author_label": p.author_label, "face_id": p.face_id, "rev_side": p.rev_side, "pin_xyz": list(p.pin_xyz),
            "body": p.body, "resolved": p.resolved_at is not None, "resolved_at": iso(p.resolved_at), "created_at": iso(p.created_at)}


def inbox(i: ShopInbox) -> dict[str, Any]:
    return {"id": str(i.id), "workspace_id": str(i.workspace_id), "slug": i.slug, "shop_name": i.shop_name,
            "owner_contact": i.owner_contact, "is_active": i.is_active, "created_at": iso(i.created_at)}


def attachment(a: RevisionAttachment, url: str | None = None) -> dict[str, Any]:
    """AttachmentItem (packages/contracts manufacturing.schema.json)."""
    return {"id": str(a.id), "revision_id": str(a.revision_id), "kind": a.kind, "filename": a.filename,
            "content_type": a.content_type, "file_size_bytes": a.file_size_bytes, "file_sha256": a.file_sha256,
            "status": a.status, "created_at": iso(a.created_at), "url": url}


async def ready_attachments(db: AsyncSession, revision_ids) -> dict[Any, list[RevisionAttachment]]:
    out: dict[Any, list[RevisionAttachment]] = {rid: [] for rid in revision_ids}
    rows = (await db.execute(select(RevisionAttachment).where(RevisionAttachment.revision_id.in_(list(revision_ids)),
                                                              RevisionAttachment.status == "READY")
                             .order_by(RevisionAttachment.created_at, RevisionAttachment.id))).scalars()
    for a in rows:
        out.setdefault(a.revision_id, []).append(a)
    return out


def attachment_list(storage: Storage, atts: list[RevisionAttachment]) -> list[dict[str, Any]]:
    """READY attachments with 15 min signed GET URLs (inline: the web renders PDF/DXF itself, no download UI)."""
    return [attachment(a, storage.presign_get(storage.raw, a.r2_key) if a.status == "READY" else None) for a in atts]


# ---- signed URLs -------------------------------------------------------------------------------
def revision_urls(storage: Storage, r: Revision) -> dict[str, str] | None:
    if r.status != "READY" or not r.viewer_prefix:
        return None
    return {name: storage.presign_get(storage.viewer, r.viewer_prefix + obj)
            for name, obj in (("lod0", "lod0.glb"), ("lod1", "lod1.glb"), ("meta", "analytical_meta.json"))}


async def load_manifest(storage: Storage, redis: Redis, key: str) -> dict[str, Any]:
    cache_key = f"rg:manifest:{sha256_hex(key)}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)
    data = await storage.get_decoded(storage.viewer, key)
    await redis.set(cache_key, data.decode("utf-8"), ex=3600)
    return json.loads(data)


async def diff_urls(storage: Storage, redis: Redis, d: DiffRun, base: Revision, target: Revision) -> dict[str, Any]:
    out: dict[str, Any] = {"base": revision_urls(storage, base), "target": revision_urls(storage, target)}
    if d.manifest_r2_key:
        prefix = d.manifest_r2_key.rsplit("/", 1)[0] + "/"
        out["manifest"] = storage.presign_get(storage.viewer, d.manifest_r2_key)
        manifest = await load_manifest(storage, redis, d.manifest_r2_key)
        for k, v in manifest.get("artifacts", {}).items():
            out[k] = storage.presign_get(storage.viewer, prefix + v) if v else None
    return out
