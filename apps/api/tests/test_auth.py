"""[1] Internal auth: magic link -> rg_sid (HttpOnly + Secure + SameSite=Lax, 7 d)."""

from __future__ import annotations

from .conftest import H, login, q


def test_magic_link_session_lifecycle(http, redis, db):
    s = login(http, redis)
    assert s["cookie_attrs"] == {"max-age": "604800", "path": "/", "samesite": "lax", "httponly": True, "secure": True}
    assert s["user"]["role"] == "OWNER" and s["workspace"]["security_mode"] == "TTL_VAULT"

    me = http.get("/v1/auth/me", headers=H(s))
    assert me.status_code == 200 and me.json()["user"]["email"] == s["email"]

    # magic links are single use; only the SHA-256 of tokens is stored
    assert http.post("/v1/auth/verify", json={"token": s["token"]}).status_code == 401
    assert redis.get(f"rg:magic:{s['token']}") is None

    actions = [r[0] for r in q(db, "SELECT action FROM audit_events WHERE workspace_id = %s ORDER BY id", s["workspace"]["id"])]
    assert actions[:2] == ["USER_SIGNED_UP", "LOGIN"]

    out = http.post("/v1/auth/logout", headers=H(s))
    assert out.status_code == 200
    assert any("rg_sid=" in c and "Max-Age=0" in c for c in out.headers.get_list("set-cookie"))
    assert http.get("/v1/auth/me", headers=H(s)).status_code == 401


def test_internal_endpoints_require_session(http):
    for method, path in (("GET", "/v1/parts"), ("POST", "/v1/parts"), ("GET", "/v1/auth/me"),
                         ("POST", "/v1/shop-inboxes"), ("GET", "/v1/diffs/00000000-0000-7000-8000-000000000000")):
        r = http.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_workspaces_are_isolated(http, redis):
    a, b = login(http, redis), login(http, redis)
    part = http.post("/v1/parts", headers=H(a), json={"part_number": "ISO-1", "name": "iso"}).json()["part"]
    assert http.get("/v1/parts", headers=H(b)).json()["items"] == []
    r = http.post(f"/v1/parts/{part['id']}/revisions/presign", headers=H(b),
                  json={"rev_label": "A", "filename": "x.step", "file_size_bytes": 10, "file_sha256": "0" * 64})
    assert r.status_code == 404
