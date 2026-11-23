"""E2E: ZERO_RETENTION purge (q-purge, RAW_PURGED) + hash-verified re-drop, and the Shop Inbox flow."""

from __future__ import annotations

import hashlib
import time

import httpx
import pytest
from botocore.exceptions import ClientError

from .conftest import H, golden, login, make_part, outbox, presign_and_put, q, sse_events, unique, upload_revision, wait_diff

pytestmark = pytest.mark.e2e


def _wait_purged(db, rev_ids: list[str], timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = q(db, "SELECT count(*) FROM revisions WHERE id = ANY(%s::uuid[]) AND raw_r2_key IS NULL AND raw_purged_at IS NOT NULL",
                 rev_ids)
        if rows[0][0] == len(rev_ids):
            return
        time.sleep(0.5)
    raise AssertionError(f"raw STEP of {rev_ids} not purged within {timeout}s")


def _gone(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket="revgate-raw-cad", Key=key)
        return False
    except ClientError as exc:
        return exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")


def test_zero_retention_purge_and_hash_verified_redrop(http, redis, db, s3):
    s = login(http, redis)
    # The 11-table API has no workspace-settings endpoint yet: switch the mode as an operator would.
    q(db, "UPDATE workspaces SET security_mode = 'ZERO_RETENTION' WHERE id = %s", s["workspace"]["id"])
    part = make_part(http, s)
    path_a, path_b = golden("01_hole_diam", "A"), golden("01_hole_diam", "B")
    a = upload_revision(http, s, part["id"], path_a, "A")
    b = upload_revision(http, s, part["id"], path_b, "B")
    keys = dict(q(db, "SELECT id::text, raw_r2_key FROM revisions WHERE id = ANY(%s::uuid[])", [a, b]))
    diff_id = http.post(f"/v1/parts/{part['id']}/diffs", headers=H(s), json={"base_rev_id": a, "target_rev_id": b}).json()["diff_run_id"]
    assert sse_events(s, diff_id)[-1][0] == "ready"

    # q-purge: raw STEP destroyed right after processing, purge recorded, derived viewer data kept
    _wait_purged(db, [a, b])
    assert all(_gone(s3, k) for k in keys.values())
    purges = q(db, "SELECT entity_id::text, actor_contact FROM audit_events WHERE action = 'RAW_PURGED' AND entity_id = ANY(%s::uuid[])", [a, b])
    assert sorted(p[0] for p in purges) == sorted([a, b]) and {p[1] for p in purges} == {"system:q-purge"}
    rev = http.get(f"/v1/revisions/{a}", headers=H(s)).json()
    assert rev["revision"]["raw_available"] is False and rev["revision"]["raw_purged_at"] and rev["signed_urls"]["lod0"]
    assert wait_diff(http, s, diff_id)["items"]  # the diff result survives the purge

    # new comparison needs the originals back
    blocked = http.post(f"/v1/parts/{part['id']}/diffs", headers=H(s), json={"base_rev_id": a, "target_rev_id": b})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "RAW_PURGED_REDROP_REQUIRED"

    # re-drop with a tampered file of identical size -> SHA-256 mismatch, object discarded
    data_a = path_a.read_bytes()
    tampered = data_a.replace(b"revgate-01_hole_diam-rev_A", b"revgate-01_hole_diam-rev_X", 1)
    assert len(tampered) == len(data_a) and tampered != data_a
    up = presign_and_put(http, s, part["id"], tampered, "A", sha=hashlib.sha256(data_a).hexdigest())
    assert up["redrop"] is True and up["revision_id"] == a
    r = http.post(f"/v1/revisions/{a}/complete", headers=H(s))
    assert r.status_code == 409 and r.json()["error"]["code"] == "SHA256_MISMATCH"
    assert _gone(s3, up["r2_key"])
    assert http.get(f"/v1/revisions/{a}", headers=H(s)).json()["revision"]["raw_available"] is False

    # re-drop the genuine originals -> accepted -> immediate recompute -> purged again
    for rid, path, label in ((a, path_a, "A"), (b, path_b, "B")):
        up = presign_and_put(http, s, part["id"], path.read_bytes(), label)
        assert up["redrop"] is True and up["revision_id"] == rid
        r = http.post(f"/v1/revisions/{rid}/complete", headers=H(s))
        assert r.status_code == 200 and r.json()["redropped"] is True
    diff2 = http.post(f"/v1/parts/{part['id']}/diffs", headers=H(s), json={"base_rev_id": a, "target_rev_id": b})
    assert diff2.status_code == 200, diff2.text
    assert sse_events(s, diff2.json()["diff_run_id"])[-1][0] == "ready"
    _wait_purged(db, [a, b])
    actions = [r[0] for r in q(db, "SELECT action FROM audit_events WHERE entity_id = %s ORDER BY id", a)]
    assert actions.count("RAW_PURGED") == 2 and "REDROP_REJECTED" in actions and "RAW_REDROPPED" in actions


def test_shop_inbox_submission(http, redis, db):
    owner = login(http, redis)
    slug = unique("e2e-shop").lower()
    r = http.post("/v1/shop-inboxes", headers=H(owner), json={"slug": slug, "shop_name": "OO정밀", "owner_contact": owner["email"]})
    assert r.status_code == 200, r.text
    assert r.json()["public_url"].endswith(f"/shop/{slug}")
    inbox_id = r.json()["inbox"]["id"]
    assert http.post("/v1/shop-inboxes", headers=H(owner), json={"slug": slug, "shop_name": "dup", "owner_contact": "x@y.z"}).status_code == 409
    assert http.get(f"/v1/public/shop/{slug}").json() == {"shop_name": "OO정밀", "is_active": True}

    sender = f"{unique('client')}@startup.test"
    files = []
    blobs = []
    for side in ("A", "B"):
        data = golden("01_hole_diam", side).read_bytes()
        blobs.append(data)
        files.append({"filename": f"bracket_{side}.step", "file_size_bytes": len(data),
                      "file_sha256": hashlib.sha256(data).hexdigest(), "rev_label": f"rev{side}"})
    pre = http.post(f"/v1/public/shop/{slug}/submissions/presign",
                    json={"sender_name": "로봇스타트업 김설계", "sender_contact": sender, "part_number": "BRK-001", "files": files})
    assert pre.status_code == 200, pre.text
    sub = pre.json()
    assert len(sub["uploads"]) == 2
    for up, data in zip(sub["uploads"], blobs):
        assert httpx.put(up["put_url"], content=data, headers={"Content-Type": "application/octet-stream"}).status_code == 200

    done = http.post(f"/v1/public/shop/{slug}/submissions/complete", json={"submission_id": sub["submission_id"],
                                                                         "spec_card_json": {"material": "SUS304"}})
    assert done.status_code == 200, done.text
    res = done.json()
    assert res["status"] == "QUEUED" and len(res["revision_ids"]) == 2
    again = http.post(f"/v1/public/shop/{slug}/submissions/complete", json={"submission_id": sub["submission_id"]})
    assert again.status_code == 404  # single use

    row = q(db, "SELECT workspace_id::text, shop_inbox_id::text, part_number FROM parts WHERE id = %s", res["part_id"])[0]
    assert row == (owner["workspace"]["id"], inbox_id, "BRK-001")
    spec = q(db, "SELECT spec_card_json FROM revisions WHERE id = %s", res["revision_ids"][0])[0][0]
    assert spec["submitted_by"]["contact"] == sender and spec["material"] == "SUS304"

    final = wait_diff(http, owner, res["diff_run_id"])
    assert final["diff_run"]["status"] == "READY"
    assert [i["category"] for i in final["items"]] == ["HOLE_DIAMETER", "HOLE_DIAMETER"]
    note = outbox(redis, owner["email"], "INBOX_SUBMISSION")
    assert note["data"]["diff_run_id"] == res["diff_run_id"]
    assert q(db, "SELECT actor_contact FROM audit_events WHERE action = 'SUBMISSION_COMPLETED' AND entity_id = %s", inbox_id) == [(sender,)]

    # tampered submission is rejected at complete
    pre2 = http.post(f"/v1/public/shop/{slug}/submissions/presign",
                     json={"sender_name": "x", "sender_contact": sender, "part_number": "BRK-002", "files": files}).json()
    bad = bytearray(blobs[0])
    bad[-20] ^= 1
    httpx.put(pre2["uploads"][0]["put_url"], content=bytes(bad), headers={"Content-Type": "application/octet-stream"})
    httpx.put(pre2["uploads"][1]["put_url"], content=blobs[1], headers={"Content-Type": "application/octet-stream"})
    r = http.post(f"/v1/public/shop/{slug}/submissions/complete", json={"submission_id": pre2["submission_id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SHA256_MISMATCH"
    assert http.get("/v1/public/shop/does-not-exist").status_code == 404
