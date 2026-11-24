"""PATCH /v1/workspace/settings (OWNER only) + Phase 3 read models + dev outbox helper."""

from __future__ import annotations

import hashlib
import time
import uuid

import pytest

from .conftest import H, golden, login, make_part, q, upload_revision, wait_revision

pytestmark = pytest.mark.e2e


def test_owner_updates_security_settings(http, redis, db):
    s = login(http, redis)
    r = http.patch("/v1/workspace/settings", headers=H(s), json={"retention_days": 60})
    assert r.status_code == 200, r.text
    assert r.json()["workspace"]["retention_days"] == 60 and r.json()["workspace"]["security_mode"] == "TTL_VAULT"
    assert http.get("/v1/auth/me", headers=H(s)).json()["workspace"]["retention_days"] == 60
    for bad in ({}, {"retention_days": 10}, {"retention_days": 365}, {"security_mode": "NONE"}):
        assert http.patch("/v1/workspace/settings", headers=H(s), json=bad).status_code == 422, bad
    rows = q(db, "SELECT action, actor_contact FROM audit_events WHERE entity_id = %s AND action = 'WORKSPACE_SETTINGS_CHANGED'",
             s["workspace"]["id"])
    assert rows == [("WORKSPACE_SETTINGS_CHANGED", s["email"])]


def test_non_owner_is_rejected(http, redis, db):
    owner = login(http, redis)
    # No invite flow yet: add an ENGINEER to the owner's workspace and give them a session directly.
    eng_id, token = uuid.uuid4(), f"e2e-eng-{uuid.uuid4().hex}"
    q(db, "INSERT INTO users (id, workspace_id, email, name, role) VALUES (%s, %s, %s, 'eng', 'ENGINEER')",
      eng_id, owner["workspace"]["id"], f"eng-{eng_id.hex[:8]}@e2e.revgate.test")
    redis.set(f"rg:sess:{hashlib.sha256(token.encode()).hexdigest()}", str(eng_id), ex=600)
    r = http.patch("/v1/workspace/settings", headers={"Cookie": f"rg_sid={token}"}, json={"security_mode": "ZERO_RETENTION"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "OWNER_ONLY"
    assert http.get("/v1/auth/me", headers=H(owner)).json()["workspace"]["security_mode"] == "TTL_VAULT"


def test_switching_to_zero_retention_purges_existing_raw(http, redis, db):
    s = login(http, redis)
    part = make_part(http, s)
    rid = upload_revision(http, s, part["id"], golden("02_pocket_offset", "A"), "A")
    assert wait_revision(http, s, rid)["revision"]["raw_available"] is True
    r = http.patch("/v1/workspace/settings", headers=H(s), json={"security_mode": "ZERO_RETENTION"})
    assert r.status_code == 200 and r.json()["purges_enqueued"] == 1
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and q(db, "SELECT raw_r2_key FROM revisions WHERE id = %s", rid)[0][0] is not None:
        time.sleep(0.3)
    assert q(db, "SELECT raw_r2_key IS NULL, raw_purged_at IS NOT NULL FROM revisions WHERE id = %s", rid) == [(True, True)]
    detail = http.get(f"/v1/parts/{part['id']}", headers=H(s)).json()
    assert [x["id"] for x in detail["revisions"]] == [rid] and detail["revisions"][0]["raw_available"] is False


def test_dev_outbox_helper(http, redis):
    s = login(http, redis)
    r = http.get("/v1/dev/outbox", params={"to": s["email"], "kind": "MAGIC_LINK"})
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert msgs and msgs[0]["data"]["token"] == s["token"]
    assert http.get("/v1/dev/outbox", params={"link_token": "nope"}).status_code == 404
