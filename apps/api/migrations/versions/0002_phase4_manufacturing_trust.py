"""Phase 4 — manufacturing trust (docs/PHASE4_MANUFACTURING_SPEC.md §1), verbatim.

* revisions: spec_version / spec_updated_at (SpecCardV2 edit tracking)
* review_links: immutable approval snapshot (+ canonical SHA-256), supersede tracking, DFM acknowledgement
* 12th table revision_attachments (2D PDF / DXF / spec sheet per revision) + grants for the app role

Revision ID: 0002_phase4_manufacturing_trust
Revises: 0001_initial_schema
Create Date: 2026-11-24
"""

from __future__ import annotations

import os
import re

from alembic import op

revision = "0002_phase4_manufacturing_trust"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

# one statement per entry (asyncpg prepares statements singly)
DDL = [
    # 1. revisions: 사양 수정 버전 추적
    """ALTER TABLE revisions
  ADD COLUMN spec_version INT NOT NULL DEFAULT 1,
  ADD COLUMN spec_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()""",
    # 2. review_links: 불변 승인 스냅샷 + 구승인(Supersede) 추적 + DFM 확인
    """ALTER TABLE review_links
  ADD COLUMN approval_snapshot_json JSONB,
  ADD COLUMN approval_snapshot_sha256 TEXT,
  ADD COLUMN superseded_at TIMESTAMPTZ,
  ADD COLUMN superseded_reason TEXT
    CHECK (superseded_reason IN ('NEW_REVISION','SPEC_UPDATED','DRAWING_UPDATED')),
  ADD COLUMN superseded_by_revision_id UUID REFERENCES revisions(id) ON DELETE SET NULL,
  ADD COLUMN dfm_ack_json JSONB NOT NULL DEFAULT '{}'::jsonb""",
    # 3. 12번째 테이블: 리비전별 2D 도면 첨부 (PDF / DXF / SPEC_SHEET)
    """CREATE TABLE revision_attachments (
  id              UUID PRIMARY KEY,
  revision_id     UUID NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN ('2D_PDF','2D_DXF','SPEC_SHEET')),
  filename        TEXT NOT NULL,
  content_type    TEXT NOT NULL,
  file_size_bytes BIGINT NOT NULL,
  file_sha256     TEXT NOT NULL,
  r2_key          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'PENDING_UPLOAD'
                  CHECK (status IN ('PENDING_UPLOAD','READY','DELETED')),
  uploaded_by     UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX idx_rev_att_rev ON revision_attachments(revision_id, status)",
]


def _role() -> str:
    role = os.environ.get("APP_DB_ROLE", "revgate_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"invalid APP_DB_ROLE {role!r}")
    return role


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    role = _role()
    # the role is created by 0001; guard anyway so a manual DB without it still migrates
    op.execute(
        f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON revision_attachments TO {role}; END IF; END $do$"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS revision_attachments")
    op.execute(
        """ALTER TABLE review_links
  DROP COLUMN IF EXISTS dfm_ack_json,
  DROP COLUMN IF EXISTS superseded_by_revision_id,
  DROP COLUMN IF EXISTS superseded_reason,
  DROP COLUMN IF EXISTS superseded_at,
  DROP COLUMN IF EXISTS approval_snapshot_sha256,
  DROP COLUMN IF EXISTS approval_snapshot_json"""
    )
    op.execute("ALTER TABLE revisions DROP COLUMN IF EXISTS spec_updated_at, DROP COLUMN IF EXISTS spec_version")
