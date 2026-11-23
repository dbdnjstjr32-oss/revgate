"""1-Job-1-Subprocess isolation and the standard error codes (§3.4 / §5.3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.errors import TIMEOUT_ERROR_CODE, CadDomainError
from src.runner import run_job, run_subprocess


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.mark.parametrize(
    ("code", "expected", "rc"),
    [
        ("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)", "SEGFAULT_OCCT", -11),
        ("import ctypes; ctypes.string_at(0)", "SEGFAULT_OCCT", -11),  # a real native null-pointer crash
        ("import os, signal; os.kill(os.getpid(), signal.SIGABRT)", "SEGFAULT_OCCT", -6),
        ("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", "OOM_KILLED", -9),
        ("import sys; sys.exit(137)", "OOM_KILLED", 137),
        ("import sys; sys.exit(139)", "SEGFAULT_OCCT", 139),
        ("import sys; sys.exit(134)", "SEGFAULT_OCCT", 134),
    ],
)
def test_exit_code_map(code, expected, rc):
    res = run_subprocess(_py(code))
    assert (res.ok, res.error_code, res.returncode) == (False, expected, rc)


def test_timeout_maps_to_timeout_worker_60s():
    res = run_subprocess(_py("import time; time.sleep(30)"), timeout=1.0)
    assert (res.ok, res.error_code) == (False, TIMEOUT_ERROR_CODE)
    assert res.duration_ms < 10_000


def _cgroup_limit(name: str) -> int | None:
    p = Path("/sys/fs/cgroup") / name
    if not p.is_file():
        return None
    raw = p.read_text().strip()
    return None if raw == "max" else int(raw)


MEM_MAX = _cgroup_limit("memory.max")
SWAP_MAX = _cgroup_limit("memory.swap.max") or 0


@pytest.mark.skipif(not (MEM_MAX and MEM_MAX <= 4 * 1024**3), reason="needs the cad-worker container (mem_limit 2g)")
def test_real_oom_kill_is_reported():
    """Grow a real job process past mem_limit (+swap): the kernel OOM-kills it, the parent survives."""
    target = MEM_MAX + SWAP_MAX + 512 * 1024**2
    code = (
        "chunks = []\n"
        f"while sum(map(len, chunks)) < {target}:\n"
        "    chunks.append(bytearray(b'\\x01') * (64 * 1024 * 1024))\n"
    )
    res = run_subprocess(_py(code), timeout=120)
    assert (res.ok, res.error_code, res.returncode) == (False, "OOM_KILLED", -9)


@pytest.mark.skipif(MEM_MAX is None, reason="needs the cad-worker container")
def test_container_memory_limit_is_hard():
    """compose: mem_limit 2g + memswap_limit 2g -> no swap headroom (blueprint: mem_limit 2g 강제)."""
    assert MEM_MAX == 2 * 1024**3
    assert SWAP_MAX == 0


def test_uncaught_exception_is_internal_error():
    res = run_subprocess(_py("raise RuntimeError('boom')"))
    assert (res.ok, res.error_code, res.returncode) == (False, "WORKER_INTERNAL_ERROR", 1)
    assert "boom" in res.stderr_tail


def _ingest(step: Path, out: Path, **extra):
    return run_job({"job_type": "ingest", "revision_id": "rev_x", "step_path": str(step), "out_dir": str(out), **extra})


def test_domain_error_invalid_step_syntax(tmp_path):
    bad = tmp_path / "garbage.step"
    bad.write_text("this is not an ISO-10303-21 file\n" * 20)
    res = _ingest(bad, tmp_path / "o")
    assert (res.ok, res.error_code) == (False, "INVALID_STEP_SYNTAX")


def test_domain_error_raw_file_not_found(tmp_path):
    res = _ingest(tmp_path / "missing.step", tmp_path / "o")
    assert (res.ok, res.error_code) == (False, "RAW_FILE_NOT_FOUND")


def test_domain_error_empty_geometry(tmp_path):
    from tests.gen_golden_steps import EMPTY_STEP

    empty = tmp_path / "empty.step"
    empty.write_text(EMPTY_STEP)
    res = _ingest(empty, tmp_path / "o")
    assert (res.ok, res.error_code) == (False, "EMPTY_GEOMETRY")


def test_domain_error_sha256_mismatch(golden_dir, tmp_path):
    step = golden_dir / "01_hole_diam" / "rev_A.step"
    res = _ingest(step, tmp_path / "o", expected_sha256="0" * 64)
    assert (res.ok, res.error_code) == (False, "SHA256_MISMATCH")
    assert res.details["actual"] != "0" * 64


def test_hash_verified_redrop_accepts_matching_sha(golden_dir, tmp_path):
    from src.ingest import sha256_file

    step = golden_dir / "01_hole_diam" / "rev_A.step"
    res = _ingest(step, tmp_path / "o", expected_sha256=sha256_file(step))
    assert res.ok and res.result["file_sha256"] == sha256_file(step)


def test_domain_error_too_many_solids(tmp_path):
    from tests.gen_golden_steps import write_many_solids

    step = write_many_solids(tmp_path / "eleven.step", n=11)
    res = _ingest(step, tmp_path / "o")
    assert (res.ok, res.error_code) == (False, "TOO_MANY_SOLIDS")
    assert res.details["solids"] == 11


def test_ten_solids_are_accepted(tmp_path):
    from tests.gen_golden_steps import write_many_solids

    step = write_many_solids(tmp_path / "ten.step", n=10)
    assert _ingest(step, tmp_path / "o").ok


def test_domain_error_too_many_faces(golden_dir, monkeypatch):
    from src import constants as C
    from src.ingest import check_limits, read_step

    shape, _u, _c = read_step(str(golden_dir / "01_hole_diam" / "rev_A.step"))
    check_limits(shape)  # 10 faces: fine under the real limit
    monkeypatch.setattr(C, "MAX_FACES_HARD", 9)
    with pytest.raises(CadDomainError) as exc:
        check_limits(shape)
    assert exc.value.code == "TOO_MANY_FACES"


def test_scratch_dir_is_cleaned(tmp_path, golden_dir):
    res = run_job({"job_type": "ingest", "revision_id": "r", "step_path": str(golden_dir / "01_hole_diam" / "rev_A.step"),
                   "out_dir": str(tmp_path / "out")}, scratch_root=tmp_path / "scratch")
    assert res.ok
    assert list((tmp_path / "scratch").iterdir()) == []


def test_unknown_job_type_is_rejected():
    res = run_job({"job_type": "render"})
    assert (res.ok, res.error_code) == (False, "WORKER_INTERNAL_ERROR")
