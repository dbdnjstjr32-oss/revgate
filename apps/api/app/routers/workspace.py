"""Workspace settings (Phase 3 addition): PATCH /v1/workspace/settings — OWNER only.

Switching to ZERO_RETENTION enqueues q-purge for every finished revision that still holds its raw
STEP (the purge task itself defers while a diff run still needs the file).
"""

from __future__ import annotations

from arq import ArqRedis
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from .. import serializers as S
from ..config import Settings
from ..deps import CurrentUser, current_user, get_arq, get_db, get_settings_dep
from ..errors import ApiError
from ..models import Part, Revision
from ..queue import enqueue_purge
from ..schemas import WorkspaceSettingsIn

router = APIRouter(prefix="/v1/workspace", tags=["workspace"])


@router.patch("/settings")
async def update_settings(body: WorkspaceSettingsIn, request: Request, cu: CurrentUser = Depends(current_user),
                          db: AsyncSession = Depends(get_db), arq: ArqRedis = Depends(get_arq),
                          settings: Settings = Depends(get_settings_dep)):
    if cu.user.role != "OWNER":
        raise ApiError(403, "OWNER_ONLY", "only the workspace OWNER can change security settings")
    ws = cu.workspace
    before = {"security_mode": ws.security_mode, "retention_days": ws.retention_days}
    if body.security_mode is not None:
        ws.security_mode = body.security_mode
    if body.retention_days is not None:
        ws.retention_days = body.retention_days
    after = {"security_mode": ws.security_mode, "retention_days": ws.retention_days}
    audit.record(db, settings, workspace_id=ws.id, entity_type="workspace", entity_id=ws.id, actor=cu.user.email,
                 action="WORKSPACE_SETTINGS_CHANGED", payload={"before": before, "after": after}, request=request)
    to_purge = []
    if before["security_mode"] != "ZERO_RETENTION" and ws.security_mode == "ZERO_RETENTION":
        to_purge = (await db.execute(
            select(Revision.id).join(Part, Part.id == Revision.part_id)
            .where(Part.workspace_id == ws.id, Revision.raw_r2_key.is_not(None), Revision.status.in_(("READY", "FAILED")))
        )).scalars().all()
    await db.commit()
    for rid in to_purge:
        await enqueue_purge(arq, rid, "ZERO_RETENTION")
    return {"workspace": S.workspace(ws), "purges_enqueued": len(to_purge)}
