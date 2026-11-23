"""E2E: share link -> 6-digit OTP (lockout + success cookie) -> bundle -> checks / pins -> approve ->
WeasyPrint PDF in the WORM audit bucket."""

from __future__ import annotations

import io

import httpx
import pytest
from pypdf import PdfReader

from .conftest import H, cookie_from, outbox, q, unique

pytestmark = pytest.mark.e2e


def _new_link(http, hole_diff, contact: str) -> tuple[str, str]:
    s, diff_id = hole_diff["session"], hole_diff["diff_id"]
    r = http.post(f"/v1/diffs/{diff_id}/links", headers=H(s), json={
        "recipient_name": "한빛정밀", "recipient_contact": contact, "expires_in_days": 7, "allow_raw_download": True})
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["share_url"].rsplit("/r/", 1)[1]
    return body["link_id"], token


def _otp(http, redis, token: str, contact: str) -> str:
    r = http.post(f"/v1/public/links/{token}/otp/request")
    assert r.status_code == 200, r.text
    assert r.json()["ttl_sec"] == 300
    # newest OTP message for this contact
    return outbox(redis, contact, "OTP")["data"]["code"]


def test_share_otp_pins_approve_pdf(http, redis, db, s3, hole_diff):
    s, diff_id = hole_diff["session"], hole_diff["diff_id"]
    contact = f"{unique('shop')}@machining.test"
    link_id, token = _new_link(http, hole_diff, contact)

    meta = http.get(f"/v1/public/links/{token}/meta").json()
    assert meta["part_number"] == hole_diff["part"]["part_number"] and meta["change_count"] == 2
    assert meta["rev_pair"] == {"base": "A", "target": "B"} and meta["status"] == "ACTIVE"
    assert contact not in meta["masked_contact"] and meta["masked_contact"].endswith("@machining.test")

    # no session yet
    assert http.get(f"/v1/public/links/{token}/bundle").json()["error"]["code"] == "OTP_REQUIRED"

    # 1st code: 5 wrong guesses lock it (even the right code is refused afterwards)
    code1 = _otp(http, redis, token, contact)
    wrong = "000000" if code1 != "000000" else "111111"
    for attempt in range(1, 5):
        r = http.post(f"/v1/public/links/{token}/otp/verify", json={"code": wrong})
        assert r.status_code == 401 and r.json()["error"]["remaining_attempts"] == 5 - attempt
    r = http.post(f"/v1/public/links/{token}/otp/verify", json={"code": wrong})
    assert r.status_code == 423 and r.json()["error"]["code"] == "OTP_LOCKED"
    assert http.post(f"/v1/public/links/{token}/otp/verify", json={"code": code1}).status_code == 423
    assert q(db, "SELECT attempts FROM otp_sessions WHERE review_link_id = %s", link_id) == [(5,)]

    # 2nd code: success -> rg_ext_sid (HttpOnly, 24 h)
    code2 = _otp(http, redis, token, contact)
    r = http.post(f"/v1/public/links/{token}/otp/verify", json={"code": code2})
    assert r.status_code == 200 and r.json()["verified"] is True
    ext, attrs = cookie_from(r, "rg_ext_sid")
    assert attrs == {"max-age": "86400", "path": "/", "samesite": "lax", "httponly": True, "secure": True}
    E = {"Cookie": f"rg_ext_sid={ext}"}
    stored = q(db, "SELECT otp_hash, session_token_hash FROM otp_sessions WHERE review_link_id = %s AND verified_at IS NOT NULL", link_id)
    assert len(stored) == 1 and code2 not in stored[0][0] and ext not in stored[0][1]  # only hashes at rest

    bundle = http.get(f"/v1/public/links/{token}/bundle", headers=E)
    assert bundle.status_code == 200, bundle.text
    b = bundle.json()
    assert b["diff_run"]["id"] == diff_id and len(b["items"]) == 2 and b["pins"] == []
    assert b["watermark_text"].startswith("한빛정밀 · ") and diff_id[:8] in b["watermark_text"]
    assert {"base", "target", "manifest", "delta_removed_glb"} <= set(b["signed_urls"])
    assert "raw" not in b["signed_urls"]  # original STEP only after approval
    assert httpx.get(b["signed_urls"]["base"]["lod0"]).content[:4] == b"glTF"

    # the external session is scoped to its own review_link_id
    other_link, other_token = _new_link(http, hole_diff, f"{unique('other')}@machining.test")
    assert http.get(f"/v1/public/links/{other_token}/bundle", headers=E).json()["error"]["code"] == "LINK_SCOPE"

    # change checklist + 3D pins (external) -> audit trail
    item = b["items"][0]
    r = http.patch(f"/v1/review/diff-items/{item['id']}/check", headers=E, json={"checked": True})
    assert r.status_code == 200 and r.json()["item"]["checked_by_contact"] == contact
    r = http.post(f"/v1/review/diffs/{diff_id}/pins", headers=E, json={
        "diff_item_id": item["id"], "face_id": 9, "rev_side": "TARGET", "pin_xyz": [80.0, 50.0, 20.0],
        "body": "M5 탭 깊이 확인 부탁드립니다"})
    assert r.status_code == 200, r.text
    pin = r.json()["pin"]
    assert pin["author_type"] == "EXTERNAL" and pin["author_label"] == "한빛정밀"
    internal_pin = http.post(f"/v1/review/diffs/{diff_id}/pins", headers=H(s), json={
        "face_id": 7, "rev_side": "BASE", "pin_xyz": [88.0, 12.0, 20.0], "body": "관통 탭으로 가공"}).json()["pin"]
    assert internal_pin["author_type"] == "INTERNAL"
    r = http.patch(f"/v1/review/pins/{internal_pin['id']}/resolve", headers=E, json={"resolved": True})
    assert r.status_code == 200 and r.json()["pin"]["resolved"] is True
    pins = http.get(f"/v1/review/diffs/{diff_id}/pins", headers=E).json()["pins"]
    assert [p["id"] for p in pins] == [pin["id"], internal_pin["id"]]

    # approve -> snapshot locked + PDF (WeasyPrint) in the WORM bucket
    r = http.post(f"/v1/public/links/{token}/approve", headers=E)
    assert r.status_code == 200, r.text
    ap = r.json()
    pdf = httpx.get(ap["pdf_signed_url"], timeout=60)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "가공 착수 승인" in text and hole_diff["part"]["part_number"] in text and "HOLE_DIAMETER" in text
    row = q(db, "SELECT status, approved_at, approval_pdf_r2_key FROM review_links WHERE id = %s", link_id)[0]
    assert row[0] == "APPROVED" and row[1] is not None and row[2].endswith(".pdf")
    head = s3.head_object(Bucket="revgate-audit-proofs", Key=row[2])
    assert head["ObjectLockMode"] == "GOVERNANCE" and head["ContentType"] == "application/pdf"
    assert head["Metadata"]["sha256"] == ap["pdf_sha256"]

    # terminal + locked
    assert http.post(f"/v1/public/links/{token}/approve", headers=E).json()["error"]["code"] == "ALREADY_APPROVED"
    locked = http.post(f"/v1/review/diffs/{diff_id}/pins", headers=E, json={
        "face_id": 1, "rev_side": "BASE", "pin_xyz": [0, 0, 0], "body": "late"})
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "LINK_LOCKED"
    assert http.post(f"/v1/diffs/{diff_id}/realign", headers=H(s),
                     json={"transform_4x4": [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]}).json()["error"]["code"] == "DIFF_LOCKED"
    after = http.get(f"/v1/public/links/{token}/bundle", headers=E).json()
    assert after["link"]["status"] == "APPROVED" and set(after["signed_urls"]["raw"]) == {"base", "target"}
    internal = http.get(f"/v1/links/{link_id}/approval-pdf", headers=H(s))
    assert internal.status_code == 200 and httpx.get(internal.json()["pdf_signed_url"]).content == pdf.content

    actions = [r[0] for r in q(db, "SELECT action FROM audit_events WHERE entity_id = %s ORDER BY id", link_id)]
    assert actions[0] == "LINK_CREATED" and actions.count("OTP_FAILED") == 5 and actions.count("OTP_REQUESTED") == 2
    assert actions.index("OTP_VERIFIED") < actions.index("LINK_APPROVED") < len(actions) - 1
    assert actions[-1] == "LINK_VIEWED"  # the post-approval (read-only) bundle view is audited too
    assert {r[0] for r in q(db, "SELECT action FROM audit_events WHERE entity_id IN (%s, %s, %s)",
                             item["id"], pin["id"], internal_pin["id"])} == {"DIFF_ITEM_CHECKED", "PIN_CREATED", "PIN_RESOLVED"}

    # revoke the second link -> terminal
    assert http.delete(f"/v1/links/{other_link}", headers=H(s)).json()["status"] == "REVOKED"
    assert http.post(f"/v1/public/links/{other_token}/otp/request").json()["error"]["code"] == "LINK_REVOKED"


def test_otp_request_rate_limit(http, redis, hole_diff):
    contact = f"{unique('rate')}@machining.test"
    _link_id, token = _new_link(http, hole_diff, contact)
    for _ in range(3):
        assert http.post(f"/v1/public/links/{token}/otp/request").status_code == 200
    r = http.post(f"/v1/public/links/{token}/otp/request")
    assert r.status_code == 429 and r.json()["error"]["code"] == "OTP_RATE_LIMITED"
    assert r.headers["retry-after"] == "600"
