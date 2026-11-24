"""Append-only audit trail (audit_events). Only INSERT is possible for the app role."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import AuditEvent
from .security import canonical_sha256, ip_hash


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def record(db: AsyncSession, settings: Settings, *, workspace_id, entity_type: str, entity_id, actor: str, action: str,
           payload: dict[str, Any] | None = None, request: Request | None = None,
           payload_sha256: str | None = None) -> AuditEvent:
    """payload_sha256 overrides the hash of `payload` (e.g. APPROVED records the approval snapshot SHA-256 itself)."""
    ev = AuditEvent(
        workspace_id=workspace_id, entity_type=entity_type, entity_id=entity_id, actor_contact=actor, action=action,
        ip_hash=ip_hash(settings, client_ip(request)),
        payload_sha256=payload_sha256 or (canonical_sha256(payload) if payload is not None else None),
    )
    db.add(ev)
    return ev
