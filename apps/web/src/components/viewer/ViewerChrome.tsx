"use client";

/** Viewer chrome (Phase 4 §3.1, §5): spec-only banner, STEP warning banners, INCH chip, LOD switch, floating
 *  [회전 | 측정 | 핀 추가] mode segment and the always-on STEP compatibility drawer. */

import type { AnalyticalMeta } from "@revgate/contracts";

import { INCH_CHIP_TEXT, STEP_COMPAT_NOTICE, STEP_WARNING_BANNER_CODES, STEP_WARNING_TEXT } from "@/lib/spec";

export type Tool = "SELECT" | "MEASURE" | "PIN";

export function ModeSegment({ tool, canPin, onChange }: { tool: Tool; canPin: boolean; onChange: (t: Tool) => void }) {
  const items: { id: Tool; label: string; testId: string; disabled?: boolean }[] = [
    { id: "SELECT", label: "회전", testId: "tool-select" },
    { id: "MEASURE", label: "측정", testId: "tool-measure" },
    { id: "PIN", label: "핀 추가", testId: "tool-pin", disabled: !canPin },
  ];
  return (
    <div data-testid="mode-segment" role="radiogroup" aria-label="뷰어 모드"
      className="pointer-events-auto absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 overflow-hidden rounded-full bg-white/95 shadow-lg ring-1 ring-slate-300">
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          role="radio"
          aria-checked={tool === it.id}
          data-testid={it.testId}
          disabled={it.disabled}
          onClick={() => onChange(it.id)}
          className={`min-h-[44px] min-w-[64px] px-4 text-sm font-semibold disabled:text-slate-300 ${tool === it.id ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"}`}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function ViewerBanners({ specOnly, specDiffCount, metas }: {
  specOnly: boolean;
  specDiffCount: number;
  metas: (AnalyticalMeta | null)[];
}) {
  const warnings = [...new Set(metas.flatMap((m) => m?.summary.warnings ?? []))].filter((w) => STEP_WARNING_BANNER_CODES.includes(w));
  const inch = metas.some((m) => m?.source_unit === "INCH");
  if (!specOnly && !warnings.length && !inch) return null;
  return (
    <div className="space-y-1">
      {specOnly && (
        <div data-testid="spec-only-banner" className="sticky top-14 z-20 rounded-lg bg-violet-700 px-3 py-2 text-sm font-bold text-white shadow">
          3D 형상 동일 — 제조 사양/공차만 {specDiffCount}건 변경됨
        </div>
      )}
      {inch && (
        <span data-testid="inch-chip" className="inline-block rounded-full bg-orange-100 px-2.5 py-1 text-xs font-semibold text-orange-900 ring-1 ring-orange-300">
          {INCH_CHIP_TEXT}
        </span>
      )}
      {warnings.map((w) => (
        <p key={w} data-testid="step-warning" data-code={w} className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-900 ring-1 ring-amber-200">
          ⚠ {STEP_WARNING_TEXT[w] ?? w} <span className="font-mono text-[10px]">({w})</span>
        </p>
      ))}
    </div>
  );
}

export function StepInfoDrawer() {
  return (
    <details data-testid="step-info" open className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600">
      <summary className="min-h-[32px] cursor-pointer font-semibold text-slate-700">ⓘ STEP 호환성 안내</summary>
      <p data-testid="step-info-text">{STEP_COMPAT_NOTICE}</p>
    </details>
  );
}
