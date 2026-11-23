"""E2E: presign PUT -> complete -> q-ingest -> q-diff-batch -> SSE -> diff_items + Brotli signed URLs,
and NEEDS_ALIGN -> 3-2-1 realign (q-diff-interactive) -> READY."""

from __future__ import annotations

import json
import struct

import brotli
import httpx
import jsonschema
import pytest
from revgate_contracts import load_schema

from .conftest import H, golden, login, make_part, q, sse_events, upload_revision, wait_diff, wait_revision

pytestmark = pytest.mark.e2e
IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _glb_json(data: bytes) -> dict:
    magic, version, _length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and version == 2
    json_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    assert chunk_type == b"JSON"
    return json.loads(data[20:20 + json_len])


def _fetch_br(url: str) -> bytes:
    """Download a signed URL; assert it is stored/served as Brotli and return the decoded bytes."""
    with httpx.stream("GET", url, timeout=60) as r:
        assert r.status_code == 200, url
        assert r.headers.get("content-encoding") == "br"
        raw = b"".join(r.iter_raw())
    decoded = brotli.decompress(raw)
    assert len(raw) < len(decoded)
    assert httpx.get(url, timeout=60).content == decoded  # transparent decoding for HTTP clients
    return decoded


def test_hole_diam_full_pipeline(http, db, hole_diff):
    s, diff_id = hole_diff["session"], hole_diff["diff_id"]
    events = hole_diff["events"]
    names = [e for e, _ in events]
    assert names[-1] == "ready", events
    assert "progress" in names
    stages = [d.get("stage") for e, d in events if e == "progress"]
    assert {"COMPUTING"} <= set(stages) or events[0][1].get("snapshot"), stages
    ready = events[-1][1]
    assert ready["status"] == "READY" and ready["item_count"] == 2
    assert ready["alignment"]["method"] == "IDENTITY"

    for rid in (hole_diff["base"], hole_diff["target"]):
        rev = wait_revision(http, s, rid)
        assert rev["revision"]["status"] == "READY"
        lod0 = _glb_json(_fetch_br(rev["signed_urls"]["lod0"]))
        assert "_FACE_ID" in lod0["meshes"][0]["primitives"][0]["attributes"]
        meta = json.loads(_fetch_br(rev["signed_urls"]["meta"]))
        jsonschema.validate(meta, load_schema("analytical_meta"))
        assert meta["revision_id"] == rid and meta["summary"]["face_count"] == 10

    body = http.get(f"/v1/diffs/{diff_id}", headers=H(s)).json()
    assert body["diff_run"]["status"] == "READY"
    items = body["items"]
    assert [i["category"] for i in items] == ["HOLE_DIAMETER", "HOLE_DIAMETER"]
    for i in items:
        assert i["fact"]["from_diameter_mm"] == 3.3 and i["fact"]["to_diameter_mm"] == 4.2
        assert i["hint_text"] == "참고 추정: ISO M4 → M5 탭 기초홀 규격과 일치"
        assert i["severity"] == "MAJOR" and i["checked"] is False

    manifest = json.loads(_fetch_br(body["signed_urls"]["manifest"]))
    jsonschema.validate(manifest, load_schema("diff_manifest"))
    assert manifest["diff_run_id"] == diff_id
    assert sorted(c["change_id"] for c in manifest["changes"]) == sorted(i["change_key"] for i in items)
    assert body["signed_urls"]["delta_removed_glb"] and body["signed_urls"]["delta_added_glb"] is None
    _glb_json(_fetch_br(body["signed_urls"]["delta_removed_glb"]))

    rows = q(db, "SELECT category, severity, fact_json->>'to_diameter_mm', anchor_xyz FROM diff_items WHERE diff_run_id = %s", diff_id)
    assert len(rows) == 2 and all(r[0] == "HOLE_DIAMETER" and r[1] == "MAJOR" and r[2] == "4.2" and len(r[3]) == 3 for r in rows)
    run = q(db, "SELECT status, align_method, align_confidence, manifest_r2_key, error_code FROM diff_runs WHERE id = %s", diff_id)[0]
    assert run[0] == "READY" and run[1] == "IDENTITY" and run[2] > 0.9 and run[3].endswith("diff_manifest.json") and run[4] is None
    actions = {r[0] for r in q(db, "SELECT action FROM audit_events WHERE entity_id IN (%s, %s, %s)",
                                  diff_id, hole_diff["base"], hole_diff["target"])}
    assert {"REVISION_UPLOADED", "REVISION_READY", "DIFF_REQUESTED", "DIFF_READY"} <= actions
    assert hole_diff["elapsed"] < 60


def test_sse_late_subscriber_gets_terminal_snapshot(hole_diff):
    events = sse_events(hole_diff["session"], hole_diff["diff_id"])
    assert len(events) == 1 and events[0][0] == "ready" and events[0][1]["snapshot"] is True


