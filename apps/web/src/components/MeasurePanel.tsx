"use client";

import type { AnalyticalMeta } from "@revgate/contracts";

import { describeFace, type Measurement } from "@/lib/measure";
import { formatMeasureValue, reliability } from "@/lib/measureFormat";
import { INCH_CHIP_TEXT, MEASURE_CAPTION } from "@/lib/spec";
import type { V3 } from "@/lib/vec";

import type { Side } from "./viewer/DiffViewer";

export interface MeasureSlot {
  side: Side;
  faceId: number;
  point: V3 | null;
}

function SlotSelect({ label, testId, slot, metas, onChange }: {
  label: string;
  testId: string;
  slot: MeasureSlot | null;
  metas: { BASE: AnalyticalMeta | null; TARGET: AnalyticalMeta | null };
  onChange: (s: MeasureSlot | null) => void;
}) {
  const value = slot ? `${slot.side}:${slot.faceId}` : "";
  return (
    <label className="block text-xs text-slate-600">
      <span className="mb-1 block font-medium">{label}</span>
      <select
        data-testid={testId}
        className="w-full rounded-md border border-slate-300 bg-white px-2 py-1 text-xs"
        value={value}
        onChange={(e) => {
          if (!e.target.value) return onChange(null);
          const [side, id] = e.target.value.split(":");
          onChange({ side: side as Side, faceId: Number(id), point: null });
        }}
      >
        <option value="">— 캔버스 클릭 또는 선택 —</option>
        {(["BASE", "TARGET"] as const).map((side) =>
          metas[side] ? (
            <optgroup key={side} label={side === "BASE" ? "Base (Before)" : "Target (After)"}>
              {metas[side]!.faces.map((f) => (
                <option key={`${side}:${f.face_id}`} value={`${side}:${f.face_id}`}>
                  {side} · {describeFace(f)}
                </option>
              ))}
            </optgroup>
          ) : null,
        )}
      </select>
    </label>
  );
}

export function MeasurePanel({ slots, metas, measurement, onChange }: {
  slots: [MeasureSlot | null, MeasureSlot | null];
  metas: { BASE: AnalyticalMeta | null; TARGET: AnalyticalMeta | null };
  measurement: Measurement | null;
  onChange: (slots: [MeasureSlot | null, MeasureSlot | null]) => void;
}) {
  return (
    <div data-testid="measure-panel" className="space-y-2">
      <SlotSelect label="면 A" testId="measure-a" slot={slots[0]} metas={metas} onChange={(s) => onChange([s, slots[1]])} />
      <SlotSelect label="면 B" testId="measure-b" slot={slots[1]} metas={metas} onChange={(s) => onChange([slots[0], s])} />
      {(metas.BASE?.source_unit === "INCH" || metas.TARGET?.source_unit === "INCH") && (
        <p data-testid="measure-inch" className="rounded bg-orange-50 px-1.5 py-0.5 text-[10px] font-semibold text-orange-900">{INCH_CHIP_TEXT}</p>
      )}
      {measurement ? (
        <div data-testid="measure-result" data-kind={measurement.kind} data-approximate={measurement.approximate ? "true" : "false"}
          className="rounded-md bg-fuchsia-50 p-2 text-xs text-fuchsia-950">
          <p className="font-semibold" data-testid="measure-summary">{measurement.summary}</p>
          <ul className="mt-1 space-y-0.5 font-mono">
            {measurement.values.map((v) => (
              <li key={v.key} data-testid="measure-value" data-key={v.key}>
                {v.label}: {formatMeasureValue(v, measurement.approximate)} · <span className="font-sans">{reliability(measurement).label}</span>
              </li>
            ))}
          </ul>
          <div className="mt-1 flex flex-wrap gap-1">
            {measurement.badges.map((b) => (
              <span key={b} data-testid="measure-badge" className="rounded bg-fuchsia-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                {b}
              </span>
            ))}
            <span data-testid="measure-reliability" data-approximate={measurement.approximate ? "true" : "false"}
              className={`rounded px-1.5 py-0.5 text-[10px] font-semibold text-white ${measurement.approximate ? "bg-amber-600" : "bg-emerald-600"}`}>
              {reliability(measurement).label}
            </span>
          </div>
          {!measurement.approximate && <p data-testid="measure-caption" className="mt-1 text-[10px] text-slate-600">{MEASURE_CAPTION}</p>}
        </div>
      ) : (
        <p className="text-xs text-slate-500">두 면을 고르면 analytical_meta 수식으로 거리·각도를 계산합니다.</p>
      )}
    </div>
  );
}
