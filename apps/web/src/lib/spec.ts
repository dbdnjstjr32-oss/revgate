/** Phase 4 manufacturing spec UI definitions (docs/PHASE4_MANUFACTURING_SPEC.md §3, §5). Rules live in the API
 *  (apps/api/app/spec.py); the web only renders, edits and explains them. */

import type {
  DfmCheckId, ManufacturingProcess, MissingSpecWarning, SpecCardV2, SpecDiffItem,
} from "@revgate/contracts";

export const PROCESS_OPTIONS: { value: ManufacturingProcess; label: string }[] = [
  { value: "CNC_MILLING", label: "CNC 밀링" },
  { value: "CNC_TURNING", label: "CNC 선반" },
  { value: "PRINT_FDM", label: "3D 프린팅 (FDM)" },
  { value: "PRINT_SLA_SLS_MJF", label: "3D 프린팅 (SLA/SLS/MJF)" },
  { value: "SHEET_METAL", label: "판금" },
  { value: "OTHER", label: "기타/미지정" },
];
export const PROCESS_LABEL = Object.fromEntries(PROCESS_OPTIONS.map((o) => [o.value, o.label])) as Record<ManufacturingProcess, string>;

export const TOLERANCE_OPTIONS: { value: SpecCardV2["general_tolerance"]["standard"]; label: string }[] = [
  { value: "UNSPECIFIED", label: "미지정" },
  { value: "ISO_2768_fH", label: "ISO 2768-fH (정밀)" },
  { value: "ISO_2768_mK", label: "ISO 2768-mK (보통)" },
  { value: "ISO_2768_cL", label: "ISO 2768-cL (거친)" },
  { value: "CUSTOM", label: "사용자 정의" },
];

export const INSERT_OPTIONS: { value: SpecCardV2["threads_and_inserts"][number]["insert_type"]; label: string }[] = [
  { value: "TAPPED_DIRECT", label: "직접 탭" },
  { value: "HELICOIL", label: "헬리코일" },
  { value: "HEAT_SET_BRASS", label: "열압입 황동 인서트" },
  { value: "PEM", label: "PEM 인서트" },
];

export const INSPECTION_OPTIONS: { value: SpecCardV2["order"]["inspection_level"]; label: string }[] = [
  { value: "NONE", label: "검사 없음" },
  { value: "DIMENSIONAL_SHEET", label: "치수 성적서" },
  { value: "CMM_REPORT", label: "CMM 리포트" },
];

/** §5.2 — a caution checklist for the reviewer, not an automatic DFM verdict. */
export const DFM_CHECKS: Record<"CNC" | "PRINT", { id: DfmCheckId; label: string; hint: string }[]> = {
  CNC: [
    { id: "corner_r", label: "내측 코너 R", hint: "포켓 내측 코너 R이 공구 반경 이상인지" },
    { id: "thin_wall", label: "박육부", hint: "얇은 벽의 채터링·변형 위험" },
    { id: "precision_fit", label: "정밀 끼워맞춤", hint: "H7 등 끼워맞춤 공차와 측정 방법" },
    { id: "coating_shift", label: "코팅 치수 변화", hint: "아노다이징·도금 두께만큼 치수 이동" },
    { id: "datum_setup", label: "데이텀·치구", hint: "기준면·치구 세팅 순서" },
  ],
  PRINT: [
    { id: "orientation", label: "적층 방향", hint: "강도 방향·표면 품질에 맞는 출력 방향" },
    { id: "overhang", label: "오버행·서포트", hint: "서포트 제거 자국·오버행 각도" },
    { id: "heat_insert", label: "인서트 홀", hint: "열압입 인서트 홀 지름·깊이" },
    { id: "shrinkage", label: "수축·휨", hint: "재료 수축률과 평면도" },
  ],
};

export function dfmChecksFor(process: ManufacturingProcess) {
  if (process.startsWith("CNC_")) return DFM_CHECKS.CNC;
  if (process.startsWith("PRINT_")) return DFM_CHECKS.PRINT;
  return [];
}

export const SPEC_ACK_TEXT = "누락된 사양(재질/공차)을 인지했으며 별도 합의 기준으로 진행합니다";
export const SUPERSEDED_TEXT =
  "⚠️ 구버전 승인(SUPERSEDED) — 가공 착수 금지. 이후 새 리비전(또는 사양/도면 수정)이 발생했습니다. 발주처에 최신 링크를 요청하세요.";
