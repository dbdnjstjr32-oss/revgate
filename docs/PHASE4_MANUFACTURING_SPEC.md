# RevGate Phase 4 — 실무 신뢰성·제조 사양·공차·승인 불변성 고도화 명세서

본 문서는 드론 부품 외주 가공 현장 피드백을 반영해 사이드 제미나이와 합의·확정한 **Phase 4 구현 명세**입니다. 기존 Phase 1~3(`IMPLEMENTATION_BLUEPRINT.md`)과 100% 하위 호환을 유지하며 아래 기능을 추가합니다.

---

## 1. Alembic `0002_phase4_manufacturing_trust` DB 확장

```sql
-- 1. revisions: 사양 수정 버전 추적
ALTER TABLE revisions
  ADD COLUMN spec_version INT NOT NULL DEFAULT 1,
  ADD COLUMN spec_updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- 2. review_links: 불변 승인 스냅샷 + 구승인(Supersede) 추적 + DFM 확인
ALTER TABLE review_links
  ADD COLUMN approval_snapshot_json JSONB,
  ADD COLUMN approval_snapshot_sha256 TEXT,
  ADD COLUMN superseded_at TIMESTAMPTZ,
  ADD COLUMN superseded_reason TEXT
    CHECK (superseded_reason IN ('NEW_REVISION','SPEC_UPDATED','DRAWING_UPDATED')),
  ADD COLUMN superseded_by_revision_id UUID REFERENCES revisions(id) ON DELETE SET NULL,
  ADD COLUMN dfm_ack_json JSONB NOT NULL DEFAULT '{}'::jsonb;

-- 3. 12번째 테이블: 리비전별 2D 도면 첨부 (PDF / DXF / SPEC_SHEET)
CREATE TABLE revision_attachments (
  id              UUID PRIMARY KEY,
  revision_id     UUID NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN ('2D_PDF','2D_DXF','SPEC_SHEET')),
  filename        TEXT NOT NULL,
  content_type    TEXT NOT NULL,
  file_size_bytes BIGINT NOT NULL,
  file_sha256     TEXT NOT NULL,
  r2_key          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'PENDING_UPLOAD'
                  CHECK (status IN ('PENDING_UPLOAD','READY','DELETED')),
  uploaded_by     UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_rev_att_rev ON revision_attachments(revision_id, status);
```

---

## 2. 승인 스냅샷 불변 고정 & 구승인 경고 (`Supersede`)

### 2.1. 불변 스냅샷 생성 (`POST /v1/public/links/{token}/approve`)
- 트랜잭션 내부에서 아래 구조의 스냅샷 JSON을 조립하고 `json.dumps(snap, sort_keys=True, separators=(",", ":"))`의 SHA-256을 `approval_snapshot_sha256`에 기록한다.
- DB `review_links.status`는 영구적으로 `'APPROVED'`를 유지한다(역사적 기록 보존).
- `audit_events`에 `action = 'APPROVED'`, `payload_sha256 = approval_snapshot_sha256` 기록.
- 승인 PDF(`WeasyPrint`)는 오직 저장된 `approval_snapshot_json`만 읽어 렌더링하며 재조회 시 재계산하지 않는다.
  - 상단 봉인 헤더: `PRODUCTION APPROVAL RECORD · 승인 시각(KST/UTC) · Snapshot SHA-256`
  - 파일 무결성 지문 블록: Base/Target STEP SHA-256, 첨부 2D PDF/DXF 파일명·SHA-256, `spec_version`
  - 제조 사양 요약 + Base↔Target 사양 Diff + 3D 형상 변경 체크리스트 + DFM 체크 항목 + 미해결 핀 개수
  - 하단 법적 고지: *"본 문서의 3D 치수는 STEP 공칭 형상 기준이며, 실제 가공 공차·나사·표면처리는 첨부 2D 도면 및 제조 사양이 우선합니다."*

```json
{
  "snapshot_version": "1.0",
  "approved_at": "2026-11-24T01:30:00Z",
  "approver": { "name": "대한정밀 김사장", "contact": "010-****-1234", "ip_hash": "..." },
  "part": { "id": "...", "part_number": "DRN-ARM-01", "name": "Carbon-AL Motor Mount" },
  "base_rev": {
    "id": "...", "rev_label": "v1", "step_sha256": "...",
    "spec_version": 1, "spec_card": {},
    "attachments": [{ "id": "...", "kind": "2D_PDF", "filename": "arm_v1.pdf", "sha256": "..." }]
  },
  "target_rev": {
    "id": "...", "rev_label": "v2", "step_sha256": "...",
    "spec_version": 2, "spec_card": {},
    "attachments": [{ "id": "...", "kind": "2D_PDF", "filename": "arm_v2.pdf", "sha256": "..." }]
  },
  "diff_run": { "id": "...", "align_method": "AUTO_BREP_RANSAC", "transform_4x4": [], "manifest_sha256": "..." },
  "spec_diffs": [],
  "geometry_changes": [],
  "dfm_ack": { "process": "CNC_MILLING", "checked_ids": ["corner_r", "thin_wall", "anodizing"] },
  "missing_spec_warnings_acked": ["SURFACE_FINISH_EMPTY"],
  "unresolved_pin_count": 0
}
```