def test_upload_guards(http, redis):
    s = login(http, redis)
    part = make_part(http, s)
    too_big = http.post(f"/v1/parts/{part['id']}/revisions/presign", headers=H(s), json={
        "rev_label": "A", "filename": "big.step", "file_size_bytes": 50 * 1024 * 1024 + 1, "file_sha256": "a" * 64})
    assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "FILE_TOO_LARGE"
    wrong_ext = http.post(f"/v1/parts/{part['id']}/revisions/presign", headers=H(s), json={
        "rev_label": "A", "filename": "part.stl", "file_size_bytes": 10, "file_sha256": "a" * 64})
    assert wrong_ext.status_code == 422
    # declared SHA-256 differs from the uploaded bytes -> rejected at complete, revision FAILED
    data = golden("01_hole_diam", "A").read_bytes()
    r = http.post(f"/v1/parts/{part['id']}/revisions/presign", headers=H(s), json={
        "rev_label": "A", "filename": "a.step", "file_size_bytes": len(data), "file_sha256": "b" * 64})
    put = httpx.put(r.json()["put_url"], content=data, headers={"Content-Type": "application/octet-stream"})
    assert put.status_code == 200
    done = http.post(f"/v1/revisions/{r.json()['revision_id']}/complete", headers=H(s))
    assert done.status_code == 409 and done.json()["error"]["code"] == "SHA256_MISMATCH"
    assert http.get(f"/v1/revisions/{r.json()['revision_id']}", headers=H(s)).json()["revision"]["status"] == "FAILED"
    # the signed PUT enforces the declared Content-Length
    r2 = http.post(f"/v1/parts/{part['id']}/revisions/presign", headers=H(s), json={
        "rev_label": "B", "filename": "b.step", "file_size_bytes": 1234, "file_sha256": "c" * 64})
    bad_put = httpx.put(r2.json()["put_url"], content=b"x" * 999, headers={"Content-Type": "application/octet-stream"})
    assert bad_put.status_code in (400, 403)


def test_needs_align_then_manual_realign(http, redis, db):
    s = login(http, redis)
    part = make_part(http, s)
    a = upload_revision(http, s, part["id"], golden("05_symmetric_needs_align", "A"), "A")
    b = upload_revision(http, s, part["id"], golden("05_symmetric_needs_align", "B"), "B")
    diff_id = http.post(f"/v1/parts/{part['id']}/diffs", headers=H(s), json={"base_rev_id": a, "target_rev_id": b}).json()["diff_run_id"]

    events = sse_events(s, diff_id)
    assert events[-1][0] == "needs_align", events
    assert events[-1][1]["alignment"]["requires_manual_review"] is True
    assert events[-1][1]["alignment"]["confidence"] < 0.5
    # policy: provisional manifest exists, but no diff_items while NEEDS_ALIGN
    assert q(db, "SELECT count(*) FROM diff_items WHERE diff_run_id = %s", diff_id) == [(0,)]
    body = http.get(f"/v1/diffs/{diff_id}", headers=H(s)).json()
    assert body["diff_run"]["status"] == "NEEDS_ALIGN" and body["items"] == [] and body["signed_urls"]["manifest"]
    assert http.post(f"/v1/diffs/{diff_id}/links", headers=H(s), json={
        "recipient_name": "x", "recipient_contact": "x@shop.test"}).json()["error"]["code"] == "DIFF_NOT_READY"

    bad = http.post(f"/v1/diffs/{diff_id}/realign", headers=H(s), json={"transform_4x4": [2.0] + IDENTITY[1:]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_TRANSFORM"

    r = http.post(f"/v1/diffs/{diff_id}/realign", headers=H(s), json={"transform_4x4": IDENTITY})
    assert r.status_code == 200 and r.json()["status"] == "COMPUTING"
    again = http.post(f"/v1/diffs/{diff_id}/realign", headers=H(s), json={"transform_4x4": IDENTITY})
    assert again.status_code == 409  # already COMPUTING

    events = sse_events(s, diff_id)
    assert events[-1][0] == "ready", events
    final = wait_diff(http, s, diff_id)
    assert final["diff_run"]["status"] == "READY" and final["diff_run"]["align_method"] == "MANUAL_321"
    cats = [i["category"] for i in final["items"]]
    assert cats == ["VOLUME_DELTA"] * 4
    assert all(i["fact"]["volume_mm3"] == pytest.approx(5760.0) for i in final["items"])
    assert q(db, "SELECT count(*) FROM diff_items WHERE diff_run_id = %s", diff_id) == [(4,)]
    assert q(db, "SELECT align_method FROM diff_runs WHERE id = %s", diff_id) == [("MANUAL_321",)]
    jobs = {r[0] for r in q(db, "SELECT action FROM audit_events WHERE entity_id = %s", diff_id)}
    assert {"DIFF_NEEDS_ALIGN", "DIFF_REALIGN_REQUESTED", "DIFF_READY"} <= jobs
