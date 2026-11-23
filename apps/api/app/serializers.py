"""JSON views of the ORM rows + signed-URL bundles."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from .models import DiffItem, DiffRun, Part, PinComment, Revision, ReviewLink, ShopInbox, User, Workspace
from .security import mask_contact, sha256_hex
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


def revision(r: Revision) -> dict[str, Any]:
    return {"id": str(r.id), "part_id": str(r.part_id), "rev_label": r.rev_label, "file_sha256": r.file_sha256,
            "file_size_bytes": r.file_size_bytes, "status": r.status, "error_code": r.error_code,
            "raw_available": r.raw_r2_key is not None, "raw_purged_at": iso(r.raw_purged_at),
            "viewer_prefix": r.viewer_prefix, "spec_card_json": r.spec_card_json, "created_at": iso(r.created_at)}


def diff_run(d: DiffRun) -> dict[str, Any]:
    return {"id": str(d.id), "part_id": str(d.part_id), "base_rev_id": str(d.base_rev_id), "target_rev_id": str(d.target_rev_id),
            "align_method": d.align_method, "transform_4x4": list(d.transform_4x4), "align_confidence": d.align_confidence,
            "status": d.status, "error_code": d.error_code, "created_at": iso(d.created_at), "updated_at": iso(d.updated_at)}


def item(i: DiffItem) -> dict[str, Any]:
    return {"id": str(i.id), "diff_run_id": str(i.diff_run_id), "change_key": i.change_key, "category": i.category,
            "severity": i.severity, "title": i.title, "fact": i.fact_json, "hint_text": i.hint_text,
            "anchor_xyz": list(i.anchor_xyz), "checked": i.checked_at is not None, "checked_at": iso(i.checked_at),
            "checked_by_contact": i.checked_by_contact}


def link(l: ReviewLink) -> dict[str, Any]:
    return {"id": str(l.id), "diff_run_id": str(l.diff_run_id), "recipient_name": l.recipient_name,
            "masked_contact": mask_contact(l.recipient_contact), "allow_raw_download": l.allow_raw_download,
            "status": l.status, "expires_at": iso(l.expires_at), "approved_at": iso(l.approved_at),
            "created_at": iso(l.created_at)}


def pin(p: PinComment) -> dict[str, Any]:
    return {"id": str(p.id), "diff_run_id": str(p.diff_run_id), "diff_item_id": str(p.diff_item_id) if p.diff_item_id else None,
            "review_link_id": str(p.review_link_id) if p.review_link_id else None, "author_type": p.author_type,
            "author_label": p.author_label, "face_id": p.face_id, "rev_side": p.rev_side, "pin_xyz": list(p.pin_xyz),
            "body": p.body, "resolved": p.resolved_at is not None, "resolved_at": iso(p.resolved_at), "created_at": iso(p.created_at)}


def inbox(i: ShopInbox) -> dict[str, Any]:
    return {"id": str(i.id), "workspace_id": str(i.workspace_id), "slug": i.slug, "shop_name": i.shop_name,
            "owner_contact": i.owner_contact, "is_active": i.is_active, "created_at": iso(i.created_at)}


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
