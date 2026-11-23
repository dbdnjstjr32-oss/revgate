# RevGate CAD worker

OCP (cadquery-ocp 7.8.1.1.post1 / OCCT 7.8) engine, run as **one isolated subprocess per job**
(`src/runner.py`, `subprocess.run(..., timeout=60)`). Blueprint references: §2 (contracts),
§5 (numbers, fallbacks, error codes), §7 (golden suite).

| module | role |
| --- | --- |
| `constants.py` | §5.2 constants verbatim + documented implementation constants |
| `errors.py` | `EXIT_CODE_MAP`, `DOMAIN_ERRORS`, `WARNINGS`, `TIMEOUT_WORKER_60S`, `CadDomainError` |
| `ingest.py` | XDE STEP read (`xstep.cascade.unit=MM`, `FileUnits` → `source_unit`, face colours), limits, `BRepCheck_Analyzer` + `ShapeFix_Shape`, 0-based `face_id` via `TopTools_IndexedMapOfShape`, analytical params, `analytical_meta.json` |
| `tessellate.py` | `get_lod_params`, lod0/lod1 meshing, `BRepTools.Clean_s` between LODs |
| `glb_builder.py` | pygltflib + numpy 1-primitive GLB: POSITION, NORMAL, COLOR_0, `_FACE_ID` (FLOAT32) |
| `align.py` | IDENTITY fast path → P1 plane+hole frames / P2 3-plane frames / P3 OBB(24)+trimmed ICP, area-weighted `align_confidence`, Kabsch snap, 0.50 gate |
| `diff_engine.py` | stage 1 AAG face matching, stage 2 AFR facts + ISO tap-drill hints + thin walls, stage 3 Boolean (subprocess, 15 s) / cKDTree heatmap fallback, `diff_manifest.json` |
| `boolean_job.py` | `BRepAlgoAPI_Cut` A−B / B−A in its own process |
| `runner.py` | parent/child job protocol, exit-code mapping |
| `cli.py` | `ingest` / `diff` / `compare` |

## Job spec (what the ARQ task will enqueue)

```json
{"job_type": "ingest", "revision_id": "rev_…", "step_path": "/tmp/revgate_scratch/…/a.step",
 "out_dir": "…", "expected_sha256": "optional (hash-verified re-drop)"}

{"job_type": "diff", "diff_run_id": "diff_…",
 "base":   {"revision_id": "rev_a", "step_path": "…", "expected_sha256": "optional"},
 "target": {"revision_id": "rev_b", "step_path": "…"},
 "out_dir": "…",
 "alignment": {"mode": "AUTO"} | {"mode": "MANUAL_321", "transform_4x4": [16 numbers, row-major Target->Base]}}
```

Result (`JobResult`): `status` READY | NEEDS_ALIGN | FAILED, `error_code` (EXIT_CODE_MAP / DOMAIN_ERRORS /
`TIMEOUT_WORKER_60S` / `WORKER_INTERNAL_ERROR`), `result` (artifacts, summary, warnings, timings).

## Tests

```sh
docker compose -f infra/docker-compose.yml run --rm cad-worker pytest -q          # 53 tests
docker compose -f infra/docker-compose.yml run --rm cad-worker python tests/gen_golden_steps.py
```

Golden pairs live in `tests/golden/<case>/rev_{A,B}.step` (+ `case.json`) and are byte-reproducible
(pinned STEP header timestamp + explicit product names).
