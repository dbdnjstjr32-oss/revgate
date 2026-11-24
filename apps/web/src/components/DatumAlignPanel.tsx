"use client";

/**
 * NEEDS_ALIGN -> 3-2-1 Datum Align (product spec §3.2): pick the same primary plane, secondary
 * plane/hole and tertiary plane/hole on both revisions (canvas click or list), preview the resulting
 * Target -> Base transform instantly in the viewer, then send only its 16 numbers to /realign.
 */

import type { AnalyticalMeta, Face } from "@revgate/contracts";
import { useEffect, useMemo, useState } from "react";

import { type DatumTriple, solveDatumTransform, validateDatum } from "@/lib/datum";
import { describeFace } from "@/lib/measure";

import { Button } from "./ui";
import type { PickEvent, Side } from "./viewer/DiffViewer";

const ROLES = ["1차 기준 (평면)", "2차 기준 (평면/홀)", "3차 기준 (평면/홀)"];

type Slots = Record<Side, (number | null)[]>;

function eligible(face: Face, role: number): boolean {
  return role === 0 ? face.params.type === "PLANE" : face.params.type === "PLANE" || face.params.type === "CYLINDER";
}

export function DatumAlignPanel({ baseMeta, targetMeta, confidence, applying, onPreview, onApply, registerPick }: {
  baseMeta: AnalyticalMeta | null;
  targetMeta: AnalyticalMeta | null;
  confidence: number | null;
  applying: boolean;
  onPreview: (transform: number[] | null, faces: { base: number[]; target: number[] }) => void;
  onApply: (transform: number[]) => void;
  registerPick: (handler: ((p: PickEvent) => boolean) | null) => void;
}) {
  const [slots, setSlots] = useState<Slots>({ BASE: [null, null, null], TARGET: [null, null, null] });
  const [active, setActive] = useState<{ side: Side; role: number } | null>({ side: "BASE", role: 0 });
  const metas = useMemo(() => ({ BASE: baseMeta, TARGET: targetMeta }), [baseMeta, targetMeta]);

  const triples = useMemo(() => {
    const pick = (side: Side) => slots[side].map((id) => (id === null ? null : metas[side]?.faces[id] ?? null));
    return { BASE: pick("BASE"), TARGET: pick("TARGET") };
  }, [slots, metas]);

  const errors = useMemo(() => {
    const err = (side: Side) => (triples[side].some((f) => !f) ? "세 기준을 모두 선택하세요" : validateDatum(triples[side] as Face[]));
    return { BASE: err("BASE"), TARGET: err("TARGET") };
  }, [triples]);

  const transform = useMemo(() => {
    if (errors.BASE || errors.TARGET) return null;
    return solveDatumTransform(triples.BASE as DatumTriple, triples.TARGET as DatumTriple);
  }, [errors, triples]);

  useEffect(() => {
    onPreview(transform, {
      base: slots.BASE.filter((x): x is number => x !== null),
      target: slots.TARGET.filter((x): x is number => x !== null),
    });
  }, [transform, slots, onPreview]);

  useEffect(() => {
    if (!active) {
      registerPick(null);
      return;
    }
    registerPick((p: PickEvent) => {
      const face = metas[p.side]?.faces[p.faceId];
      if (p.side !== active.side || !face || !eligible(face, active.role)) return false;
      setSlots((s) => ({ ...s, [p.side]: s[p.side].map((v, i) => (i === active.role ? p.faceId : v)) }));
      const next = active.role < 2 ? { side: active.side, role: active.role + 1 } : active.side === "BASE" ? { side: "TARGET" as Side, role: 0 } : null;
      setActive(next);
      return true;
    });
    return () => registerPick(null);
  }, [active, metas, registerPick]);

  return (
    <div data-testid="datum-panel" className="space-y-3">
      <div className="rounded-md bg-sky-50 p-2 text-xs text-sky-900">
        <p className="font-semibold">자동 정합 신뢰도가 낮습니다 ({confidence !== null ? confidence.toFixed(3) : "-"} &lt; 0.50)</p>
        <p>현장 치구 세팅과 같은 3-2-1 순서로 두 리비전의 기준면을 고르면 즉시 미리보기 후 재계산합니다.</p>
      </div>
      {(["BASE", "TARGET"] as const).map((side) => (
        <div key={side}>
          <p className="mb-1 text-xs font-semibold text-slate-700">{side === "BASE" ? "Base (Before)" : "Target (After)"}</p>
          {ROLES.map((role, i) => (
            <div key={role} className="mb-1 flex items-center gap-1">
              <select
                data-testid={`datum-${side.toLowerCase()}-${i}`}
                className="min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-1.5 py-1 text-[11px]"
                value={slots[side][i] ?? ""}
                onChange={(e) => {
                  const v = e.target.value === "" ? null : Number(e.target.value);
                  setSlots((s) => ({ ...s, [side]: s[side].map((x, j) => (j === i ? v : x)) }));
                }}
              >
                <option value="">{role}</option>
                {(metas[side]?.faces ?? []).filter((f) => eligible(f, i)).map((f) => (
                  <option key={f.face_id} value={f.face_id}>
                    {describeFace(f)}
                  </option>
                ))}
              </select>
              <button
                type="button"
                title="캔버스에서 선택"
                onClick={() => setActive({ side, role: i })}
                className={`rounded px-1.5 py-1 text-[11px] ${active?.side === side && active.role === i ? "bg-sky-600 text-white" : "bg-slate-100 text-slate-600"}`}
              >
                🎯
              </button>
            </div>
          ))}
          {errors[side] && slots[side].some((x) => x !== null) && (
            <p data-testid={`datum-error-${side.toLowerCase()}`} className="text-[11px] text-red-600">
              {errors[side]}
            </p>
          )}
        </div>
      ))}
      {transform && (
        <pre data-testid="datum-preview" className="overflow-x-auto rounded-md bg-slate-900 p-2 text-[10px] leading-4 text-emerald-300">
          {[0, 1, 2, 3].map((r) => transform.slice(r * 4, r * 4 + 4).map((v) => v.toFixed(4).padStart(10)).join(" ")).join("\n")}
        </pre>
      )}
      <Button data-testid="datum-apply" className="w-full" disabled={!transform || applying} onClick={() => transform && onApply(transform)}>
        {applying ? "재계산 중…" : "3-2-1 정합 적용 (q-diff-interactive)"}
      </Button>
    </div>
  );
}
