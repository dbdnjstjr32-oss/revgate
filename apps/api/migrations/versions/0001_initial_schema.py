"""Initial schema — the 11 tables of IMPLEMENTATION_BLUEPRINT §3, verbatim.

+ restricted application role (REVGATE app connects as it), and audit_events defence in depth:
  REVOKE UPDATE/DELETE/TRUNCATE from the app role AND a BEFORE UPDATE/DELETE/TRUNCATE trigger that
  rejects the statement for every role (append-only even for the schema owner).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-23
"""

from __future__ import annotations

import os
import re

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

# --------------------------------------------------------------------------------------------
# IMPLEMENTATION_BLUEPRINT §3 DDL (one statement per entry: asyncpg prepares statements singly)
# --------------------------------------------------------------------------------------------
DDL = [
    # 1. 워크스페이스 (보안/파기 정책 단위)
    """CREATE TABLE workspaces (
  id             UUID PRIMARY KEY,
  name           TEXT NOT NULL,
  plan           TEXT NOT NULL DEFAULT 'FREE' CHECK (plan IN ('FREE','PRO','TEAM','ENTERPRISE')),
  security_mode  TEXT NOT NULL DEFAULT 'TTL_VAULT' CHECK (security_mode IN ('TTL_VAULT','ZERO_RETENTION')),
  retention_days INT NOT NULL DEFAULT 30,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    # 2. 내부 사용자 (발주처 설계자 / 공장주)
    """CREATE TABLE users (
  id           UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  email        TEXT NOT NULL UNIQUE,
  name         TEXT NOT NULL,
  role         TEXT NOT NULL DEFAULT 'ENGINEER' CHECK (role IN ('OWNER','ENGINEER','VIEWER')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX idx_users_ws ON users(workspace_id)",
    # 3. 가공업체 무료 도면 접수함 (PLG 허브)
    """CREATE TABLE shop_inboxes (
  id             UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  slug           TEXT NOT NULL UNIQUE,
  shop_name      TEXT NOT NULL,
  owner_contact  TEXT NOT NULL,
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    # 4. 부품 (도번 단위)
    """CREATE TABLE parts (
  id               UUID PRIMARY KEY,
  workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  shop_inbox_id    UUID REFERENCES shop_inboxes(id) ON DELETE SET NULL,
  part_number      TEXT NOT NULL,
  name             TEXT NOT NULL,
  default_material TEXT,
  default_finish   TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX idx_parts_ws_num ON parts(workspace_id, part_number)",
    # 5. 리비전 (STEP 원본 + 파생 뷰어 번들)
    """CREATE TABLE revisions (
  id              UUID PRIMARY KEY,
  part_id         UUID NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
  uploaded_by     UUID REFERENCES users(id) ON DELETE SET NULL,
  rev_label       TEXT NOT NULL,
  file_sha256     TEXT NOT NULL,
  file_size_bytes BIGINT NOT NULL,
  raw_r2_key      TEXT,
  raw_purged_at   TIMESTAMPTZ,
  viewer_prefix   TEXT,
  spec_card_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
  status          TEXT NOT NULL DEFAULT 'PENDING_UPLOAD'
                  CHECK (status IN ('PENDING_UPLOAD','QUEUED','PARSING','READY','FAILED')),
  error_code      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX idx_revisions_part ON revisions(part_id, created_at DESC)",
    "CREATE INDEX idx_revisions_sha  ON revisions(part_id, file_sha256)",
    # 6. Diff 실행
    """CREATE TABLE diff_runs (
  id               UUID PRIMARY KEY,
  part_id          UUID NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
  base_rev_id      UUID NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
  target_rev_id    UUID NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
  align_method     TEXT NOT NULL DEFAULT 'IDENTITY' CHECK (align_method IN ('IDENTITY','AUTO_BREP_RANSAC','MANUAL_321')),
  transform_4x4    DOUBLE PRECISION[16] NOT NULL,
  align_confidence DOUBLE PRECISION,
  manifest_r2_key  TEXT,
  status           TEXT NOT NULL DEFAULT 'QUEUED'
                   CHECK (status IN ('QUEUED','COMPUTING','READY','NEEDS_ALIGN','FAILED')),
  error_code       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX idx_diff_runs_pair ON diff_runs(part_id, base_rev_id, target_rev_id)",
    # 7. 변경점 체크리스트 항목
    """CREATE TABLE diff_items (
  id                 UUID PRIMARY KEY,
  diff_run_id        UUID NOT NULL REFERENCES diff_runs(id) ON DELETE CASCADE,
  change_key         TEXT NOT NULL,
  category           TEXT NOT NULL,
  severity           TEXT NOT NULL CHECK (severity IN ('CRITICAL','MAJOR','INFO')),
  title              TEXT NOT NULL,
  fact_json          JSONB NOT NULL,
  hint_text          TEXT,
  anchor_xyz         DOUBLE PRECISION[3] NOT NULL,
  checked_at         TIMESTAMPTZ,
  checked_by_contact TEXT
)""",
    "CREATE INDEX idx_diff_items_run ON diff_items(diff_run_id)",
    # 8. 외부 공유 링크
    """CREATE TABLE review_links (
  id                  UUID PRIMARY KEY,
  diff_run_id         UUID NOT NULL REFERENCES diff_runs(id) ON DELETE CASCADE,
  created_by          UUID NOT NULL REFERENCES users(id),
  token_hash          TEXT NOT NULL UNIQUE,
  recipient_name      TEXT NOT NULL,
  recipient_contact   TEXT NOT NULL,
  allow_raw_download  BOOLEAN NOT NULL DEFAULT FALSE,
  status              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','APPROVED','REVOKED','EXPIRED')),
  expires_at          TIMESTAMPTZ NOT NULL,
  approved_at         TIMESTAMPTZ,
  approval_pdf_r2_key TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX idx_review_links_run ON review_links(diff_run_id)",
    # 9. 외부 비로그인 6자리 OTP & 쿠키 세션
    """CREATE TABLE otp_sessions (
  id                 UUID PRIMARY KEY,
  review_link_id     UUID NOT NULL REFERENCES review_links(id) ON DELETE CASCADE,
  contact            TEXT NOT NULL,
  otp_hash           TEXT NOT NULL,
  attempts           INT NOT NULL DEFAULT 0,
  otp_expires_at     TIMESTAMPTZ NOT NULL,
  session_token_hash TEXT UNIQUE,
  verified_at        TIMESTAMPTZ,
  session_expires_at TIMESTAMPTZ
)""",
    "CREATE INDEX idx_otp_link ON otp_sessions(review_link_id, contact)",
    # 10. 3D 면 고정 핀 코멘트
    """CREATE TABLE pin_comments (
  id             UUID PRIMARY KEY,
  diff_run_id    UUID NOT NULL REFERENCES diff_runs(id) ON DELETE CASCADE,
  diff_item_id   UUID REFERENCES diff_items(id) ON DELETE SET NULL,
  review_link_id UUID REFERENCES review_links(id) ON DELETE SET NULL,
  author_type    TEXT NOT NULL CHECK (author_type IN ('INTERNAL','EXTERNAL')),
  author_label   TEXT NOT NULL,
  face_id        INT NOT NULL,
  rev_side       TEXT NOT NULL CHECK (rev_side IN ('BASE','TARGET')),
  pin_xyz        DOUBLE PRECISION[3] NOT NULL,
  body           TEXT NOT NULL,
  resolved_at    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX idx_pins_run ON pin_comments(diff_run_id, created_at)",
    # 11. 불변 감사 로그 (App DB Role에서 UPDATE/DELETE 권한 REVOKE)
    """CREATE TABLE audit_events (
  id             BIGSERIAL PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspaces(id),
  entity_type    TEXT NOT NULL,
  entity_id      UUID NOT NULL,
  actor_contact  TEXT NOT NULL,
  action         TEXT NOT NULL,
  ip_hash        TEXT,
  payload_sha256 TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX idx_audit_ws ON audit_events(workspace_id, created_at DESC)",
]

TABLES = [
    "workspaces", "users", "shop_inboxes", "parts", "revisions", "diff_runs",
    "diff_items", "review_links", "otp_sessions", "pin_comments", "audit_events",
]

APPEND_ONLY = [
    """CREATE FUNCTION audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
END
$fn$""",
    """CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()""",
    """CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()""",
]


def _role() -> tuple[str, str]:
    role = os.environ.get("APP_DB_ROLE", "revgate_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError(f"invalid APP_DB_ROLE {role!r}")
    password = os.environ.get("APP_DB_PASSWORD", "revgate_app_dev")
    return role, password.replace("'", "''")


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    for stmt in APPEND_ONLY:
        op.execute(stmt)

    role, password = _role()
    op.execute(
        f"""DO $do$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END $do$"""
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    rw = [t for t in TABLES if t != "audit_events"]
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(rw)} TO {role}")
    op.execute(f"GRANT SELECT, INSERT ON audit_events TO {role}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE audit_events_id_seq TO {role}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM {role}")


def downgrade() -> None:
    role, _ = _role()
    op.execute(
        f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}; "
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}; END IF; END $do$"
    )
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