### 2.2. 구승인 무효/경고 (`Supersede`) 자동 트리거 3가지
1. **`NEW_REVISION`:** 같은 `part_id`에 새 리비전이 `READY`가 되면 기존 `status='APPROVED' AND superseded_at IS NULL` 링크 전체에 `superseded_at = now()`, `superseded_reason = 'NEW_REVISION'`, `superseded_by_revision_id = new_rev.id` 마킹.
2. **`SPEC_UPDATED`:** 이미 `APPROVED`된 링크의 `target_rev` 사양이 `PATCH /v1/revisions/{id}/spec`으로 수정되면 `spec_version += 1` + 해당 링크에 `superseded_reason = 'SPEC_UPDATED'`.
3. **`DRAWING_UPDATED`:** `target_rev`에 2D 도면 첨부가 추가(`complete`) 또는 삭제(`DELETE`)되면 `superseded_reason = 'DRAWING_UPDATED'`.

- **API/UI 표현:** DB `status: "APPROVED"`와 함께 계산 필드 `effective_status: "APPROVED" | "SUPERSEDED"`, `superseded_reason`, `latest_revision_label` 반환.
- `/r/[token]` 열람 시 `effective_status === "SUPERSEDED"`면 상단에 닫기 불가 적색 고정 배너 노출:
  *"⚠️ 구버전 승인(SUPERSEDED) — 가공 착수 금지. 이후 새 리비전(또는 사양/도면 수정)이 발생했습니다. 발주처에 최신 링크를 요청하세요."* + 원본 STEP 다운로드 버튼 자동 비활성화.

---

## 3. 구조화된 제조 사양(`SpecCardV2`) + 사양 Diff + 공차 누락 경고

Phase 1~3의 빈 `{}`과 100% 하위 호환되도록 `schema_version: "2.0"` 파서에서 기본값 보완:

```typescript
export interface SpecCardV2 {
  schema_version: "2.0";
  process: "CNC_MILLING" | "CNC_TURNING" | "PRINT_FDM" | "PRINT_SLA_SLS_MJF" | "SHEET_METAL" | "OTHER";
  material: { grade: string; standard?: string };
  heat_treatment: { required: boolean; spec?: string };
  surface_finish: {
    type: string;
    anodizing_thickness_um?: string;
    color?: string;
    ra_um?: number;
    masking_note?: string;
  };
  general_tolerance: {
    standard: "ISO_2768_fH" | "ISO_2768_mK" | "ISO_2768_cL" | "CUSTOM" | "UNSPECIFIED";
    custom_note?: string;
  };
  critical_tolerances: Array<{
    id: string;
    location: string;
    spec: string;
    datum_ref?: string;
    linked_face_id?: number;
  }>;
  threads_and_inserts: Array<{
    spec: string;
    count: number;
    depth_mm?: number;
    insert_type: "TAPPED_DIRECT" | "HELICOIL" | "HEAT_SET_BRASS" | "PEM";
    note?: string;
  }>;
  additive_spec?: {
    build_orientation: string;
    layer_height_mm?: number;
    infill_pct?: number;
    wall_loops?: number;
    support_note?: string;
  };
  order: {
    quantity?: number;
    due_date?: string; // YYYY-MM-DD
    inspection_level: "NONE" | "DIMENSIONAL_SHEET" | "CMM_REPORT";
  };
}
```

### 3.1. `compute_spec_diff` 규칙
- 반환: `{ field, label, before, after, severity }[]`
- **`CRITICAL` 등급:** `process`, `material.grade`, `heat_treatment`, `general_tolerance`, `critical_tolerances`, `threads_and_inserts`, `additive_spec.build_orientation`
- **`MAJOR` 등급:** `surface_finish` 전체(두께·색상·조도·마스킹), `order.quantity`, `order.due_date`
- **엣지 케이스:** `geometry_changes.length === 0 && spec_diffs.length > 0`이면 뷰어 최상단에 **"3D 형상 동일 — 제조 사양/공차만 N건 변경됨"** 강조 배너 고정.

