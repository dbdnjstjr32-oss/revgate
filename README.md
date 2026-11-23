# RevGate (레브게이트)

링크 하나로 3D 형상 변경점을 정밀 대조·합의·승인하는 하드웨어 외주 발주용 리비전 게이트.
제품 기획은 [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md), 확정 기술 사양은
[docs/IMPLEMENTATION_BLUEPRINT.md](docs/IMPLEMENTATION_BLUEPRINT.md).

## 구조

```text
revgate/
├── apps/api/               # FastAPI + SQLAlchemy 2.0(asyncpg) + Alembic + ARQ client + WeasyPrint (§3, §4)
├── workers/cad/            # OCP(OCCT) 엔진 + ARQ 워커 (q-ingest / q-diff-batch / q-diff-interactive / q-purge)
├── packages/contracts/     # SSOT: schemas/*.schema.json -> ts/ (TypeScript) + py/revgate_contracts (Pydantic v2)
├── infra/                  # docker-compose: Postgres 16, Redis 7, MinIO(+init), api, cad-worker, api-tests
├── scripts/validate-glb.mjs# Khronos glTF-Validator로 GLB 계약 검증
├── pnpm-workspace.yaml
└── Makefile
```

## 빠른 시작

```sh
make dev        # Postgres :5432, Redis :6379, MinIO :9000/:9001 + 3 buckets + CORS(localhost:3000)
make stack      # + api :8000 (시작 시 alembic upgrade head) + cad-worker(ARQ 4 queues)
make codegen    # JSON Schema -> TS/Pydantic 생성 + ajv/tsc/pydantic 검증
make test-cad   # Golden STEP 5종 + 엔진 테스트 (linux/amd64, 실제 OCP)
make test-api   # E2E: 실제 api + cad-worker + Postgres + Redis + MinIO (mock 없음)
```

`make`가 없는 Windows 셸에서는 compose를 직접 호출합니다.

```sh
docker compose -f infra/docker-compose.yml --profile app up -d          # 전체 스택
docker compose -f infra/docker-compose.yml --profile test run --rm api-tests pytest -q
docker compose -f infra/docker-compose.yml --profile cad run --rm --no-deps cad-worker pytest -q
```

compose profile: (없음) 인프라만 · `app` = + api + cad-worker · `test` = + api-tests · `cad` = cad-worker 단독.

## 백엔드 흐름 (Phase 2)

```text
browser ──presign──▶ api ──▶ (signed PUT 10m, ≤50MB) ──▶ MinIO/R2 revgate-raw-cad
        ──complete─▶ api: SHA-256 재검증 → revisions.QUEUED → ARQ q-ingest
cad-worker(ARQ): raw → tmpfs → run_job(1-Job-1-Subprocess, 60s) → GLB/JSON Brotli(Content-Encoding: br)
                 → revgate-viewer-derived → Postgres(status, diff_items, error_code) → Redis Pub/Sub
api SSE /v1/diffs/{id}/events ◀── Redis Pub/Sub (progress | ready | needs_align | failed)
```

* 정책: `diff_items`는 `diff_runs.status = READY`일 때만 존재. `NEEDS_ALIGN`은 잠정 manifest만 만들고,
  `/realign`(q-diff-interactive) 이후 READY가 되면 생성됩니다 (같은 change_key의 체크 상태는 유지).
* ZERO_RETENTION 워크스페이스: 처리가 끝나면 q-purge가 원본 STEP을 지우고 `raw_purged_at` + `RAW_PURGED`
  감사 로그를 남깁니다. 같은 `file_sha256`로 presign하면 해시 검증 재드롭(re-drop)이 시작됩니다.
* 감사 로그 `audit_events`는 append-only: 앱 롤(`revgate_app`)에서 UPDATE/DELETE/TRUNCATE를 REVOKE했고,
  트리거가 소유자 롤까지 막습니다.
* 메일/SMS 발송은 아직 provider가 없어서 로그와 Redis `rg:dev:outbox`(개발용 훅)로 나갑니다.

### API를 호스트에서 실행 (HMR)

```sh
make dev
cd apps/api && uv sync && uv run alembic upgrade head
COOKIE_SECURE=false uv run uvicorn app.main:app --reload
```

### CAD CLI (week-1 데모)

```sh
docker compose -f infra/docker-compose.yml run --rm --no-deps cad-worker \
  python -m src.cli compare tests/golden/01_hole_diam/rev_A.step tests/golden/01_hole_diam/rev_B.step \
  --out /tmp/revgate_scratch/demo
```

## 로컬 인프라 메모

* 업스트림 `minio/minio`, `minio/mc` 이미지는 Docker Hub/quay.io에서 더 이상 받을 수 없어 기본값으로
  업스트림 호환 커뮤니티 빌드(`pgsty/minio`, `pgsty/mc`, RELEASE 태그 고정)를 씁니다. `MINIO_IMAGE` /
  `MC_IMAGE`로 교체 가능.
* 이 MinIO 빌드는 버킷 단위 `PutBucketCors`를 지원하지 않아 서버 전역 `api.cors_allow_origin`
  (`MINIO_API_CORS_ALLOW_ORIGIN`)으로 `http://localhost:3000`만 허용합니다.
* `cad-worker`: `tmpfs /tmp/revgate_scratch:size=1G`, `mem_limit: 2g` + `memswap_limit: 2g`
  (swap 여유 없이 2g에서 OOM-kill → `OOM_KILLED`).
* `api-tests`는 MinIO의 네트워크 네임스페이스를 공유해서(`network_mode: service:minio`) API가 발급한
  `http://localhost:9000/...` 서명 URL을 브라우저와 똑같이 사용합니다.
