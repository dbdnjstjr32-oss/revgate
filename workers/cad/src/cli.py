"""RevGate CAD CLI (week-1 DoD: STEP x2 -> auto-align + B-Rep diff JSON + lod GLBs + analytical_meta).

Every command goes through runner.run_job, i.e. the same isolated subprocess path the queue uses.

    python -m src.cli ingest  part.step --out out/rev_a
    python -m src.cli diff    rev_A.step rev_B.step --out out/diff [--transform "1,0,0,0,..."]
    python -m src.cli compare rev_A.step rev_B.step --out out/compare
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from .runner import JobResult, run_job


def _print(res: JobResult, label: str) -> None:
    head = f"[{label}] {res.status}"
    if res.error_code:
        head += f" error_code={res.error_code} ({res.message})"
    head += f" in {res.duration_ms / 1000:.2f}s"
    print(head)
    if res.result:
        print(json.dumps(res.result, ensure_ascii=False, indent=2))
    elif res.stderr_tail:
        print(res.stderr_tail, file=sys.stderr)


def _ingest(step: str, out: Path, revision_id: str | None) -> JobResult:
    return run_job({
        "job_type": "ingest",
        "revision_id": revision_id or f"rev_{Path(step).stem}",
        "step_path": step,
        "out_dir": str(out),
    })


def _diff(base: str, target: str, out: Path, transform: list[float] | None) -> JobResult:
    alignment = {"mode": "MANUAL_321", "transform_4x4": transform} if transform else {"mode": "AUTO"}
    return run_job({
        "job_type": "diff",
        "diff_run_id": f"diff_{uuid.uuid4().hex[:12]}",
        "base": {"revision_id": f"rev_{Path(base).stem}", "step_path": base},
        "target": {"revision_id": f"rev_{Path(target).stem}", "step_path": target},
        "out_dir": str(out),
        "alignment": alignment,
    })


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ing = sub.add_parser("ingest", help="STEP -> lod0.glb, lod1.glb, analytical_meta.json")
    p_ing.add_argument("step")
    p_ing.add_argument("--out", required=True, type=Path)
    p_ing.add_argument("--revision-id")

    p_diff = sub.add_parser("diff", help="auto-align + B-Rep diff -> diff_manifest.json (+ delta GLBs)")
    p_diff.add_argument("base")
    p_diff.add_argument("target")
    p_diff.add_argument("--out", required=True, type=Path)
    p_diff.add_argument("--transform", help="16 comma-separated numbers, row-major Target->Base (MANUAL_321)")

    p_cmp = sub.add_parser("compare", help="ingest both revisions + diff (week-1 demo)")
    p_cmp.add_argument("base")
    p_cmp.add_argument("target")
    p_cmp.add_argument("--out", required=True, type=Path)

    args = ap.parse_args(argv)
    if args.cmd == "ingest":
        res = _ingest(args.step, args.out, args.revision_id)
        _print(res, "ingest")
        return 0 if res.ok else 1
    if args.cmd == "diff":
        transform = [float(x) for x in args.transform.split(",")] if args.transform else None
        if transform is not None and len(transform) != 16:
            ap.error("--transform needs exactly 16 numbers")
        res = _diff(args.base, args.target, args.out, transform)
        _print(res, "diff")
        return 0 if res.ok else 1

    t0 = time.monotonic()
    results = {
        "ingest_base": _ingest(args.base, args.out / "base", None),
        "ingest_target": _ingest(args.target, args.out / "target", None),
    }
    results["diff"] = _diff(args.base, args.target, args.out / "diff", None)
    for label, res in results.items():
        _print(res, label)
    total = time.monotonic() - t0
    ok = all(r.ok for r in results.values())
    print(f"[compare] {'OK' if ok else 'FAILED'} total {total:.2f}s -> {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