### 3.2. 공차/재료 누락 경고 조건 (공유 링크 생성 & 승인 직전)
- `MISSING_MATERIAL` (**강제 확인**): `material.grade.trim() === ""`
- `MISSING_TOLERANCE` (**강제 확인**): `general_tolerance.standard === "UNSPECIFIED" && critical_tolerances.length === 0 && Target 리비전에 2D_PDF 첨부 없음`
- `UNDECLARED_THREAD` (**주의 경고**): 3D Diff에 ISO 탭 추정 힌트가 있는데 `threads_and_inserts.length === 0`
- `MISSING_BUILD_ORIENTATION` (**주의 경고**): `process.startsWith("PRINT_")`인데 `additive_spec.build_orientation` 비어 있음
- 강제 확인 경고가 1개 이상이면 **"누락된 사양(재질/공차)을 인지했으며 별도 합의 기준으로 진행합니다"** 명시적 체크박스를 눌러야 승인 버튼이 활성화되고, 경고 코드들이 `approval_snapshot_json.missing_spec_warnings_acked`에 기록된다.

---

## 4. 2D 도면(PDF/DXF) 첨부 API & 3D+2D Split View

### 4.1. 추가 API 엔드포인트 (4개)
```text
PATCH  /v1/revisions/{rev_id}/spec                          # SpecCardV2 수정 -> spec_version++ & APPROVED 링크 Supersede
POST   /v1/revisions/{rev_id}/attachments/presign           # Req: { kind, filename, content_type, file_size_bytes (<=25MB), file_sha256 }
                                                            # Res: { attachment_id, put_url (10m) }
POST   /v1/revisions/{rev_id}/attachments/{att_id}/complete  # R2/MinIO HEAD 검증 -> READY 전환 & APPROVED 링크 Supersede
DELETE /v1/revisions/{rev_id}/attachments/{att_id}           # status=DELETED & APPROVED 링크 Supersede
```

- 외부 리뷰어 번들(`GET /v1/public/links/{token}/bundle`) 및 내부 조회(`GET /v1/diffs/{diff_id}`) 응답에 `attachments: { base: [], target: [] }` (15분 Signed GET URL 포함) 제공.
- **원본 유출 방지:** `allow_raw_download === false`일 때 PDF는 `<iframe src>` 다운로드 UI 대신 `pdfjs-dist` 캔버스 렌더링 + 동일 SVG 동적 워터마크 오버레이, DXF는 `dxf-parser` + Canvas2D 벡터 미리보기만 허용(다운로드 버튼 숨김).
- **Split View UX:** `>= 1024px`는 `[3D 단독 | 3D+2D 분할 | 2D 단독]` 토글 + Base/Target 전환, `< 1024px`는 `[3D 뷰 | 2D 도면]` 탭 전환.

---

## 5. 측정 신뢰도 · DFM 체크리스트 · STEP 호환 · 태블릿 UX

1. **측정 신뢰도 표시:**
   - 해석적 측정: `Ø4.200 mm · CAD 이론치` + 보조 캡션 *"가공 공차 미포함 — 2D 도면·제조 사양 우선"*
   - 자유곡면/메쉬: `~4.20 mm · 메쉬 근사(±0.05mm)`
   - `source_unit === "INCH"`: 상단 고정 칩 `원본 INCH → MM 환산 (반올림 오차 확인)`
2. **공정별 DFM / 중요부위 체크리스트 (`review_links.dfm_ack_json` 저장):**
   - CNC(`CNC_MILLING`/`CNC_TURNING`): `corner_r`, `thin_wall`, `precision_fit`, `coating_shift`, `datum_setup`
   - 3D 프린팅(`PRINT_*`): `orientation`, `overhang`, `heat_insert`, `shrinkage`
   - 자동 판정이 아닌 "설계·공정 확인용 주의 체크리스트"로 표현, 승인 요청 시 `dfm_ack`를 함께 전송·저장.
3. **STEP 호환성 상시 안내 + 경고 칩:**
   - 뷰어 정보 드로어 고정 문구: *"STEP(AP203/214) 포맷은 나사산 규격·GD&T 공차·재질·표면처리를 포함하지 않습니다. 반드시 제조 사양 카드 및 2D 도면과 함께 검토하세요."*
   - `analytical_meta.warnings[]` (`OPEN_SHELL_ONLY`, `HELICAL_THREADS_DETECTED`, `HEALING_APPLIED`) 상단 경고 배너 매핑.
4. **공장 현장 태블릿 UX:**
   - 주요 인터랙션 버튼/행/체크 토글 `min-h-[44px] min-w-[44px]` 적용, `< 1024px` 바텀시트/모바일 레이아웃 지원.
   - 하단 플로팅 모드 세그먼트 `[회전 | 측정 | 핀 추가]` 분리 (회전 모드 오탭 방지, 탭 판정 `duration < 200ms && move < 8px`).
   - `matchMedia('(pointer: coarse)')` 또는 저사양 감지 시 기본 `lod1.glb` 로드 + `고화질 전환` 토글.
   - SSE 끊김 시 지수 백오프 후 4초 Polling 폴백, 15분 Signed URL 만료 전 12분 시점 자동 갱신, 오프라인 체크/핀 `sessionStorage` 임시 보관 및 `online` 시 자동 동기화.
