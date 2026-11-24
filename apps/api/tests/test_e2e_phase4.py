"""E2E Phase 4 (docs/PHASE4_MANUFACTURING_SPEC.md) on the real stack — api + ARQ cad-worker + Postgres + Redis + MinIO:

* SpecCardV2: Phase 1~3 cards upgraded on read, PATCH /spec -> spec_version++, strict body, workspace scoping
* Base<->Target spec diff when the 3D geometry is identical ("사양만 변경됨")
* 2D drawings: presign -> PUT -> complete (size + SHA-256 + content sniff) -> READY / DELETED
* missing-spec warnings at link creation, forced acknowledgement + DFM validation at approval
* immutable approval snapshot (canonical SHA-256, audit APPROVED payload hash, PDF rendered from it only)
* supersede triggers SPEC_UPDATED / DRAWING_UPDATED (add + delete) / NEW_REVISION (CAD worker),
  status stays APPROVED, effective_status SUPERSEDED, original STEP no longer handed out
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time

import httpx
import pytest
from pypdf import PdfReader

from .conftest import H, cookie_from, golden, login, make_part, outbox, presign_and_put, q, sse_events, unique, upload_revision, wait_revision

pytestmark = pytest.mark.e2e

LEGAL = "본 문서의 3D 치수는 STEP 공칭 형상 기준이며, 실제 가공 공차·나사·표면처리는 첨부 2D 도면 및 제조 사양이 우선합니다."
DXF = ("0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0.0\n20\n0.0\n30\n0.0\n11\n100.0\n21\n0.0\n31\n0.0\n"
       "0\nCIRCLE\n8\n0\n10\n50.0\n20\n30.0\n30\n0.0\n40\n10.0\n0\nENDSEC\n0\nEOF\n").encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_bytes(title: str) -> bytes:
    from weasyprint import HTML  # same image as the api: a real PDF, not a fixture blob

    return HTML(string=f"<h1>{title}</h1><p>일반 공차 ISO 2768-mK · 모터 축 홀 Ø6 H7</p>").write_pdf()


def card(**over) -> dict:
    c = {
        "schema_version": "2.0", "process": "CNC_MILLING",
        "material": {"grade": "AL6061-T6", "standard": "KS D 6701"},
        "heat_treatment": {"required": False},
        "surface_finish": {"type": "흑색 아노다이징 Type II", "anodizing_thickness_um": "10-15", "color": "Black"},
        "general_tolerance": {"standard": "ISO_2768_mK"},
        "critical_tolerances": [{"id": "CT1", "location": "모터 축 홀", "spec": "Ø6 H7", "datum_ref": "A"}],
        "threads_and_inserts": [{"spec": "M5x0.8", "count": 2, "depth_mm": 10, "insert_type": "TAPPED_DIRECT"}],
        "order": {"quantity": 20, "due_date": "2026-12-10", "inspection_level": "DIMENSIONAL_SHEET"},
    }
    c.update(over)
    return c


def patch_spec(http, s, rev_id: str, body: dict) -> dict:
    r = http.patch(f"/v1/revisions/{rev_id}/spec", headers=H(s), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def attach(http, s, rev_id: str, kind: str, filename: str, data: bytes, content_type: str, declared_sha: str | None = None):
    r = http.post(f"/v1/revisions/{rev_id}/attachments/presign", headers=H(s), json={
        "kind": kind, "filename": filename, "content_type": content_type, "file_size_bytes": len(data),
        "file_sha256": declared_sha or sha(data)})
    assert r.status_code == 200, r.text
    pre = r.json()
    put = httpx.put(pre["put_url"], content=data, headers=pre["headers"], timeout=60)
    assert put.status_code == 200, put.text
    return http.post(f"/v1/revisions/{rev_id}/attachments/{pre['attachment_id']}/complete", headers=H(s))


def ready_pair(http, redis, case: str = "01_hole_diam") -> dict:
    s = login(http, redis)
    part = make_part(http, s)
    a = upload_revision(http, s, part["id"], golden(case, "A"), "A")
    b = upload_revision(http, s, part["id"], golden(case, "B"), "B")
    for rid in (a, b):
        assert wait_revision(http, s, rid)["revision"]["status"] == "READY"
    return {"s": s, "part": part, "a": a, "b": b}


def make_diff(http, s, part_id: str, a: str, b: str) -> str:
    r = http.post(f"/v1/parts/{part_id}/diffs", headers=H(s), json={"base_rev_id": a, "target_rev_id": b})
    assert r.status_code == 200, r.text
    diff_id = r.json()["diff_run_id"]
    assert sse_events(s, diff_id)[-1][0] == "ready"
    return diff_id


def reviewer(http, redis, s, diff_id: str, *, allow_raw: bool = True) -> dict:
    """Share link + 6-digit OTP -> rg_ext_sid for that link."""
    contact = f"{unique('mfg')}@machining.test"
    r = http.post(f"/v1/diffs/{diff_id}/links", headers=H(s), json={
        "recipient_name": "대한정밀", "recipient_contact": contact, "allow_raw_download": allow_raw})
    assert r.status_code == 200, r.text
    created = r.json()
    token = created["share_url"].rsplit("/r/", 1)[1]
    assert http.post(f"/v1/public/links/{token}/otp/request").status_code == 200
    code = outbox(redis, contact, "OTP")["data"]["code"]
    v = http.post(f"/v1/public/links/{token}/otp/verify", json={"code": code})
    assert v.status_code == 200, v.text
    return {"link_id": created["link_id"], "token": token, "created": created,
            "E": {"Cookie": f"rg_ext_sid={cookie_from(v, 'rg_ext_sid')[0]}"}}


def approve(http, rv: dict, **body) -> dict:
    r = http.post(f"/v1/public/links/{rv['token']}/approve", headers=rv["E"], json=body)
    assert r.status_code == 200, r.text
    return r.json()


def links_by_id(http, s, diff_id: str) -> dict[str, dict]:
    return {l["id"]: l for l in http.get(f"/v1/diffs/{diff_id}/links", headers=H(s)).json()["items"]}


# ---------------------------------------------------------------------------------------------
def test_spec_card_v2_upgrade_patch_and_version(http, redis, db):
    s = login(http, redis)
    part = make_part(http, s)  # default_material AL6061-T6, default_finish Anodize
    a = upload_revision(http, s, part["id"], golden("01_hole_diam", "A"), "A")  # Phase 2 style card {"material": "AL6061"}
    wait_revision(http, s, a)
    rev = http.get(f"/v1/parts/{part['id']}", headers=H(s)).json()["revisions"][0]
    assert rev["spec_version"] == 1 and rev["spec_updated_at"] and rev["attachments"] == []
    assert rev["spec_card"]["schema_version"] == "2.0" and rev["spec_card"]["process"] == "OTHER"
    assert rev["spec_card"]["material"] == {"grade": "AL6061"}  # legacy key upgraded
    assert rev["spec_card"]["surface_finish"] == {"type": "Anodize"}  # part default fills the gap
    assert rev["spec_card"]["general_tolerance"] == {"standard": "UNSPECIFIED"}
    assert rev["spec_card_json"] == {"filename": "rev_A.step", "material": "AL6061"}  # stored JSON untouched by reads

    new = card(material={"grade": "AL7075-T6"})
    r = patch_spec(http, s, a, new)
    assert r["changed"] is True and r["superseded_links"] == 0
    assert r["revision"]["spec_version"] == 2 and r["revision"]["spec_card"]["material"] == {"grade": "AL7075-T6"}
    same = patch_spec(http, s, a, new)  # no-op edits do not bump the version
    assert same["changed"] is False and same["revision"]["spec_version"] == 2
    for bad in ({**new, "process": "LASER"}, {"material": "AL6061"}, {**new, "extra": 1},
                {**new, "order": {"inspection_level": "NONE", "due_date": "10/12/2026"}}):
        assert http.patch(f"/v1/revisions/{a}/spec", headers=H(s), json=bad).status_code == 422
    version, stored = q(db, "SELECT spec_version, spec_card_json FROM revisions WHERE id = %s", a)[0]
    assert version == 2 and stored["filename"] == "rev_A.step" and stored["schema_version"] == "2.0"
    assert stored["material"] == {"grade": "AL7075-T6"}
    assert q(db, "SELECT count(*) FROM audit_events WHERE entity_id = %s AND action = 'SPEC_UPDATED'", a) == [(1,)]
    other = login(http, redis)  # another workspace cannot read or edit it
    assert http.patch(f"/v1/revisions/{a}/spec", headers=H(other), json=new).status_code == 404


def test_geometry_identical_spec_only_diff(http, redis):
    s = login(http, redis)
    part = make_part(http, s)
    data_a = golden("01_hole_diam", "A").read_bytes()
    data_b = data_a.replace(b"revgate-01_hole_diam-rev_A", b"revgate-01_hole_diam-rev_C", 1)  # new SHA, same B-Rep
    assert data_b != data_a and len(data_b) == len(data_a)
    ids = []
    for data, label in ((data_a, "A"), (data_b, "B")):
        body = presign_and_put(http, s, part["id"], data, label, filename=f"bracket_{label}.step")
        assert http.post(f"/v1/revisions/{body['revision_id']}/complete", headers=H(s)).status_code == 200
        ids.append(body["revision_id"])
    for rid in ids:
        wait_revision(http, s, rid)
    patch_spec(http, s, ids[0], card())
    patch_spec(http, s, ids[1], card(material={"grade": "AL7075-T6", "standard": "KS D 6701"}, order={"quantity": 50, "due_date": "2026-12-10",
                                                                              "inspection_level": "DIMENSIONAL_SHEET"},
                                     surface_finish={"type": "흑색 아노다이징 Type II", "anodizing_thickness_um": "10-15",
                                                     "color": "Red"}))
    diff_id = make_diff(http, s, part["id"], ids[0], ids[1])
    d = http.get(f"/v1/diffs/{diff_id}", headers=H(s)).json()
    assert d["diff_run"]["status"] == "READY" and d["items"] == []  # 3D geometry identical
    assert {x["field"]: x["severity"] for x in d["spec_diffs"]} == {
        "material.grade": "CRITICAL", "surface_finish.color": "MAJOR", "order.quantity": "MAJOR"}
    assert next(x for x in d["spec_diffs"] if x["field"] == "order.quantity")["after"] == "50개"
    assert d["revisions"]["target"]["spec_version"] == 2 and d["spec_warnings"] == []
    assert d["attachments"] == {"base": [], "target": []}


def test_2d_attachments_verified_upload_and_delete(http, redis, db):
    s = login(http, redis)
    part = make_part(http, s)
    a = upload_revision(http, s, part["id"], golden("01_hole_diam", "A"), "A")
    wait_revision(http, s, a)

    def presign(**over):
        body = {"kind": "2D_PDF", "filename": "arm.pdf", "content_type": "application/pdf", "file_size_bytes": 1000,
                "file_sha256": "0" * 64, **over}
        return http.post(f"/v1/revisions/{a}/attachments/presign", headers=H(s), json=body)

    assert presign(filename="arm.dwg").json()["error"]["code"] == "ATTACHMENT_EXTENSION"
    assert presign(content_type="text/html").json()["error"]["code"] == "ATTACHMENT_CONTENT_TYPE"
    assert presign(file_size_bytes=25 * 1024 * 1024 + 1).json()["error"]["code"] == "FILE_TOO_LARGE"
    assert presign(kind="3D_STEP").status_code == 422

    pdf = pdf_bytes("DRN-ARM-01 Rev A")
    r = attach(http, s, a, "2D_PDF", "arm_A.pdf", pdf, "application/pdf")
    assert r.status_code == 200, r.text
    att = r.json()["attachment"]
    assert att["status"] == "READY" and att["file_sha256"] == sha(pdf) and att["kind"] == "2D_PDF"
    assert httpx.get(att["url"], timeout=30).content == pdf  # 15 min signed GET
    assert attach(http, s, a, "2D_DXF", "arm_A.dxf", DXF, "application/dxf").status_code == 200

    forged = attach(http, s, a, "2D_PDF", "forged.pdf", pdf, "application/pdf", declared_sha=sha(b"another drawing"))
    assert forged.status_code == 409 and forged.json()["error"]["code"] == "SHA256_MISMATCH"
    fake = attach(http, s, a, "2D_PDF", "fake.pdf", b"this is not a pdf at all", "application/pdf")
    assert fake.status_code == 409 and fake.json()["error"]["code"] == "CONTENT_MISMATCH"
    binary = attach(http, s, a, "2D_DXF", "bin.dxf", b"AutoCAD Binary DXF\r\n\x1a\x00" + bytes(64), "application/dxf")
    assert binary.status_code == 409 and binary.json()["error"]["code"] == "DXF_BINARY_UNSUPPORTED"

    listed = http.get(f"/v1/revisions/{a}", headers=H(s)).json()["attachments"]
    assert sorted(x["filename"] for x in listed) == ["arm_A.dxf", "arm_A.pdf"] and all(x["url"] for x in listed)
    assert dict(q(db, "SELECT filename, status FROM revision_attachments WHERE revision_id = %s", a)) == {
        "arm_A.pdf": "READY", "arm_A.dxf": "READY", "forged.pdf": "DELETED", "fake.pdf": "DELETED", "bin.dxf": "DELETED"}

    d = http.delete(f"/v1/revisions/{a}/attachments/{att['id']}", headers=H(s))
    assert d.status_code == 200 and d.json()["attachment"]["status"] == "DELETED" and d.json()["superseded_links"] == 0
    assert http.delete(f"/v1/revisions/{a}/attachments/{att['id']}", headers=H(s)).status_code == 409
    assert [x["filename"] for x in http.get(f"/v1/revisions/{a}", headers=H(s)).json()["attachments"]] == ["arm_A.dxf"]
    actions = [r[0] for r in q(db, "SELECT e.action FROM audit_events e JOIN revision_attachments t ON t.id = e.entity_id "
                                    "WHERE t.revision_id = %s ORDER BY e.id", a)]
    assert actions.count("ATTACHMENT_ADDED") == 2 and actions.count("ATTACHMENT_REJECTED") == 3
    assert actions.count("ATTACHMENT_DELETED") == 1 and actions.count("ATTACHMENT_PRESIGNED") == 5
    other = login(http, redis)
    assert http.post(f"/v1/revisions/{a}/attachments/presign", headers=H(other), json={
        "kind": "2D_PDF", "filename": "x.pdf", "content_type": "application/pdf", "file_size_bytes": 1,
        "file_sha256": "0" * 64}).status_code == 404


def test_missing_spec_warnings_and_forced_ack(http, redis, db):
    p = ready_pair(http, redis)
    s = p["s"]
    diff_id = make_diff(http, s, p["part"]["id"], p["a"], p["b"])
    rv = reviewer(http, redis, s, diff_id)
    # Phase 2 style cards: no tolerance, no 2D PDF, ISO tap hint in the 3D diff but no thread declared
    assert {w["code"]: w["level"] for w in rv["created"]["spec_warnings"]} == {
        "MISSING_TOLERANCE": "ACK_REQUIRED", "UNDECLARED_THREAD": "CAUTION"}

    bare = card(material={"grade": ""}, process="PRINT_FDM", general_tolerance={"standard": "UNSPECIFIED"},
                critical_tolerances=[], threads_and_inserts=[])
    patch_spec(http, s, p["b"], bare)
    d = http.get(f"/v1/diffs/{diff_id}", headers=H(s)).json()
    assert [w["code"] for w in d["spec_warnings"]] == [
        "MISSING_MATERIAL", "MISSING_TOLERANCE", "UNDECLARED_THREAD", "MISSING_BUILD_ORIENTATION"]

    url = f"/v1/public/links/{rv['token']}/approve"
    r = http.post(url, headers=rv["E"])
    assert r.status_code == 422 and r.json()["error"]["required"] == ["MISSING_MATERIAL", "MISSING_TOLERANCE"]
    r = http.post(url, headers=rv["E"], json={"acked_warnings": ["MISSING_MATERIAL"]})
    assert r.status_code == 422 and r.json()["error"]["required"] == ["MISSING_TOLERANCE"]
    acks = ["MISSING_MATERIAL", "MISSING_TOLERANCE", "UNDECLARED_THREAD", "MISSING_BUILD_ORIENTATION"]
    r = http.post(url, headers=rv["E"], json={"acked_warnings": acks, "dfm_ack": {"process": "CNC_MILLING", "checked_ids": []}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "DFM_ACK_INVALID" and r.json()["error"]["process"] == "PRINT_FDM"
    r = http.post(url, headers=rv["E"], json={"acked_warnings": acks, "dfm_ack": {"process": "PRINT_FDM", "checked_ids": ["corner_r"]}})
    assert r.status_code == 422 and r.json()["error"]["unknown"] == ["corner_r"]
    assert q(db, "SELECT status FROM review_links WHERE id = %s", rv["link_id"]) == [("ACTIVE",)]  # nothing half-approved

    ap = approve(http, rv, acked_warnings=acks + ["NOT_A_WARNING"],
                 dfm_ack={"process": "PRINT_FDM", "checked_ids": ["shrinkage", "orientation"]})
    snap = ap["approval_snapshot"]
    assert snap["missing_spec_warnings_acked"] == sorted(acks)  # only codes that were actually present
    assert snap["dfm_ack"] == {"process": "PRINT_FDM", "checked_ids": ["orientation", "shrinkage"]}  # canonical order
    assert q(db, "SELECT dfm_ack_json FROM review_links WHERE id = %s", rv["link_id"]) == [(snap["dfm_ack"],)]

    # a 2D PDF drawing on the target carries the tolerances -> MISSING_TOLERANCE disappears for the next link
    assert attach(http, s, p["b"], "2D_PDF", "arm_B.pdf", pdf_bytes("Rev B"), "application/pdf").status_code == 200
    codes = [w["code"] for w in http.get(f"/v1/diffs/{diff_id}", headers=H(s)).json()["spec_warnings"]]
    assert "MISSING_TOLERANCE" not in codes and "MISSING_MATERIAL" in codes


def test_immutable_snapshot_pdf_and_three_supersede_triggers(http, redis, db):
    p = ready_pair(http, redis)
    s, part_id, a, b = p["s"], p["part"]["id"], p["a"], p["b"]
    patch_spec(http, s, a, card())
    patch_spec(http, s, b, card(material={"grade": "AL7075-T6", "standard": "KS D 6701"}))
    pdf_a, pdf_b = pdf_bytes("DRN-ARM-01 Rev A"), pdf_bytes("DRN-ARM-01 Rev B")
    assert attach(http, s, a, "2D_PDF", "arm_A.pdf", pdf_a, "application/pdf").status_code == 200
    assert attach(http, s, b, "2D_PDF", "arm_B.pdf", pdf_b, "application/pdf").status_code == 200
    dxf = attach(http, s, b, "2D_DXF", "arm_B.dxf", DXF, "application/dxf")
    assert dxf.status_code == 200
    diff_id = make_diff(http, s, part_id, a, b)

    # ---- approval 1: full spec -> no forced ack; one unresolved pin; DFM checks
    l1 = reviewer(http, redis, s, diff_id)
    assert l1["created"]["spec_warnings"] == []
    pin = http.post(f"/v1/review/diffs/{diff_id}/pins", headers=l1["E"], json={
        "face_id": 7, "rev_side": "BASE", "pin_xyz": [88.0, 12.0, 20.0], "body": "탭 깊이 확인"})
    assert pin.status_code == 200, pin.text
    ap = approve(http, l1, dfm_ack={"process": "CNC_MILLING", "checked_ids": ["thin_wall", "corner_r"]})
    snap, snap_sha = ap["approval_snapshot"], ap["snapshot_sha256"]
    stored, stored_sha = q(db, "SELECT approval_snapshot_json, approval_snapshot_sha256 FROM review_links WHERE id = %s",
                           l1["link_id"])[0]
    assert stored == snap and stored_sha == snap_sha
    assert hashlib.sha256(json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == snap_sha
    assert q(db, "SELECT payload_sha256 FROM audit_events WHERE entity_id = %s AND action = 'APPROVED'", l1["link_id"]) == [(snap_sha,)]
    assert snap["snapshot_version"] == "1.0" and snap["approver"]["name"] == "대한정밀"
    assert snap["approver"]["contact"] == l1["created"]["link"]["masked_contact"] and "*" in snap["approver"]["contact"]
    assert snap["target_rev"]["step_sha256"] == sha(golden("01_hole_diam", "B").read_bytes())
    assert snap["target_rev"]["spec_version"] == 2 and snap["target_rev"]["spec_card"]["material"]["grade"] == "AL7075-T6"
    assert {(x["kind"], x["filename"], x["sha256"]) for x in snap["target_rev"]["attachments"]} == {
        ("2D_PDF", "arm_B.pdf", sha(pdf_b)), ("2D_DXF", "arm_B.dxf", sha(DXF))}
    assert [(x["field"], x["severity"]) for x in snap["spec_diffs"]] == [("material.grade", "CRITICAL")]
    assert [g["category"] for g in snap["geometry_changes"]] == ["HOLE_DIAMETER", "HOLE_DIAMETER"]
    assert snap["unresolved_pin_count"] == 1 and snap["dfm_ack"]["checked_ids"] == ["corner_r", "thin_wall"]
    assert re.fullmatch(r"[0-9a-f]{64}", snap["diff_run"]["manifest_sha256"]) and len(snap["diff_run"]["transform_4x4"]) == 16

    pdf = httpx.get(ap["pdf_signed_url"], timeout=60).content
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)
    flat = "".join(text.split())
    for needle in ("PRODUCTIONAPPROVALRECORD", snap_sha, snap["target_rev"]["step_sha256"], sha(pdf_b), sha(DXF),
                   "".join(LEGAL.split()), "AL7075-T6", "arm_B.dxf", "가공착수승인"):
        assert needle in flat, needle
    bundle = http.get(f"/v1/public/links/{l1['token']}/bundle", headers=l1["E"]).json()
    assert bundle["link"]["effective_status"] == "APPROVED" and set(bundle["signed_urls"]["raw"]) == {"base", "target"}
    assert {x["filename"] for x in bundle["attachments"]["target"]} == {"arm_B.pdf", "arm_B.dxf"}

    # ---- SPEC_UPDATED: editing the approved target supersedes l1; its snapshot + PDF stay byte-identical
    r = patch_spec(http, s, b, card(material={"grade": "AL7075-T6", "standard": "KS D 6701"},
                                    order={"quantity": 30, "inspection_level": "NONE"}))
    assert r["superseded_links"] == 1 and r["revision"]["spec_version"] == 3
    link1 = links_by_id(http, s, diff_id)[l1["link_id"]]
    assert (link1["status"], link1["effective_status"], link1["superseded_reason"], link1["superseded_by_revision_id"]) == (
        "APPROVED", "SUPERSEDED", "SPEC_UPDATED", b)
    assert q(db, "SELECT approval_snapshot_json, approval_snapshot_sha256 FROM review_links WHERE id = %s",
             l1["link_id"])[0] == (snap, snap_sha)
    again = http.get(f"/v1/links/{l1['link_id']}/approval-pdf", headers=H(s)).json()
    assert again["approval_snapshot_sha256"] == snap_sha and again["effective_status"] == "SUPERSEDED"
    assert httpx.get(again["pdf_signed_url"], timeout=60).content == pdf  # never re-rendered
    stale = http.get(f"/v1/public/links/{l1['token']}/bundle", headers=l1["E"]).json()
    assert stale["link"]["effective_status"] == "SUPERSEDED" and "raw" not in stale["signed_urls"]  # no original STEP
    meta = http.get(f"/v1/public/links/{l1['token']}/meta").json()
    assert meta["effective_status"] == "SUPERSEDED" and meta["superseded_reason"] == "SPEC_UPDATED" and meta["status"] == "APPROVED"

    # ---- DRAWING_UPDATED (added): l2 approved on spec_version 3, then a new drawing lands on the target
    l2 = reviewer(http, redis, s, diff_id)
    assert approve(http, l2)["approval_snapshot"]["target_rev"]["spec_version"] == 3
    added = attach(http, s, b, "2D_PDF", "arm_B_r2.pdf", pdf_bytes("Rev B r2"), "application/pdf")
    assert added.status_code == 200 and added.json()["superseded_links"] == 1  # l1 keeps its first reason

    # ---- DRAWING_UPDATED (deleted): l3 approved, then a drawing is removed
    l3 = reviewer(http, redis, s, diff_id)
    approve(http, l3)
    gone = http.delete(f"/v1/revisions/{b}/attachments/{dxf.json()['attachment']['id']}", headers=H(s))
    assert gone.status_code == 200 and gone.json()["superseded_links"] == 1

    # base-side edits leave approvals of the target alone
    l4 = reviewer(http, redis, s, diff_id)
    approve(http, l4)
    assert patch_spec(http, s, a, card(order={"quantity": 99, "inspection_level": "NONE"}))["superseded_links"] == 0
    assert links_by_id(http, s, diff_id)[l4["link_id"]]["effective_status"] == "APPROVED"

    # ---- NEW_REVISION: a newer revision of the part turns READY in the CAD worker -> live approvals go stale
    c = upload_revision(http, s, part_id, golden("02_pocket_offset", "A"), "C")
    assert wait_revision(http, s, c)["revision"]["status"] == "READY"
    deadline = time.monotonic() + 30
    while links_by_id(http, s, diff_id)[l4["link_id"]]["effective_status"] != "SUPERSEDED":
        assert time.monotonic() < deadline, "NEW_REVISION supersede did not happen"
        time.sleep(0.5)
    links = links_by_id(http, s, diff_id)
    assert {k: links[v["link_id"]]["superseded_reason"] for k, v in (("l1", l1), ("l2", l2), ("l3", l3), ("l4", l4))} == {
        "l1": "SPEC_UPDATED", "l2": "DRAWING_UPDATED", "l3": "DRAWING_UPDATED", "l4": "NEW_REVISION"}
    assert links[l4["link_id"]]["superseded_by_revision_id"] == c and links[l4["link_id"]]["latest_revision_label"] == "C"
    ids = [l1["link_id"], l2["link_id"], l3["link_id"], l4["link_id"]]
    assert q(db, "SELECT DISTINCT status FROM review_links WHERE id = ANY(%s::uuid[])", ids) == [("APPROVED",)]
    reasons = q(db, "SELECT count(*) FROM audit_events WHERE action = 'LINK_SUPERSEDED' AND entity_id = ANY(%s::uuid[])", ids)
    assert reasons == [(4,)]  # exactly one supersede per approval (the first reason wins)
    assert q(db, "SELECT actor_contact FROM audit_events WHERE action = 'LINK_SUPERSEDED' AND entity_id = %s",
             l4["link_id"]) == [("system:cad-worker",)]  # NEW_REVISION is marked by the worker in the READY transaction
