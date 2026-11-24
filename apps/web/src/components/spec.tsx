"use client";

/** Phase 4 manufacturing spec views: SpecCardV2 summary, Base↔Target spec diff, missing-spec warnings. */

import type { MissingSpecWarning, SpecCardV2, SpecDiffItem } from "@revgate/contracts";

import { bytes } from "@/lib/format";
import { INSPECTION_OPTIONS, INSERT_OPTIONS, PROCESS_LABEL, SEVERITY_ORDER, TOLERANCE_OPTIONS, specOnlyChange } from "@/lib/spec";
import type { Part, Revision } from "@/lib/types";

const SEV_CHIP: Record<string, string> = {
  CRITICAL: "bg-red-600 text-white",
  MAJOR: "bg-amber-500 text-white",
  INFO: "bg-slate-400 text-white",
};
const label = <T extends string>(opts: { value: T; label: string }[], v: T) => opts.find((o) => o.value === v)?.label ?? v;

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-1">
      <dt className="text-slate-500">{k}</dt>
      <dd className="text-slate-800">{children}</dd>
    </div>
  );
}

export function SpecCardSummary({ card, testId }: { card: SpecCardV2; testId?: string }) {
  const sf = card.surface_finish;
  return (
    <dl data-testid={testId} data-process={card.process} className="space-y-0.5 text-[11px]">
      <Row k="공정">{PROCESS_LABEL[card.process]}</Row>
      <Row k="재질">
        {card.material.grade || <span className="font-semibold text-red-600">미지정</span>}
        {card.material.standard ? ` (${card.material.standard})` : ""}
      </Row>
      <Row k="열처리">{card.heat_treatment.required ? `필요${card.heat_treatment.spec ? ` · ${card.heat_treatment.spec}` : ""}` : "없음"}</Row>
      <Row k="표면처리">
        {[sf.type || "미지정", sf.anodizing_thickness_um && `${sf.anodizing_thickness_um} µm`, sf.color, sf.ra_um !== undefined && `Ra ${sf.ra_um} µm`,
          sf.masking_note && `마스킹: ${sf.masking_note}`].filter(Boolean).join(" · ")}
      </Row>
      <Row k="일반 공차">
        {card.general_tolerance.standard === "UNSPECIFIED" ? (
          <span className="font-semibold text-red-600">미지정</span>
        ) : (
          label(TOLERANCE_OPTIONS, card.general_tolerance.standard) + (card.general_tolerance.custom_note ? ` · ${card.general_tolerance.custom_note}` : "")
        )}
      </Row>
      <Row k="중요 공차">
        {card.critical_tolerances.length
          ? card.critical_tolerances.map((t) => `${t.location}: ${t.spec}${t.datum_ref ? ` [${t.datum_ref}]` : ""}`).join(" / ")
          : "-"}
      </Row>
      <Row k="나사·인서트">
        {card.threads_and_inserts.length
          ? card.threads_and_inserts.map((t) => `${t.spec} ×${t.count} ${label(INSERT_OPTIONS, t.insert_type)}${t.depth_mm ? ` 깊이 ${t.depth_mm}mm` : ""}`).join(" / ")
          : "-"}
      </Row>
      {card.additive_spec && (
        <Row k="적층">
          {[card.additive_spec.build_orientation || "방향 미지정", card.additive_spec.layer_height_mm && `${card.additive_spec.layer_height_mm} mm`,
            card.additive_spec.infill_pct !== undefined && `충전 ${card.additive_spec.infill_pct}%`].filter(Boolean).join(" · ")}
        </Row>
      )}
      <Row k="주문">
        {[card.order.quantity !== undefined && `${card.order.quantity}개`, card.order.due_date && `납기 ${card.order.due_date}`,
          label(INSPECTION_OPTIONS, card.order.inspection_level)].filter(Boolean).join(" · ")}
      </Row>
    </dl>
  );
}

/** Spec card of both revisions (replaces the Phase 3 raw spec_card_json dump). */
export function SpecCard({ part, base, target }: { part: Part; base?: Revision; target?: Revision }) {
  const block = (rev: Revision | undefined, side: "Base" | "Target") =>
    rev ? (
      <div className="rounded-md bg-slate-50 p-2" data-testid={`spec-card-${side.toLowerCase()}`}>
        <p className="font-semibold text-slate-800">
          {side} · Rev {rev.rev_label} <span data-testid={`spec-version-${side.toLowerCase()}`} className="font-mono text-[10px] text-slate-500">spec v{rev.spec_version}</span>
        </p>
        <p className="font-mono text-[10px] text-slate-500">STEP sha256 {rev.file_sha256.slice(0, 16)}… · {bytes(rev.file_size_bytes)}</p>
        <p className="mb-1 text-[11px] text-slate-600">원본: {rev.raw_available ? "보관 중" : "파기됨 (Zero-Retention)"}</p>
        <SpecCardSummary card={rev.spec_card} />
      </div>
    ) : null;
  return (
    <div data-testid="spec-card" className="space-y-1.5 text-xs">
      <p>
        <span className="font-semibold">{part.part_number}</span> {part.name}
      </p>
      <p className="text-slate-600">
        부품 기본값: 소재 {part.default_material ?? "-"} · 후처리 {part.default_finish ?? "-"}
      </p>
      {block(base, "Base")}
      {block(target, "Target")}
    </div>
  );
}

export function SpecDiffCard({ diffs, geometryChanges }: { diffs: SpecDiffItem[]; geometryChanges: number }) {
  const sorted = [...diffs].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  return (
    <div data-testid="spec-diff-card" data-count={diffs.length} className="space-y-1.5 text-xs">
      {specOnlyChange(geometryChanges, diffs) && (
        <p data-testid="spec-only-note" className="rounded-md bg-violet-600 px-2 py-1.5 font-semibold text-white">
          3D 형상 동일 — 제조 사양/공차만 {diffs.length}건 변경됨
        </p>
      )}
      {sorted.length === 0 ? (
        <p className="text-slate-500">Base↔Target 제조 사양 차이가 없습니다.</p>
      ) : (
        <ul className="space-y-1">
          {sorted.map((d) => (
            <li key={d.field} data-testid="spec-diff-row" data-field={d.field} data-severity={d.severity} className="rounded-md border border-slate-200 p-1.5">
              <div className="flex items-center gap-1.5">
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${SEV_CHIP[d.severity]}`}>{d.severity}</span>
                <span className="font-semibold text-slate-800">{d.label}</span>
                <span className="font-mono text-[10px] text-slate-400">{d.field}</span>
              </div>
              <p className="mt-0.5 text-[11px]">
                <span className="text-slate-500 line-through">{d.before ?? "미지정"}</span>
                <span className="mx-1 text-slate-400">→</span>
                <span className="font-semibold text-slate-900">{d.after ?? "미지정"}</span>
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SpecWarnings({ warnings }: { warnings: MissingSpecWarning[] }) {
  if (!warnings.length) return null;
  return (
    <ul data-testid="spec-warnings" className="space-y-1">
      {warnings.map((w) => (
        <li
          key={w.code}
          data-testid="spec-warning"
          data-code={w.code}
          data-level={w.level}
          className={`rounded-md px-2 py-1 text-[11px] ${w.level === "ACK_REQUIRED" ? "bg-red-50 text-red-800 ring-1 ring-red-200" : "bg-amber-50 text-amber-900 ring-1 ring-amber-200"}`}
        >
          <b>{w.level === "ACK_REQUIRED" ? "확인 필수" : "주의"}</b> · {w.message} <span className="font-mono text-[10px] opacity-70">{w.code}</span>
        </li>
      ))}
    </ul>
  );
}
