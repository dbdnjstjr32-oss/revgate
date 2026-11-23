"""Shared fixtures: golden STEP files, real isolated job runs (no mocks), schema validators."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = WORKER_ROOT / "tests" / "golden"
REPO_ROOT = WORKER_ROOT.parents[1]
SCHEMA_DIR = REPO_ROOT / "packages" / "contracts" / "schemas"
CASES = ["01_hole_diam", "02_pocket_offset", "03_transform_shift", "04_thin_wall", "05_symmetric_needs_align"]


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    """The committed golden STEP pairs; regenerated with the real OCP generator when missing."""
    if not all((GOLDEN_DIR / c / f"rev_{s}.step").is_file() for c in CASES for s in "AB"):
        from tests.gen_golden_steps import generate_all

        generate_all(GOLDEN_DIR)
    return GOLDEN_DIR


@pytest.fixture(scope="session")
def out_root(tmp_path_factory) -> Path:
    """Job outputs. Set REVGATE_TEST_ARTIFACTS=/some/dir to keep them for inspection."""
    env = os.environ.get("REVGATE_TEST_ARTIFACTS")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return tmp_path_factory.mktemp("revgate-out")


@pytest.fixture(scope="session")
def validators() -> dict[str, Any]:
    import jsonschema
    from revgate_contracts import load_schema

    return {name: jsonschema.Draft202012Validator(load_schema(name)) for name in ("analytical_meta", "diff_manifest")}


@pytest.fixture(scope="session")
def run_ingest(golden_dir: Path, out_root: Path) -> Callable[..., tuple[Any, Path]]:
    from src.runner import run_job

    cache: dict[tuple[str, str], tuple[Any, Path]] = {}

    def _run(case: str, side: str = "A") -> tuple[Any, Path]:
        key = (case, side)
        if key not in cache:
            out = out_root / "ingest" / f"{case}_{side}"
            res = run_job({
                "job_type": "ingest",
                "revision_id": f"rev_{case}_{side}",
                "step_path": str(golden_dir / case / f"rev_{side}.step"),
                "out_dir": str(out),
            })
            cache[key] = (res, out)
        return cache[key]

    return _run


@pytest.fixture(scope="session")
def run_diff(golden_dir: Path, out_root: Path) -> Callable[..., tuple[Any, Path, dict]]:
    from src.runner import run_job

    cache: dict[tuple, tuple[Any, Path, dict]] = {}

    def _run(case: str, alignment: dict | None = None, options: dict | None = None, tag: str = "auto") -> tuple[Any, Path, dict]:
        key = (case, tag)
        if key not in cache:
            out = out_root / "diff" / f"{case}_{tag}"
            spec = {
                "job_type": "diff",
                "diff_run_id": f"diff_{case}_{tag}",
                "base": {"revision_id": f"rev_{case}_A", "step_path": str(golden_dir / case / "rev_A.step")},
                "target": {"revision_id": f"rev_{case}_B", "step_path": str(golden_dir / case / "rev_B.step")},
                "out_dir": str(out),
            }
            if alignment:
                spec["alignment"] = alignment
            if options:
                spec["options"] = options
            res = run_job(spec)
            manifest = json.loads((out / "diff_manifest.json").read_text("utf-8")) if res.ok else {}
            cache[key] = (res, out, manifest)
        return cache[key]

    return _run


def case_meta(case: str) -> dict:
    return json.loads((GOLDEN_DIR / case / "case.json").read_text("utf-8"))