export const SUPERSEDED_REASON: Record<string, string> = {
  NEW_REVISION: "새 리비전 등록",
  SPEC_UPDATED: "제조 사양 수정",
  DRAWING_UPDATED: "2D 도면 추가/삭제",
};
export const STEP_COMPAT_NOTICE =
  "STEP(AP203/214) 포맷은 나사산 규격·GD&T 공차·재질·표면처리를 포함하지 않습니다. 반드시 제조 사양 카드 및 2D 도면과 함께 검토하세요.";
export const MEASURE_CAPTION = "가공 공차 미포함 — 2D 도면·제조 사양 우선";
export const INCH_CHIP_TEXT = "원본 INCH → MM 환산 (반올림 오차 확인)";

/** analytical_meta.summary.warnings -> top-of-viewer banner text (§5.3) */
export const STEP_WARNING_TEXT: Record<string, string> = {
  OPEN_SHELL_ONLY: "열린 쉘(솔리드 아님) — 체적·벽두께 판정이 제한됩니다",
  HELICAL_THREADS_DETECTED: "모델링된 나사산(헬리컬) 감지 — 나사 규격은 제조 사양 카드로 확인하세요",
  HEALING_APPLIED: "형상 자동 보정(Healing) 적용 — 원본과 미세 차이가 있을 수 있습니다",
  INCH_SOURCE_CONVERTED: "INCH 원본을 MM로 환산했습니다",
  BOOLEAN_FALLBACK_HEATMAP: "불리언 연산 실패 — 편차 히트맵으로 대체 표시",
};
export const STEP_WARNING_BANNER_CODES = ["OPEN_SHELL_ONLY", "HELICAL_THREADS_DETECTED", "HEALING_APPLIED"];

export const ackRequired = (warnings: MissingSpecWarning[]) => warnings.filter((w) => w.level === "ACK_REQUIRED");

/** §3.1 edge case: no geometry change but the manufacturing spec changed. */
export function specOnlyChange(geometryChanges: number, specDiffs: SpecDiffItem[]): boolean {
  return geometryChanges === 0 && specDiffs.length > 0;
}

export const SEVERITY_ORDER: Record<string, number> = { CRITICAL: 0, MAJOR: 1, INFO: 2 };

export function emptySpecCard(): SpecCardV2 {
  return {
    schema_version: "2.0",
    process: "OTHER",
    material: { grade: "" },
    heat_treatment: { required: false },
    surface_finish: { type: "" },
    general_tolerance: { standard: "UNSPECIFIED" },
    critical_tolerances: [],
    threads_and_inserts: [],
    order: { inspection_level: "NONE" },
  };
}

const blank = (v: unknown) => v === undefined || v === null || (typeof v === "string" && v.trim() === "") || (typeof v === "number" && !Number.isFinite(v));

function prune<T extends object>(obj: T, keep: (keyof T)[] = []): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    if (keep.includes(k as keyof T)) out[k] = typeof v === "string" ? v.trim() : v;
    else if (!blank(v)) out[k] = typeof v === "string" ? v.trim() : v;
  }
  return out as T;
}

/** Editor draft -> a strictly valid SpecCardV2 body (optional blanks dropped, required strings kept, rows trimmed). */
export function cleanSpecCard(card: SpecCardV2): SpecCardV2 {
  const additive = card.additive_spec && card.process.startsWith("PRINT_")
    ? prune(card.additive_spec, ["build_orientation"])
    : undefined;
  return {
    schema_version: "2.0",
    process: card.process,
    material: prune(card.material, ["grade"]),
    heat_treatment: card.heat_treatment.required
      ? prune(card.heat_treatment, ["required"])
      : { required: false },
    surface_finish: prune(card.surface_finish, ["type"]),
    general_tolerance: card.general_tolerance.standard === "CUSTOM" || card.general_tolerance.custom_note?.trim()
      ? prune(card.general_tolerance, ["standard"])
      : { standard: card.general_tolerance.standard },
    critical_tolerances: card.critical_tolerances
      .filter((t) => t.location.trim() && t.spec.trim())
      .map((t, i) => prune({ ...t, id: t.id.trim() || `CT${i + 1}` }, ["id", "location", "spec"])),
    threads_and_inserts: card.threads_and_inserts
      .filter((t) => t.spec.trim() && t.count >= 1)
      .map((t) => prune(t, ["spec", "count", "insert_type"])),
    ...(additive ? { additive_spec: additive } : {}),
    order: prune(card.order, ["inspection_level"]),
  };
}
