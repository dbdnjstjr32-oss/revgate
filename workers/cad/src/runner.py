"""1-Job-1-Subprocess isolation (IMPLEMENTATION_BLUEPRINT §3.4 / §5.3).

A corrupt STEP can take OCCT down with a segfault instead of a Python exception, so every job
runs in a fresh interpreter:

    parent (ARQ task / CLI, never imports OCP)
      └─ subprocess.run([python, -m, src.runner, --child, job_spec.json], timeout=60)
           └─ (diff jobs) subprocess.run([python, -m, src.boolean_job, ...], timeout=15)

Result protocol
  exit 0                    -> job_result.json holds the result (status READY / NEEDS_ALIGN)
  exit EXIT_DOMAIN_ERROR(3) -> job_result.json holds {"error_code": <DOMAIN_ERRORS>, ...}
  signal / 128+signal       -> EXIT_CODE_MAP (OOM_KILLED, SEGFAULT_OCCT)
  TimeoutExpired            -> TIMEOUT_WORKER_60S
  anything else             -> WORKER_INTERNAL_ERROR (+ stderr tail)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .constants import SUBPROCESS_TIMEOUT_SEC
from .errors import EXIT_DOMAIN_ERROR, TIMEOUT_ERROR_CODE, WORKER_INTERNAL_ERROR, CadDomainError, map_exit_code

WORKER_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCRATCH = Path(os.environ.get("REVGATE_SCRATCH_DIR", "/tmp/revgate_scratch"))
JOB_TYPES = ("ingest", "diff")


@dataclass
class JobResult:
    ok: bool
    status: str  # READY | NEEDS_ALIGN | FAILED
    error_code: str | None = None
    message: str | None = None
    returncode: int | None = None
    duration_ms: float = 0.0
    result: dict[str, Any] | None = None
    stderr_tail: str = ""
    work_dir: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(WORKER_ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _tail(data: bytes | str | None, limit: int = 4000) -> str:
    if not data:
        return ""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    return text[-limit:]


def run_subprocess(
    cmd: Sequence[str],
    *,
    result_path: Path | None = None,
    timeout: float = SUBPROCESS_TIMEOUT_SEC,
    cwd: Path | None = None,
) -> JobResult:
    """Run one isolated job process and map its outcome to the standard error codes."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            list(cmd), cwd=str(cwd or WORKER_ROOT), env=child_env(), capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:  # child already killed by subprocess.run
        return JobResult(
            ok=False, status="FAILED", error_code=TIMEOUT_ERROR_CODE,
            message=f"job exceeded {timeout:g}s", duration_ms=(time.monotonic() - t0) * 1e3,
            stderr_tail=_tail(exc.stderr),
        )
    duration = (time.monotonic() - t0) * 1e3
    rc = proc.returncode
    crash = map_exit_code(rc)
    if crash is not None:
        return JobResult(ok=False, status="FAILED", error_code=crash, message=f"job process died (exit {rc})",
                         returncode=rc, duration_ms=duration, stderr_tail=_tail(proc.stderr))

    payload: dict[str, Any] | None = None
    if result_path is not None and result_path.is_file():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None

    if rc == 0 and payload is not None:
        return JobResult(ok=True, status=payload.get("status", "READY"), returncode=0, duration_ms=duration,
                         result=payload, stderr_tail=_tail(proc.stderr, 1000))
    if rc == EXIT_DOMAIN_ERROR and payload is not None and payload.get("error_code"):
        return JobResult(ok=False, status="FAILED", error_code=payload["error_code"], message=payload.get("message"),
                         returncode=rc, duration_ms=duration, details=payload.get("details", {}),
                         stderr_tail=_tail(proc.stderr, 1000))
    return JobResult(ok=False, status="FAILED", error_code=WORKER_INTERNAL_ERROR,
                     message=f"job process exited with {rc}", returncode=rc, duration_ms=duration,
                     stderr_tail=_tail(proc.stderr))


def _absolutize(spec: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(spec))
    for key in ("step_path", "out_dir"):
        if key in out and out[key]:
            out[key] = str(Path(out[key]).resolve())
    for side in ("base", "target"):
        if side in out and out[side].get("step_path"):
            out[side]["step_path"] = str(Path(out[side]["step_path"]).resolve())
    return out


def run_job(
    spec: dict[str, Any],
    *,
    timeout: float = SUBPROCESS_TIMEOUT_SEC,
    scratch_root: Path | None = None,
    keep_work_dir: bool = False,
) -> JobResult:
    """Execute an ingest/diff job in an isolated interpreter (1 job = 1 subprocess)."""
    if spec.get("job_type") not in JOB_TYPES:
        return JobResult(ok=False, status="FAILED", error_code=WORKER_INTERNAL_ERROR,
                         message=f"unknown job_type {spec.get('job_type')!r}")
    job_id = str(spec.get("job_id") or uuid.uuid4())
    root = Path(scratch_root) if scratch_root else DEFAULT_SCRATCH
    work_dir = root / f"job-{job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    spec = _absolutize({**spec, "job_id": job_id, "work_dir": str(work_dir.resolve())})
    spec_path = work_dir / "job_spec.json"
    result_path = work_dir / "job_result.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        res = run_subprocess([sys.executable, "-m", "src.runner", "--child", str(spec_path)],
                             result_path=result_path, timeout=timeout)
    finally:
        if not keep_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
    res.work_dir = str(work_dir) if keep_work_dir else None
    return res


# ============================================================================================
# child side
# ============================================================================================
def die_with_parent() -> None:
    """Linux: SIGKILL this process if the parent dies (no orphaned OCCT jobs)."""
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            import signal

            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
        except OSError:
            pass


def _child_main(spec_path: Path) -> int:
    die_with_parent()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    result_path = spec_path.with_name("job_result.json")
    try:
        if spec["job_type"] == "ingest":
            from .ingest import run_ingest_job

            result = run_ingest_job(spec)
        elif spec["job_type"] == "diff":
            from .diff_engine import run_diff_job

            result = run_diff_job(spec)
        else:  # pragma: no cover - validated by the parent
            raise ValueError(f"unknown job_type {spec['job_type']!r}")
    except CadDomainError as exc:
        result_path.write_text(json.dumps({"status": "FAILED", **exc.to_dict()}, ensure_ascii=False), encoding="utf-8")
        return EXIT_DOMAIN_ERROR
    except Exception:  # noqa: BLE001 - reported as WORKER_INTERNAL_ERROR by the parent
        traceback.print_exc()
        return 1
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RevGate CAD job runner (internal)")
    ap.add_argument("--child", type=Path, required=True, help="job_spec.json written by run_job()")
    args = ap.parse_args(argv)
    return _child_main(args.child)


if __name__ == "__main__":
    sys.exit(main())
