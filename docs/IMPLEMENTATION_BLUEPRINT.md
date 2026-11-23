# RevGate (레브게이트) — 개발 직전 최종 기술 설계서 (Implementation Blueprint)

본 문서는 사이드 제미나이(`4 Neon · 높음`)와의 기술 상의를 통해, **코드 작성 직전 단계에서 모호함 없이 바로 `git init` 후 구현에 착수할 수 있도록 100% 확정한 아키텍처·계약·DB·API·알고리즘 수치·프론트엔드·테스트 사양**을 통합한 최종 설계서입니다. (제품 기획·비즈니스·GTM 배경은 [revgate_product_spec.md](file:///C:/Users/dbdnj/.gemini/antigravity/brain/b4b890fc-0171-4ff8-a2f6-9e87bad20971/revgate_product_spec.md) 참조)

---

## 1. 모노레포 구조 & 로컬/런타임 스택

### 1.1. 패키지 매니저 및 SSOT 계약 동기화
- **JS/TS:** `pnpm` (v9+, Workspace)
- **Python:** `uv` (`uv.lock` 기반 완전 재현성 확보)
- **단일 진실 공급원 (`packages/contracts`):** JSON Schema를 SSOT로 두고 `pnpm codegen` 실행 시 TypeScript(`json-schema-to-typescript`)와 Pydantic v2(`datamodel-code-generator`) 모델을 동시 생성.

```text
revgate/
├── apps/
│   ├── web/                  # Next.js 15 (App Router) + R3F (Three.js) + Tailwind
│   └── api/                  # FastAPI + SQLAlchemy 2.0 (asyncpg) + Alembic + ARQ Client
├── workers/
│   └── cad/                  # ARQ Worker + 1-Job-1-Subprocess OCP Engine (Dockerfile)
├── packages/
│   └── contracts/
│       ├── schemas/          # analytical_meta.schema.json, diff_manifest.schema.json
│       ├── ts/               # generated TS types
│       └── py/               # generated Pydantic v2 models
├── infra/
│   ├── docker-compose.yml    # Postgres 16, Redis 7, MinIO(+init), CAD Worker
│   └── minio-init.sh         # 3개 버킷 자동 생성 + localhost:3000 CORS 설정
├── pnpm-workspace.yaml
└── Makefile                  # make dev, make codegen, make test-cad
```

### 1.2. 로컬 인프라 (`docker-compose.yml`) & CAD 워커 Dockerfile
- `postgres:16-alpine`, `redis:7-alpine`, `minio/minio`는 Docker로 실행하고, `api`와 `web`은 빠른 HMR을 위해 호스트에서 실행.
- `minio/mc` 초기화 컨테이너로 프로덕션 Cloudflare R2와 동일한 3개 버킷(`revgate-raw-cad`, `revgate-viewer-derived`, `revgate-audit-proofs`)과 CORS 정책을 자동 세팅.
- `cad-worker` 컨테이너: `tmpfs: /tmp/revgate_scratch:size=1G`, `mem_limit: 2g` 강제.
- **Python CAD 런타임:** `linux/amd64` + `python:3.11-slim-bookworm` + `cadquery-ocp` (`pip/uv`). 헤드리스 환경 OCCT 링크용 필수 시스템 패키지 설치:

```dockerfile
FROM --platform=linux/amd64 python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1 \
    libgomp1 libexpat1 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*
```

---

## 2. 3D GLB 파이프라인 & 데이터 계약 (Data Contracts)

### 2.1. `_FACE_ID` 내장 1-Primitive GLB (탈락: `gltfpack`)
- `gltfpack`은 압축 과정에서 삼각형 인덱스를 재정렬하고 커스텀 속성을 삭제해 `face_map`이 깨지므로 **MVP에서 제거**.
- **확정 파이프라인:** Python(`pygltflib` + `numpy`)으로 **1-Primitive GLB 직접 빌드** → 각 정점에 커스텀 속성 `_FACE_ID` (`FLOAT32`, 정수 1,677만까지 오차 0) 내장 + `COLOR_0`에 원본 CAD 면 색상 보존 → R2 업로드 시 **Brotli(`br`) 압축** 서빙.
- **이점:** 별도 `face_map.bin` 불필요, R3F에서 `hit.face.a` → `attributes._face_id.getX(a)`로 **O(1) 피킹** 및 1 Draw Call 60fps 렌더링.
- **글로벌 좌표·단위 불변 규칙:** 전 파이프라인 **mm 고정**(`xstep.cascade.unit=MM`), **Z-up 우측 좌표계 통일**(Three.js 카메라 `up=[0,0,1]`), `transform_4x4`는 **Row-Major `Target(Rev B) -> Base(Rev A)`**.

### 2.2. `analytical_meta.json` 스키마 (리비전당 1개)
```typescript
interface AnalyticalMeta {
  schema_version: "1.0.0";
  revision_id: string;
  source_unit: "MM" | "INCH" | "UNKNOWN";
  summary: {
    bbox_min: [number, number, number];
    bbox_max: [number, number, number];
    volume_mm3: number;
    face_count: number;
    is_watertight: boolean;
    warnings: string[]; // e.g. ["OPEN_SHELL_ONLY", "INCH_SOURCE_CONVERTED"]
  };
  faces: Array<{
    face_id: number;
    surface_type: "PLANE" | "CYLINDER" | "CONE" | "SPHERE" | "TORUS" | "BSPLINE" | "OTHER";
    area_mm2: number;
    centroid: [number, number, number];
    bbox_min: [number, number, number];
    bbox_max: [number, number, number];
    lod0_tri_range: [number, number]; // [start_tri, tri_count]
    adjacent_face_ids: number[];
    params:
      | { type: "PLANE"; origin: Vec3; normal: Vec3 } // Face Orientation 반영 바깥쪽 단위 법선
      | { type: "CYLINDER"; axis_origin: Vec3; axis_dir: Vec3; radius_mm: number; height_mm: number; sweep_deg: number; is_internal: boolean }
      | { type: "CONE"; axis_origin: Vec3; axis_dir: Vec3; half_angle_deg: number; r_min: number; r_max: number; is_internal: boolean }
      | { type: "OTHER"; sample_normal: Vec3 };
  }>;
}
```

### 2.3. `diff_manifest.json` 스키마 (DiffRun당 1개)
```typescript
interface DiffManifest {
  schema_version: "1.0.0";
  diff_run_id: string;
  base_revision_id: string;
  target_revision_id: string;
  alignment: {
    method: "IDENTITY" | "AUTO_BREP_RANSAC" | "MANUAL_321";
    transform_4x4: number[]; // length 16, Row-Major Target->Base
    confidence: number;      // 0..1 (inlier area ratio)
    rms_error_mm: number;
    requires_manual_review: boolean;
  };
  summary: {
    geometry_identical: boolean;
    total_changes: number;
    volume_delta_mm3: number;
    boolean_status: "OK" | "FALLBACK_HEATMAP" | "SKIPPED";
  };
  face_status_map: {
    base:   { unchanged: number[]; modified: number[]; removed: number[] };
    target: { unchanged: number[]; modified: number[]; added: number[] };
  };
  changes: Array<{
    change_id: string;
    category: "HOLE_DIAMETER" | "HOLE_POSITION" | "PLANAR_OFFSET" | "WALL_THICKNESS" | "VOLUME_DELTA";
    severity: "CRITICAL" | "MAJOR" | "INFO";
    title: string;
    fact: Record<string, number | string>; // 100% 기하 팩트
    engineering_hint?: { text: string; confidence: "HIGH" | "LOW" }; // 참고 추정 분리
    base_face_ids: number[];
    target_face_ids: number[];
    anchor_xyz: [number, number, number];
    camera_focus: { target: Vec3; suggested_dir: Vec3; radius_mm: number };
  }>;
  artifacts: { delta_added_glb: string | null; delta_removed_glb: string | null };
}
```

---

## 3. PostgreSQL 16 스키마 (11개 테이블) & 상태 머신

- **PK 정책:** `audit_events`(`BIGSERIAL`) 제외 전부 앱 레벨 생성 **UUIDv7** 통일.
- **Enum 회피:** `ALTER TYPE` 마이그레이션 잠금 방지를 위해 `TEXT` + `CHECK` 제약 사용.

```sql
-- 1. 워크스페이스 (보안/파기 정책 단위)
CREATE TABLE workspaces (
  id             UUID PRIMARY KEY,
  name           TEXT NOT NULL,
  plan           TEXT NOT NULL DEFAULT 'FREE' CHECK (plan IN ('FREE','PRO','TEAM','ENTERPRISE')),
  security_mode  TEXT NOT NULL DEFAULT 'TTL_VAULT' CHECK (security_mode IN ('TTL_VAULT','ZERO_RETENTION')),
  retention_days INT NOT NULL DEFAULT 30,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. 내부 사용자 (발주처 설계자 / 공장주)
CREATE TABLE users (
  id           UUID PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  email        TEXT NOT NULL UNIQUE,
  name         TEXT NOT NULL,
  role         TEXT NOT NULL DEFAULT 'ENGINEER' CHECK (role IN ('OWNER','ENGINEER','VIEWER')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_ws ON users(workspace_id);

-- 3. 가공업체 무료 도면 접수함 (PLG 허브)
CREATE TABLE shop_inboxes (
  id             UUID PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  slug           TEXT NOT NULL UNIQUE,               -- revgate.io/shop/{slug}
  shop_name      TEXT NOT NULL,
  owner_contact  TEXT NOT NULL,
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. 부품 (도번 단위)
CREATE TABLE parts (
  id               UUID PRIMARY KEY,
  workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  shop_inbox_id    UUID REFERENCES shop_inboxes(id) ON DELETE SET NULL,
  part_number      TEXT NOT NULL,
  name             TEXT NOT NULL,
  default_material TEXT,
  default_finish   TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_parts_ws_num ON parts(workspace_id, part_number);

-- 5. 리비전 (STEP 원본 + 파생 뷰어 번들)
CREATE TABLE revisions (
  id              UUID PRIMARY KEY,
  part_id         UUID NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
  uploaded_by     UUID REFERENCES users(id) ON DELETE SET NULL,
  rev_label       TEXT NOT NULL,
  file_sha256     TEXT NOT NULL,
  file_size_bytes BIGINT NOT NULL,
  raw_r2_key      TEXT,                              -- 파기 시 NULL (raw_purged_at과 직교 관리)
  raw_purged_at   TIMESTAMPTZ,
  viewer_prefix   TEXT,                              -- lod0.glb, lod1.glb, analytical_meta.json 경로
  spec_card_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
  status          TEXT NOT NULL DEFAULT 'PENDING_UPLOAD'
                  CHECK (status IN ('PENDING_UPLOAD','QUEUED','PARSING','READY','FAILED')),
  error_code      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_revisions_part ON revisions(part_id, created_at DESC);
CREATE INDEX idx_revisions_sha  ON revisions(part_id, file_sha256);

-- 6. Diff 실행
CREATE TABLE diff_runs (
  id               UUID PRIMARY KEY,
  part_id          UUID NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
  base_rev_id      UUID NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
  target_rev_id    UUID NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
  align_method     TEXT NOT NULL DEFAULT 'IDENTITY' CHECK (align_method IN ('IDENTITY','AUTO_BREP_RANSAC','MANUAL_321')),
  transform_4x4    DOUBLE PRECISION[16] NOT NULL,
  align_confidence DOUBLE PRECISION,
  manifest_r2_key  TEXT,
  status           TEXT NOT NULL DEFAULT 'QUEUED'
                   CHECK (status IN ('QUEUED','COMPUTING','READY','NEEDS_ALIGN','FAILED')),
  error_code       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_diff_runs_pair ON diff_runs(part_id, base_rev_id, target_rev_id);

-- 7. 변경점 체크리스트 항목
CREATE TABLE diff_items (
  id                 UUID PRIMARY KEY,
  diff_run_id        UUID NOT NULL REFERENCES diff_runs(id) ON DELETE CASCADE,
  change_key         TEXT NOT NULL,
  category           TEXT NOT NULL,
  severity           TEXT NOT NULL CHECK (severity IN ('CRITICAL','MAJOR','INFO')),
  title              TEXT NOT NULL,
  fact_json          JSONB NOT NULL,
  hint_text          TEXT,
  anchor_xyz         DOUBLE PRECISION[3] NOT NULL,
  checked_at         TIMESTAMPTZ,
  checked_by_contact TEXT
);
CREATE INDEX idx_diff_items_run ON diff_items(diff_run_id);

-- 8. 외부 공유 링크
CREATE TABLE review_links (
  id                  UUID PRIMARY KEY,
  diff_run_id         UUID NOT NULL REFERENCES diff_runs(id) ON DELETE CASCADE,
  created_by          UUID NOT NULL REFERENCES users(id),
  token_hash          TEXT NOT NULL UNIQUE,          -- SHA-256(32-byte random token)
  recipient_name      TEXT NOT NULL,
  recipient_contact   TEXT NOT NULL,
  allow_raw_download  BOOLEAN NOT NULL DEFAULT FALSE,
  status              TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','APPROVED','REVOKED','EXPIRED')),
  expires_at          TIMESTAMPTZ NOT NULL,
  approved_at         TIMESTAMPTZ,
  approval_pdf_r2_key TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_review_links_run ON review_links(diff_run_id);

-- 9. 외부 비로그인 6자리 OTP & 쿠키 세션
CREATE TABLE otp_sessions (
  id                 UUID PRIMARY KEY,
  review_link_id     UUID NOT NULL REFERENCES review_links(id) ON DELETE CASCADE,
  contact            TEXT NOT NULL,
  otp_hash           TEXT NOT NULL,                  -- HMAC-SHA256(6-digit OTP)
  attempts           INT NOT NULL DEFAULT 0,         -- 5회 실패 시 잠금
  otp_expires_at     TIMESTAMPTZ NOT NULL,           -- 5분
  session_token_hash TEXT UNIQUE,                    -- HttpOnly 쿠키 rg_ext_sid 해시
  verified_at        TIMESTAMPTZ,
  session_expires_at TIMESTAMPTZ                     -- 검증 후 24시간
);
CREATE INDEX idx_otp_link ON otp_sessions(review_link_id, contact);

-- 10. 3D 면 고정 핀 코멘트
CREATE TABLE pin_comments (
  id             UUID PRIMARY KEY,
  diff_run_id    UUID NOT NULL REFERENCES diff_runs(id) ON DELETE CASCADE,
  diff_item_id   UUID REFERENCES diff_items(id) ON DELETE SET NULL,
  review_link_id UUID REFERENCES review_links(id) ON DELETE SET NULL,
  author_type    TEXT NOT NULL CHECK (author_type IN ('INTERNAL','EXTERNAL')),
  author_label   TEXT NOT NULL,
  face_id        INT NOT NULL,
  rev_side       TEXT NOT NULL CHECK (rev_side IN ('BASE','TARGET')),
  pin_xyz        DOUBLE PRECISION[3] NOT NULL,
  body           TEXT NOT NULL,
  resolved_at    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pins_run ON pin_comments(diff_run_id, created_at);

-- 11. 불변 감사 로그 (App DB Role에서 UPDATE/DELETE 권한 REVOKE)
CREATE TABLE audit_events (
  id             BIGSERIAL PRIMARY KEY,
  workspace_id   UUID NOT NULL REFERENCES workspaces(id),
  entity_type    TEXT NOT NULL,
  entity_id      UUID NOT NULL,
  actor_contact  TEXT NOT NULL,
  action         TEXT NOT NULL,
  ip_hash        TEXT,
  payload_sha256 TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_ws ON audit_events(workspace_id, created_at DESC);
```

### 상태 전이 규칙
- **`RevisionStatus`:** `PENDING_UPLOAD → QUEUED → PARSING → READY | FAILED` (`raw_purged_at`으로 원본 파기 여부 직교 추적)
- **`DiffRunStatus`:** `QUEUED → COMPUTING → READY | NEEDS_ALIGN(confidence < 0.5) | FAILED`. `NEEDS_ALIGN`/`READY`에서 3-2-1 수동 정합 요청 시 `COMPUTING`으로 재전이.
- **`ReviewLinkStatus`:** `ACTIVE → APPROVED | REVOKED | EXPIRED` (모두 Terminal State). `APPROVED` 시 스냅샷 PDF 생성.

---

## 4. REST API & SSE 엔드포인트 명세 (26개)

- **내부 인증:** `HttpOnly + Secure + SameSite=Lax` 쿠키 `rg_sid` (Magic Link, TTL 7일)
- **외부 리뷰어 인증:** `HttpOnly` 쿠키 `rg_ext_sid` (단일 `review_link_id` 스코프, TTL 24시간)
- **파일 접근:** 모든 GLB·JSON·PDF는 15분 만료 R2 Signed GET URL로 직접 스트리밍

```text
[1. 발주처 인증 (Internal Auth)]
POST   /v1/auth/magic-link                     # Req: { email } -> Res: { sent: true }
POST   /v1/auth/verify                         # Req: { token } -> Set-Cookie: rg_sid (HttpOnly)
GET    /v1/auth/me                             # Res: { user, workspace }
POST   /v1/auth/logout                         # Clear-Cookie: rg_sid

[2. 부품 & R2 Presigned 업로드 / 파싱 트리거]
POST   /v1/parts                               # Req: { part_number, name, default_material?, default_finish? } -> Res: { part }
GET    /v1/parts                               # Res: { items: Part[] }
POST   /v1/parts/{part_id}/revisions/presign   # Req: { rev_label, filename, file_size_bytes, file_sha256 } -> Res: { revision_id, put_url, r2_key }
POST   /v1/revisions/{rev_id}/complete         # Req: { spec_card_json? } -> R2 검증 후 q-ingest 투입 -> Res: { status: "QUEUED" }
GET    /v1/revisions/{rev_id}                  # Res: { revision, signed_urls?: { lod0, lod1, meta } }

[3. Diff 생성 / SSE / 3-2-1 재정합]
POST   /v1/parts/{part_id}/diffs               # Req: { base_rev_id, target_rev_id } -> q-diff-batch 투입 -> Res: { diff_run_id, status }
GET    /v1/diffs/{diff_id}                     # Res: { diff_run, items[], signed_urls }
GET    /v1/diffs/{diff_id}/events              # SSE: progress | ready | needs_align | failed
POST   /v1/diffs/{diff_id}/realign             # Req: { transform_4x4: float[16] } -> q-diff-interactive 투입 -> Res: { status: "COMPUTING" }

[4. 공유 링크 & 외부 6자리 OTP]
POST   /v1/diffs/{diff_id}/links               # Req: { recipient_name, recipient_contact, expires_in_days, allow_raw_download } -> Res: { link_id, share_url }
DELETE /v1/links/{link_id}                     # 링크 즉시 폐기 (REVOKED)
GET    /v1/public/links/{token}/meta           # 무인증(OG카드용): { part_number, rev_pair, change_count, masked_contact, status }
POST   /v1/public/links/{token}/otp/request    # 6자리 OTP 발송 (10분 3회 제한) -> Res: { ttl_sec: 300 }
POST   /v1/public/links/{token}/otp/verify     # Req: { code } -> Set-Cookie: rg_ext_sid (HttpOnly, 24h) -> Res: { verified: true }
GET    /v1/public/links/{token}/bundle         # Cookie(rg_ext_sid) 필수 -> Res: { diff_run, items[], pins[], signed_urls(15m), watermark_text }

[5. 변경점 체크 & 3D 핀 코멘트 (rg_sid 또는 rg_ext_sid 허용)]
PATCH  /v1/review/diff-items/{item_id}/check   # Req: { checked: bool } -> audit_events 기록
GET    /v1/review/diffs/{diff_id}/pins         # Res: { pins[] }
POST   /v1/review/diffs/{diff_id}/pins         # Req: { diff_item_id?, face_id, rev_side, pin_xyz, body } -> Res: { pin }
PATCH  /v1/review/pins/{pin_id}/resolve        # Req: { resolved: bool } -> Res: { pin }

[6. 가공 착수 승인 & 증빙 PDF]
POST   /v1/public/links/{token}/approve        # Cookie(rg_ext_sid) 필수 -> Link=APPROVED 락 + WeasyPrint PDF 생성 -> Res: { approved_at, pdf_signed_url }
GET    /v1/links/{link_id}/approval-pdf        # 발주처 내부용 -> Res: { pdf_signed_url }

[7. 공장 전용 무료 도면 접수함 (Shop Inbox PLG)]
POST   /v1/shop-inboxes                        # Req: { slug, shop_name, owner_contact } -> Res: { inbox, public_url }
GET    /v1/public/shop/{slug}                  # 무인증 -> Res: { shop_name, is_active }
POST   /v1/public/shop/{slug}/submissions/presign  # Req: { sender_name, sender_contact, part_number, files: [v1_meta, v2_meta] }
POST   /v1/public/shop/{slug}/submissions/complete # Req: { submission_id, spec_card_json? } -> 자동 Part/Rev/DiffRun 생성
```

---

## 5. `workers/cad` OCP 엔진 수치 사양, 폴백 & 에러 코드

### 5.1. STEP 로드, `face_id` 할당 & 테셀레이션
- **단위 정규화:** 로드 전 `Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")` 필수. `reader.ReadFile(path)` 직후 `reader.FileUnits(...)`로 원본 단위 탐지 (`INCH` 시 경고 `INCH_SOURCE_CONVERTED`).
- **`face_id` 결정론적 할당:** `TopExp_Explorer` 중복 순회 금지. `TopTools_IndexedMapOfShape` + `TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)`로 유일 면 1-based 인덱싱 후 `face_id = idx - 1` (0-based) 부여.
- **LOD0 → LOD1 캐시 초기화:** `lod0` GLB 생성 직후 반드시 `BRepTools.Clean_s(shape)` 호출 후 `lod1` 테셀레이션 실행 (미호출 시 OCCT가 기존 메쉬 재사용).

```python
# workers/cad/src/tessellate.py
import math

def get_lod_params(bbox_diag_mm: float, lod: str) -> tuple[float, float]:
    """returns (linear_deflection_mm, angular_deflection_rad), isRelative=False"""
    if lod == "lod0":
        return max(0.03, min(0.10, bbox_diag_mm * 0.0005)), math.radians(12.0)
    return max(0.15, min(0.50, bbox_diag_mm * 0.0025)), math.radians(25.0)
```

### 5.2. `constants.py` 기하 상수 & 자동 정합
```python
# workers/cad/src/constants.py
FACE_MATCH_DIST_MM     = 0.010   # 동일 면(Unchanged) 판정 거리 오차 상한
CHANGE_REPORT_MIN_MM   = 0.020   # 실무 변경 리포트 최소 치수 변화 (노이즈 차단)
ANGLE_TOL_DEG          = 0.50    # 법선/원통축 평행·동축 판정 허용각
AREA_RATIO_TOL         = 0.02    # 미변경 면 판정 시 면적 오차 허용비 (2%)
AXIS_COAXIAL_TOL_MM    = 0.050   # 동축 홀 쌍 매칭 중심축 거리 오차
HOLE_SWEEP_MIN_DEG     = 355.0   # 완전체 원통 홀 판정 최소 회전각

FAST_PATH_INLIER_MIN   = 0.60    # Stage 0 항등행렬 통과 기준 (면적비 60%+)
RANSAC_MAX_HYPOTHESES  = 500
ALIGN_CONFIDENCE_MIN   = 0.50    # 미만 시 NEEDS_ALIGN 전환
ALIGN_RMS_MAX_MM       = 0.020   # SVD 정밀 스냅 후 허용 최대 RMS 오차

WALL_CRITICAL_MM       = 1.00    # CNC 알루미늄 채터링/변형 CRITICAL 경고
WALL_MAJOR_MM          = 2.00    # < 2.0mm 이면서 30%+ 감소 시 MAJOR 경고
WALL_REDUCTION_RATIO   = 0.30

ISO_METRIC_TAP_DRILL_MM = {
    "M2": (1.60, 0.40), "M2.5": (2.05, 0.45), "M3": (2.50, 0.50),
    "M3.5": (2.90, 0.60), "M4": (3.30, 0.70), "M5": (4.20, 0.80),
    "M6": (5.00, 1.00), "M8": (6.80, 1.25), "M10": (8.50, 1.50),
    "M12": (10.20, 1.75), "M14": (12.00, 2.00), "M16": (14.00, 2.00),
    "M18": (15.50, 2.50), "M20": (17.50, 2.50),
}
TAP_DRILL_MATCH_TOL_MM = 0.08

MAX_SOLIDS_MVP         = 10
MAX_FACES_HARD         = 15000
THREAD_BOMB_FACES      = 3000    # BSPLINE > 50% + 소면적(<5mm²) 다발
BOOLEAN_TIMEOUT_SEC    = 15.0
SUBPROCESS_TIMEOUT_SEC = 60.0
```
- **RANSAC 후보 생성:** (1순위) 면적 상위 15개 평면 + 수직 원통 홀 축 직교 프레임 → (2순위) 비평행 상위 평면 3개 교점 프레임 → (3순위) 평면·원통 30% 미만 자유곡면 시 OBB 24방향 + 2,000점 Trimmed ICP.
- **`align_confidence`:** $\frac{\sum_{f \in \text{InlierBaseFaces}} \text{Area}(f)}{\min(\text{TotalArea}_{\text{base}}, \text{TotalArea}_{\text{target}})}$. 0.50 이상이면 Inlier 기반 SVD(Kabsch) 정밀 스냅, 미만이면 `NEEDS_ALIGN`.

### 5.3. 더러운 STEP 방어 & 표준 `error_code` (`errors.py`)
- `BRepCheck_Analyzer(shape, True).IsValid()` 실패 또는 `is_watertight == False` 시 `ShapeFix_Shape` 1회 실행 (`SetPrecision(1e-3)`, `SetMaxTolerance(0.1)` → 경고 `HEALING_APPLIED`).
- 여전히 Open Shell이면 Face-to-Face Diff는 정상 수행하되 Boolean `Cut`은 스킵(`OPEN_SHELL_ONLY`), 나사산 폭탄 또는 15초 타임아웃 초과 시 `lod0` 5,000점 면적 가중 샘플링 + `scipy.spatial.cKDTree` 편차 히트맵으로 우아한 폴백(`BOOLEAN_FALLBACK_HEATMAP`).

```python
# workers/cad/src/errors.py
EXIT_CODE_MAP = {
    -9: "OOM_KILLED", 137: "OOM_KILLED",
    -11: "SEGFAULT_OCCT", 139: "SEGFAULT_OCCT",
    -6: "SEGFAULT_OCCT", 134: "SEGFAULT_OCCT",
}
# TimeoutExpired(60s) -> "TIMEOUT_WORKER_60S"
DOMAIN_ERRORS = [
    "INVALID_STEP_SYNTAX", "EMPTY_GEOMETRY", "TOO_MANY_SOLIDS",
    "TOO_MANY_FACES", "RAW_FILE_NOT_FOUND", "SHA256_MISMATCH",
]
WARNINGS = [
    "OPEN_SHELL_ONLY", "HEALING_APPLIED", "INCH_SOURCE_CONVERTED",
    "HELICAL_THREADS_DETECTED", "BOOLEAN_FALLBACK_HEATMAP",
]
```

---

## 6. 프론트엔드(`apps/web`) R3F 3D 뷰어 & 측정/보안 UI 스펙

### 6.1. `128×128 DataTexture` + `MeshStandardMaterial.onBeforeCompile`
- **텍스처 규격:** `128 × 128` 2D `THREE.DataTexture` (`RGBAFormat`, `UnsignedByteType`, `NearestFilter`, `generateMipmaps=false`). 구형 PC/태블릿의 `MAX_TEXTURE_SIZE=4096` 제약을 피하면서 최대 16,384 Face를 **1 Draw Call**로 수용.
- **RGBA 매핑:** `RGB` = 상태/히트맵 색상, `A` = `0`(원본)/`1~254`(블렌드)/`255`(완전 하이라이트).
- **Material:** 스튜디오 IBL 음영 보존을 위해 `MeshStandardMaterial.onBeforeCompile` 사용 (`customProgramCacheKey = () => "revgate-face-lut-v1"`). 고스트 모드 시 미변경 면 `alpha=0.16` + 투명 패스(`depthWrite: false`).

```glsl
// Fragment 주입 (#include <color_fragment> 직후)
int fid = int(vFaceId + 0.5);
vec2 lutUv = (vec2(float(fid & 127), float(fid >> 7)) + 0.5) / 128.0;
vec4 lut = texture2D(uFaceLut, lutUv);
if (lut.a > 0.01) {
  diffuseColor.rgb = mix(diffuseColor.rgb, lut.rgb, lut.a);
}
```

### 6.2. 브라우저 TS 해석적 측정 공식 (`apps/web/src/lib/measure.ts`)
- `PAR_COS = cos(0.5°)`, `PERP_SIN = sin(0.5°)`. `analytical_meta.json`의 `params`를 직접 계산:
  - **CYLINDER ↔ CYLINDER:** 평행(`|dot(d1, d2)| >= PAR_COS`)이면 중심축 수직거리 `length((p2-p1) - dot(p2-p1, d1)*d1)` (`<0.01mm` 시 `동축(Coaxial)` 배지). 비평행이면 교차각 + 최단 꼬임거리.
  - **PLANE ↔ PLANE:** 평행이면 수직거리 `|dot(o2-o1, n1)|`. 비평행이면 이면각 + 보각(`180 - θ`).
  - **CYLINDER ↔ PLANE:** 축-면 평행(`|dot(d_cyl, n_pln)| <= PERP_SIN`)이면 축-면 거리 및 **최소 벽간격(`축-면 거리 - radius_mm`)**. 수직이면 `직교(90.0°)` + 중심점 거리.
  - **BSPLINE / OTHER 포함 시:** Raycaster 3D 클릭 좌표 간 유클리드 거리 + `"메쉬 근사치 (±0.05mm)"` 배지 강제 표기.

### 6.3. 3D 핀, 카메라 애니메이션 & 동적 워터마크
- **3D 핀:** `@react-three/drei` `<Html position={pin_xyz}>`. 매 프레임 가려짐(Occlusion) 체크로 뒷면 핀은 `opacity: 0.25`.
- **카메라 줌인:** `OrbitControls.up = [0, 0, 1]` 고정. 변경점 클릭 시 `450ms easeOutCubic`으로 `target → focus.target`, `position → focus.target + focus.suggested_dir * (focus.radius_mm * 2.8)` 보간 (유저 조작 `start` 시 즉시 취소).
- **동적 워터마크:** `position: fixed; inset: 0; pointer-events: none; z-index: 40` SVG 타일(`-28deg`, `opacity: 0.07`, `{업체명} · {마스킹연락처} · {YYYY-MM-DD HH:mm} · {diff_short_id}`). `MutationObserver`로 DOM 삭제·숨김 감지 시 캔버스 즉시 블러 처리.

---

## 7. Golden STEP 테스트 스위트 (5종) & Day-1 착수 체크리스트

### 7.1. Golden STEP 5종 (`workers/cad/tests/gen_golden_steps.py`)
Python `OCP` (`BRepPrimAPI` + `BRepAlgoAPI_Cut` + `gp_Trsf`)로 결정론적 테스트 쌍 5종 자동 생성:
1. `01_hole_diam`: 100×60×20mm 블록의 Ø3.30mm 관통홀 4개 중 2개를 Ø4.20mm로 변경 → `HOLE_DIAMETER` 2건 + ISO `M4 → M5` 추정 힌트 검증.
2. `02_pocket_offset`: 중앙 50×30mm 포켓 깊이 8.0 → 10.5mm(+2.5mm) 변경 → `PLANAR_OFFSET` 1건 + 체적 변화 `-3750.0 mm³` (±1mm³) 검증.
3. `03_transform_shift`: `01_hole_diam` Rev B에 이동 `(120, -45, 80)mm` + 회전 `(90°, 0°, 37°)` 적용 → `AUTO_BREP_RANSAC`, `confidence ≥ 0.85`, `rms_error < 0.005mm`, 동일 홀 2건 검출 검증.
4. `04_thin_wall`: 포켓 외벽 두께 2.5 → 0.8mm 축소 → `WALL_THICKNESS` `CRITICAL`(<1.0mm) 경보 검증.
5. `05_symmetric_needs_align`: 대칭 50×50×50mm 정육면체 대폭 변경(일치 면적 < 40%) → 자동 정합 거절 및 `NEEDS_ALIGN` 전환 검증.

### 7.2. Day-1 개발 착수 체크리스트
1. **모노레포 초기화 & 로컬 인프라 기동:** `git init`, `pnpm` workspace 구성 후 `infra/docker-compose.yml`로 Postgres 16 · Redis 7 · MinIO(3개 버킷 + CORS 자동 초기화) 기동.
2. **SSOT 데이터 계약(`packages/contracts`) 고정:** `analytical_meta.schema.json`과 `diff_manifest.schema.json` 작성 후 `pnpm codegen`으로 TS 타입·Pydantic v2 모델 자동 생성 검증.
3. **CAD 워커 런타임 빌드 & Golden STEP 5종 생성:** `python:3.11-slim` + `uv` + `cadquery-ocp` Dockerfile 빌드 확인 후 `gen_golden_steps.py`로 5개 테스트 시나리오 STEP 쌍 출력.
4. **STEP 인제스트 & 1-Primitive `_FACE_ID` GLB 빌더 구현:** `TopTools_IndexedMapOfShape` 0-based `face_id` 할당, `BRepTools.Clean_s` 호출 후 LOD0/1 GLB 및 `analytical_meta.json` CLI 생성 검증.
5. **RANSAC 자동 정합 + B-Rep Diff 엔진 `pytest` 통과:** Golden STEP 5종 회귀 테스트와 1-Job-1-Subprocess 격리/에러 코드 매핑까지 그린 확인.
6. **API 뼈대 & Alembic 초기 마이그레이션 적용:** 확정된 11개 테이블 DDL을 `apps/api` Alembic 첫 리비전으로 반영하고 `GET /health` 및 MinIO Presigned URL 발급 동작 확인.
