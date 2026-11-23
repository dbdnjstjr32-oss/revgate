# RevGate (레브게이트)

링크 하나로 3D 형상 변경점을 정밀 대조·합의·승인하는 하드웨어 외주 발주용 리비전 게이트.
제품 기획은 [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md), 확정 기술 사양은
[docs/IMPLEMENTATION_BLUEPRINT.md](docs/IMPLEMENTATION_BLUEPRINT.md).

## 구조 (Phase 1)

```text
revgate/
├── workers/cad/            # 1-Job-1-Subprocess OCP(OCCT) 엔진 — STEP ingest, _FACE_ID GLB, 자동 정합, B-Rep Diff
├── packages/contracts/     # SSOT: schemas/*.schema.json -> ts/ (TypeScript) + py/revgate_contracts (Pydantic v2)
├── infra/                  # docker-compose (Postgres 16, Redis 7, MinIO + 버킷/CORS init, cad-worker)
├── scripts/validate-glb.mjs# Khronos glTF-Validator로 GLB 계약 검증
├── pnpm-workspace.yaml
└── Makefile                # make dev | codegen | test-cad
```

`apps/api`(FastAPI + Alembic)와 `apps/web`(Next.js + R3F)는 다음 단계에서 추가합니다.

## 빠른 시작

```sh
make dev        # Postgres :5432, Redis :6379, MinIO :9000/:9001 + 3 buckets + CORS(localhost:3000)
make codegen    # JSON Schema -> TS/Pydantic 생성 + ajv/tsc/pydantic 검증
make test-cad   # linux/amd64 워커 이미지 빌드 후 Golden STEP 5종 + 엔진 테스트 (실제 OCP)
```

`make`가 없는 Windows 셸에서는 같은 명령을 직접 실행하면 됩니다.

```sh
docker compose -f infra/docker-compose.yml up -d
pnpm install && pnpm codegen
docker compose -f infra/docker-compose.yml run --rm cad-worker pytest -q
```

### CAD CLI (week-1 데모)

```sh
docker compose -f infra/docker-compose.yml run --rm cad-worker \
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
