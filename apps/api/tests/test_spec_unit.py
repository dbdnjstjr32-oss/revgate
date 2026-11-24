"""Phase 4 manufacturing-spec rules (docs/PHASE4_MANUFACTURING_SPEC.md §2.1, §3) — pure functions, no infra."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from revgate_contracts import SpecCardV2

from app import spec as SP


def full_card(**over) -> dict:
    card = {
        "schema_version": "2.0", "process": "CNC_MILLING",
        "material": {"grade": "AL6061-T6", "standard": "KS D 6701"},
        "heat_treatment": {"required": False},
        "surface_finish": {"type": "흑색 아노다이징 Type II", "anodizing_thickness_um": "10-15", "color": "Black"},
        "general_tolerance": {"standard": "ISO_2768_mK"},
        "critical_tolerances": [{"id": "CT1", "location": "모터 축 홀", "spec": "Ø6 H7", "datum_ref": "A"}],
        "threads_and_inserts": [{"spec": "M5x0.8", "count": 2, "depth_mm": 10, "insert_type": "TAPPED_DIRECT"}],
        "order": {"quantity": 20, "due_date": "2026-12-10", "inspection_level": "DIMENSIONAL_SHEET"},
    }
    card.update(over)
    return card


@pytest.mark.parametrize("raw", [{}, None, {"filename": "rev_A.step"}, {"material": 7}, {"schema_version": "2.0"}])
def test_legacy_and_empty_cards_upgrade_to_valid_v2(raw):
    card = SP.upgrade_spec_card(raw)
    SpecCardV2.model_validate(card)  # strictly valid
    assert card["schema_version"] == "2.0" and card["process"] == "OTHER"
    assert card["general_tolerance"] == {"standard": "UNSPECIFIED"} and card["critical_tolerances"] == []
    assert card["material"] == {"grade": ""} and card["order"] == {"inspection_level": "NONE"}


def test_legacy_keys_and_part_defaults_fill_the_gaps():
    card = SP.upgrade_spec_card({"material": "AL6061", "filename": "a.step", "submitted_by": {"name": "x"}},
                                default_material="SUS304", default_finish="Anodize")
    assert card["material"] == {"grade": "AL6061"}  # the revision's own legacy value wins over the part default
    assert card["surface_finish"] == {"type": "Anodize"}
    assert "filename" not in card and "submitted_by" not in card
    assert SP.upgrade_spec_card({}, default_material="SUS304")["material"] == {"grade": "SUS304"}


def test_valid_v2_card_is_kept_and_partial_cards_are_salvaged():
    card = full_card()
    assert SP.upgrade_spec_card({**card, "filename": "x.step"}) == SP.canonical(card)
    broken = full_card(process="LASER")  # invalid enum -> salvage the valid parts, default the rest
    broken["threads_and_inserts"].append({"spec": "M3", "count": 0, "insert_type": "PEM"})  # invalid item dropped
    out = SP.upgrade_spec_card(broken)
    assert out["process"] == "OTHER" and out["material"]["grade"] == "AL6061-T6"
    assert out["threads_and_inserts"] == card["threads_and_inserts"]
    assert out["critical_tolerances"] == card["critical_tolerances"]


def test_stored_card_keeps_phase3_metadata():
    stored = SP.stored_card({"filename": "a.step", "material": "old", "submitted_by": {"name": "n"}}, SP.canonical(full_card()))
    assert stored["filename"] == "a.step" and stored["submitted_by"] == {"name": "n"}
    assert SP.upgrade_spec_card(stored) == SP.canonical(full_card())


def test_spec_diff_severity_rules():
    base = SP.canonical(full_card())
    target = copy.deepcopy(base)
    target["material"]["grade"] = "AL7075-T6"
    target["heat_treatment"] = {"required": True, "spec": "T6 재시효"}
    target["general_tolerance"] = {"standard": "ISO_2768_fH"}
    target["critical_tolerances"][0]["spec"] = "Ø6 H6"
    target["threads_and_inserts"][0]["insert_type"] = "HELICOIL"
    target["surface_finish"]["color"] = "Red"
    target["surface_finish"]["ra_um"] = 1.6
    target["order"]["quantity"] = 50
    target["order"]["due_date"] = "2026-12-24"
    target["order"]["inspection_level"] = "CMM_REPORT"
    diffs = {d["field"]: d for d in SP.compute_spec_diff(base, target)}
    assert {f for f, d in diffs.items() if d["severity"] == "CRITICAL"} == {
        "material.grade", "heat_treatment", "general_tolerance", "critical_tolerances", "threads_and_inserts"}
    assert {f for f, d in diffs.items() if d["severity"] == "MAJOR"} == {
        "surface_finish.color", "surface_finish.ra_um", "order.quantity", "order.due_date"}
    assert diffs["order.inspection_level"]["severity"] == "INFO"
    assert diffs["material.grade"]["before"] == "AL6061-T6" and diffs["material.grade"]["after"] == "AL7075-T6"
    assert diffs["surface_finish.ra_um"] == {"field": "surface_finish.ra_um", "label": "표면 조도 Ra", "before": None,
                                             "after": "1.6 µm", "severity": "MAJOR"}
    assert diffs["heat_treatment"]["before"] == "없음" and diffs["heat_treatment"]["after"] == "필요 · T6 재시효"
    assert diffs["general_tolerance"]["after"] == "ISO 2768-fH"
    assert SP.compute_spec_diff(base, copy.deepcopy(base)) == []


def test_process_and_build_orientation_are_critical():
    base = SP.canonical(full_card())
    printed = SP.canonical(full_card(process="PRINT_FDM", additive_spec={"build_orientation": "Z+ 상향", "infill_pct": 40}))
    diffs = {d["field"]: d for d in SP.compute_spec_diff(base, printed)}
    assert diffs["process"]["severity"] == "CRITICAL" and diffs["process"]["after"] == "3D 프린팅 (FDM)"
    assert diffs["additive_spec.build_orientation"]["severity"] == "CRITICAL"
    assert diffs["additive_spec.infill_pct"]["severity"] == "INFO"


def test_missing_spec_warnings():
    ok = SP.canonical(full_card())
    assert SP.missing_spec_warnings(ok, target_has_pdf=False, hints=["참고 추정: ISO M4 → M5 탭 기초홀 규격과 일치"]) == []

    bare = SP.upgrade_spec_card({})
    codes = {w["code"]: w["level"] for w in SP.missing_spec_warnings(bare, target_has_pdf=False, hints=["ISO M4 → M5"])}
    assert codes == {"MISSING_MATERIAL": "ACK_REQUIRED", "MISSING_TOLERANCE": "ACK_REQUIRED", "UNDECLARED_THREAD": "CAUTION"}
    # a 2D PDF drawing carries the tolerances -> no MISSING_TOLERANCE
    assert "MISSING_TOLERANCE" not in {w["code"] for w in SP.missing_spec_warnings(bare, target_has_pdf=True, hints=[])}
    # a critical tolerance alone is enough as well
    with_ct = SP.canonical({**bare, "critical_tolerances": [{"id": "1", "location": "홀", "spec": "Ø6 H7"}]})
    assert "MISSING_TOLERANCE" not in {w["code"] for w in SP.missing_spec_warnings(with_ct, target_has_pdf=False, hints=[])}
    printed = SP.canonical(full_card(process="PRINT_SLA_SLS_MJF"))
    assert [w["code"] for w in SP.missing_spec_warnings(printed, target_has_pdf=False, hints=[])] == ["MISSING_BUILD_ORIENTATION"]
    blank_orientation = SP.canonical(full_card(process="PRINT_FDM", additive_spec={"build_orientation": "  "}))
    assert [w["code"] for w in SP.missing_spec_warnings(blank_orientation, target_has_pdf=True, hints=[])] == [
        "MISSING_BUILD_ORIENTATION"]


def test_dfm_checklists_per_process():
    assert SP.dfm_check_ids("CNC_TURNING") == ("corner_r", "thin_wall", "precision_fit", "coating_shift", "datum_setup")
    assert SP.dfm_check_ids("PRINT_FDM") == ("orientation", "overhang", "heat_insert", "shrinkage")
    assert SP.dfm_check_ids("SHEET_METAL") == () and SP.dfm_check_ids("OTHER") == ()


def test_snapshot_sha256_is_the_specified_canonical_json():
    snap = {"b": [1, {"y": "한글", "x": None}], "a": 1.5}
    expected = hashlib.sha256(json.dumps(snap, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert SP.snapshot_sha256(snap) == expected
    assert SP.snapshot_sha256({"a": 1.5, "b": [1, {"x": None, "y": "한글"}]}) == expected  # key order independent
