"""Phase 4 manufacturing context of one diff run (shared by GET /v1/diffs/{id}, link creation, the reviewer
bundle and the approval snapshot): normalized SpecCardV2 of both revisions, their READY 2D attachments,
the Base<->Target spec diff and the missing-spec warnings of the target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import serializers as S
from . import spec as SP
from .models import DiffItem, Part, Revision, RevisionAttachment
from .storage import Storage


@dataclass
class SpecContext:
    base_card: dict[str, Any]
    target_card: dict[str, Any]
    base_attachments: list[RevisionAttachment]
    target_attachments: list[RevisionAttachment]
    spec_diffs: list[dict[str, Any]]
    spec_warnings: list[dict[str, Any]]

    def attachments_json(self, storage: Storage) -> dict[str, list[dict[str, Any]]]:
        return {"base": S.attachment_list(storage, self.base_attachments),
                "target": S.attachment_list(storage, self.target_attachments)}

    @property
    def ack_required(self) -> list[str]:
        return [w["code"] for w in self.spec_warnings if w["level"] == "ACK_REQUIRED"]


async def spec_context(db: AsyncSession, part: Part, base: Revision, target: Revision, items: list[DiffItem]) -> SpecContext:
    atts = await S.ready_attachments(db, [base.id, target.id])
    base_card, target_card = S.spec_card(base, part), S.spec_card(target, part)
    target_atts = atts[target.id]
    return SpecContext(
        base_card=base_card, target_card=target_card, base_attachments=atts[base.id], target_attachments=target_atts,
        spec_diffs=SP.compute_spec_diff(base_card, target_card),
        spec_warnings=SP.missing_spec_warnings(target_card, target_has_pdf=any(a.kind == "2D_PDF" for a in target_atts),
                                               hints=[i.hint_text for i in items]),
    )
