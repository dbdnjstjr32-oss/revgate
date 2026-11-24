"""Phase 4 §2.2 — supersede approvals that no longer describe what will be manufactured.

review_links.status stays 'APPROVED' forever (historical record); superseded_at / superseded_reason /
superseded_by_revision_id mark the approval as stale and the API reports effective_status = SUPERSEDED.

Triggers: NEW_REVISION (CAD worker, a newer revision of the part became READY), SPEC_UPDATED
(PATCH /v1/revisions/{id}/spec on the approved target), DRAWING_UPDATED (2D attachment of the approved
target added or deleted).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .config import Settings
from .deps import utcnow
from .models import DiffRun, Part, ReviewLink, Revision

REASONS = ("NEW_REVISION", "SPEC_UPDATED", "DRAWING_UPDATED")


def effective_status(link: ReviewLink) -> str:
    return "SUPERSEDED" if link.superseded_at is not None else link.status


async def supersede_target_links(db: AsyncSession, settings: Settings, *, target: Revision, part: Part, reason: str,
                                 actor: str, request: Request | None, extra: dict[str, Any] | None = None) -> int:
    """Mark every live APPROVED link whose diff run targets `target` (same transaction as the change)."""
    assert reason in REASONS
    stmt = (
        update(ReviewLink)
        .where(ReviewLink.status == "APPROVED", ReviewLink.superseded_at.is_(None),
               ReviewLink.diff_run_id.in_(select(DiffRun.id).where(DiffRun.target_rev_id == target.id)))
        .values(superseded_at=utcnow(), superseded_reason=reason, superseded_by_revision_id=target.id)
        .returning(ReviewLink.id)
        .execution_options(synchronize_session=False)
    )
    ids = (await db.execute(stmt)).scalars().all()
    for link_id in ids:
        audit.record(db, settings, workspace_id=part.workspace_id, entity_type="review_link", entity_id=link_id, actor=actor,
                     action="LINK_SUPERSEDED", payload={"reason": reason, "revision_id": str(target.id), **(extra or {})},
                     request=request)
    return len(ids)


async def latest_revision_label(db: AsyncSession, part_id) -> str | None:
    """Label of the newest READY revision of the part (shown next to stale approvals)."""
    return await db.scalar(select(Revision.rev_label).where(Revision.part_id == part_id, Revision.status == "READY")
                           .order_by(Revision.created_at.desc()).limit(1))
