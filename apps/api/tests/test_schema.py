"""Alembic migration + DDL contract (blueprint §3 + Phase 4 §1) against the real Postgres 16."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import errors as pgerr

from app.models import TABLES

from .conftest import APP_DB_DSN, DB_DSN, q

API_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = ["workspaces", "users", "shop_inboxes", "parts", "revisions", "diff_runs", "diff_items",
                   "review_links", "otp_sessions", "pin_comments", "audit_events",
                   "revision_attachments"]  # 12th table (Phase 4)
EXPECTED_INDEXES = ["idx_users_ws", "idx_parts_ws_num", "idx_revisions_part", "idx_revisions_sha", "idx_diff_runs_pair",
                    "idx_diff_items_run", "idx_review_links_run", "idx_otp_link", "idx_pins_run", "idx_audit_ws",
                    "idx_rev_att_rev"]


def test_alembic_head_applied(db):
    assert q(db, "SELECT version_num FROM alembic_version") == [("0002_phase4_manufacturing_trust",)]


def test_twelve_tables_with_orm_matching_columns(db):
    tables = [r[0] for r in q(db, "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                                  "AND table_name <> 'alembic_version' ORDER BY table_name")]
    assert sorted(tables) == sorted(EXPECTED_TABLES)
    for model in TABLES:
        cols = {r[0]: r[1] for r in q(db, "SELECT column_name, is_nullable FROM information_schema.columns "
                                          "WHERE table_schema='public' AND table_name=%s", model.__tablename__)}
        orm = {c.name: ("YES" if c.nullable else "NO") for c in model.__table__.columns}
        assert cols == orm, model.__tablename__


def test_check_constraints_and_indexes(db):
    checks = {r[0] for r in q(db, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE contype='c'")}
    for fragment in ("'FREE'", "'ZERO_RETENTION'", "'OWNER'", "'PENDING_UPLOAD'", "'MANUAL_321'", "'NEEDS_ALIGN'",
                     "'CRITICAL'", "'EXPIRED'", "'EXTERNAL'", "'TARGET'",
                     # Phase 4
                     "'NEW_REVISION'", "'DRAWING_UPDATED'", "'2D_DXF'", "'SPEC_SHEET'"):
        assert any(fragment in c for c in checks), fragment
    idx = {r[0] for r in q(db, "SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
    assert set(EXPECTED_INDEXES) <= idx
    types = dict(q(db, "SELECT column_name, udt_name FROM information_schema.columns WHERE table_name='diff_runs'"))
    assert types["transform_4x4"] == "_float8" and types["id"] == "uuid"
    assert dict(q(db, "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='audit_events'"))["id"] == "bigint"
    with pytest.raises(pgerr.CheckViolation):
        q(db, "INSERT INTO workspaces (id, name, plan) VALUES (%s, 'x', 'GOLD')", uuid.uuid4())
    # Phase 4 columns: spec_version / dfm_ack_json defaults, JSONB snapshot
    rev_cols = dict(q(db, "SELECT column_name, column_default FROM information_schema.columns WHERE table_name='revisions'"))
    assert rev_cols["spec_version"] == "1" and "now()" in rev_cols["spec_updated_at"]
    link_types = dict(q(db, "SELECT column_name, udt_name FROM information_schema.columns WHERE table_name='review_links'"))
    assert link_types["approval_snapshot_json"] == "jsonb" and link_types["dfm_ack_json"] == "jsonb"
    assert link_types["superseded_by_revision_id"] == "uuid"


def test_audit_events_are_append_only(db):
    ws = uuid.uuid4()
    q(db, "INSERT INTO workspaces (id, name) VALUES (%s, 'audit-probe')", ws)
    with psycopg.connect(APP_DB_DSN, autocommit=True) as app:
        app.execute("INSERT INTO audit_events (workspace_id, entity_type, entity_id, actor_contact, action) "
                    "VALUES (%s, 'probe', %s, 'e2e', 'PROBE')", (ws, ws))
        for stmt in ("UPDATE audit_events SET action = 'TAMPERED' WHERE workspace_id = %s",
                     "DELETE FROM audit_events WHERE workspace_id = %s"):
            with pytest.raises(pgerr.InsufficientPrivilege):  # REVOKEd from the app role
                app.execute(stmt, (ws,))
        with pytest.raises(pgerr.InsufficientPrivilege):
            app.execute("TRUNCATE audit_events")
    # even the schema owner is stopped by the trigger
    with pytest.raises(pgerr.InsufficientPrivilege, match="append-only"):
        q(db, "UPDATE audit_events SET action = 'TAMPERED' WHERE workspace_id = %s", ws)
    with pytest.raises(pgerr.InsufficientPrivilege, match="append-only"):
        q(db, "DELETE FROM audit_events WHERE workspace_id = %s", ws)
    assert q(db, "SELECT action FROM audit_events WHERE workspace_id = %s", ws) == [("PROBE",)]


def test_app_role_is_restricted(db):
    rows = q(db, "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'revgate_app'")
    assert rows == [(False, False, False)]
    priv = dict(q(db, "SELECT privilege_type, 1 FROM information_schema.role_table_grants "
                      "WHERE grantee = 'revgate_app' AND table_name = 'audit_events'"))
    assert set(priv) == {"SELECT", "INSERT"}
    att = {r[0] for r in q(db, "SELECT privilege_type FROM information_schema.role_table_grants "
                               "WHERE grantee = 'revgate_app' AND table_name = 'revision_attachments'")}
    assert att == {"SELECT", "INSERT", "UPDATE", "DELETE"}


def test_migration_round_trip_on_scratch_database(db):
    name = f"revgate_mig_{uuid.uuid4().hex[:8]}"
    q(db, f'CREATE DATABASE "{name}"')
    try:
        url = os.environ["MIGRATION_DATABASE_URL"].rsplit("/", 1)[0] + f"/{name}"
        env = {**os.environ, "MIGRATION_DATABASE_URL": url}
        for args in (["upgrade", "head"], ["downgrade", "base"], ["upgrade", "head"]):
            res = subprocess.run(["alembic", *args], cwd=API_ROOT, env=env, capture_output=True, text=True, timeout=120)
            assert res.returncode == 0, res.stderr[-2000:]
            dsn = DB_DSN.rsplit("/", 1)[0] + f"/{name}"
            with psycopg.connect(dsn, autocommit=True) as c:
                n = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                              "AND table_name <> 'alembic_version'").fetchone()[0]
            assert n == (0 if args[0] == "downgrade" else 12), args
    finally:
        q(db, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
