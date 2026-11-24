"use client";

/** SpecCardV2 editor (Phase 4 §3) — PATCH /v1/revisions/{id}/spec bumps spec_version and supersedes approvals of it. */

import type { SpecCardV2 } from "@revgate/contracts";
import { useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { INSPECTION_OPTIONS, INSERT_OPTIONS, PROCESS_OPTIONS, TOLERANCE_OPTIONS, cleanSpecCard } from "@/lib/spec";
import type { Revision } from "@/lib/types";

import { Button, ErrorText, Field, inputCls } from "./ui";

const num = (v: string) => (v.trim() === "" ? undefined : Number(v));

export function SpecEditor({ revision, side, onSaved }: {
  revision: Revision;
  side: "BASE" | "TARGET";
  onSaved: (result: { revision: Revision; changed: boolean; superseded_links: number }) => void;
}) {
  const [card, setCard] = useState<SpecCardV2>(() => structuredClone(revision.spec_card));
  useEffect(() => {
    setCard(structuredClone(revision.spec_card));
  }, [revision.id, revision.spec_version, revision.spec_card]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<SpecCardV2>) => setCard((c) => ({ ...c, ...patch }));
  const printing = card.process.startsWith("PRINT_");

  return (
    <form
      data-testid="spec-editor"
      data-side={side}
      className="space-y-2 text-xs"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        setMsg(null);
        try {
          const res = await api<{ revision: Revision; changed: boolean; superseded_links: number }>(`/v1/revisions/${revision.id}/spec`, {
            method: "PATCH",
            json: cleanSpecCard(card),
          });
          setMsg(res.changed
            ? `저장됨 · spec v${res.revision.spec_version}${res.superseded_links ? ` · 기존 승인 ${res.superseded_links}건 구버전(SUPERSEDED) 처리` : ""}`
            : "변경 사항 없음");
          onSaved(res);
        } catch (err) {
          setError(errorText(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <p className="text-[11px] text-slate-500">
        Rev {revision.rev_label} · <span data-testid="spec-editor-version">spec v{revision.spec_version}</span> — STEP에는 재질·공차·나사 규격이 없으므로 여기서 지정합니다.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Field label="공정">
          <select data-testid="spec-process" className={inputCls} value={card.process}
            onChange={(e) => set({ process: e.target.value as SpecCardV2["process"] })}>
            {PROCESS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
        <Field label="재질 (grade)">
          <input data-testid="spec-material-grade" className={inputCls} value={card.material.grade} placeholder="AL6061-T6"
            onChange={(e) => set({ material: { ...card.material, grade: e.target.value } })} />
        </Field>
        <Field label="재질 규격">
          <input data-testid="spec-material-standard" className={inputCls} value={card.material.standard ?? ""} placeholder="KS D 6701"
            onChange={(e) => set({ material: { ...card.material, standard: e.target.value } })} />
        </Field>
        <Field label="열처리">
          <span className="flex min-h-[44px] items-center gap-2">
            <input data-testid="spec-heat-required" type="checkbox" className="h-5 w-5" checked={card.heat_treatment.required}
              onChange={(e) => set({ heat_treatment: { ...card.heat_treatment, required: e.target.checked } })} />
            <input data-testid="spec-heat-spec" className={inputCls} disabled={!card.heat_treatment.required} value={card.heat_treatment.spec ?? ""}
              placeholder="T6 재시효" onChange={(e) => set({ heat_treatment: { ...card.heat_treatment, spec: e.target.value } })} />
          </span>
        </Field>
        <Field label="표면처리">
          <input data-testid="spec-finish-type" className={inputCls} value={card.surface_finish.type} placeholder="흑색 아노다이징 Type II"
            onChange={(e) => set({ surface_finish: { ...card.surface_finish, type: e.target.value } })} />
        </Field>
        <Field label="아노다이징 두께 (µm)">
          <input data-testid="spec-finish-thickness" className={inputCls} value={card.surface_finish.anodizing_thickness_um ?? ""} placeholder="10-15"
            onChange={(e) => set({ surface_finish: { ...card.surface_finish, anodizing_thickness_um: e.target.value } })} />
        </Field>
        <Field label="색상">
          <input data-testid="spec-finish-color" className={inputCls} value={card.surface_finish.color ?? ""} placeholder="Black"
            onChange={(e) => set({ surface_finish: { ...card.surface_finish, color: e.target.value } })} />
        </Field>
        <Field label="표면 조도 Ra (µm)">
          <input data-testid="spec-finish-ra" type="number" step="0.1" min={0} className={inputCls} value={card.surface_finish.ra_um ?? ""}
            onChange={(e) => set({ surface_finish: { ...card.surface_finish, ra_um: num(e.target.value) } })} />
        </Field>
      </div>
      <Field label="마스킹">
        <input data-testid="spec-finish-masking" className={inputCls} value={card.surface_finish.masking_note ?? ""} placeholder="모터 장착면 마스킹"
          onChange={(e) => set({ surface_finish: { ...card.surface_finish, masking_note: e.target.value } })} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="일반 공차">
          <select data-testid="spec-tolerance" className={inputCls} value={card.general_tolerance.standard}
            onChange={(e) => set({ general_tolerance: { ...card.general_tolerance, standard: e.target.value as SpecCardV2["general_tolerance"]["standard"] } })}>
            {TOLERANCE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
        <Field label="공차 메모">
          <input data-testid="spec-tolerance-note" className={inputCls} value={card.general_tolerance.custom_note ?? ""}
            onChange={(e) => set({ general_tolerance: { ...card.general_tolerance, custom_note: e.target.value } })} />
        </Field>
      </div>

      <fieldset className="space-y-1 rounded-md border border-slate-200 p-2">
        <legend className="px-1 font-semibold text-slate-700">중요 공차</legend>
        {card.critical_tolerances.map((t, i) => (
          <div key={i} data-testid="spec-ct-row" className="grid grid-cols-[1fr_1fr_56px_44px] gap-1">
            <input data-testid="spec-ct-location" className={inputCls} value={t.location} placeholder="위치 (모터 축 홀)"
              onChange={(e) => set({ critical_tolerances: card.critical_tolerances.map((x, j) => (j === i ? { ...x, location: e.target.value } : x)) })} />
            <input data-testid="spec-ct-spec" className={inputCls} value={t.spec} placeholder="Ø6 H7"
              onChange={(e) => set({ critical_tolerances: card.critical_tolerances.map((x, j) => (j === i ? { ...x, spec: e.target.value } : x)) })} />
            <input data-testid="spec-ct-datum" className={inputCls} value={t.datum_ref ?? ""} placeholder="A"
              onChange={(e) => set({ critical_tolerances: card.critical_tolerances.map((x, j) => (j === i ? { ...x, datum_ref: e.target.value } : x)) })} />
            <button type="button" aria-label="삭제" className="min-h-[44px] rounded-md bg-slate-100"
              onClick={() => set({ critical_tolerances: card.critical_tolerances.filter((_, j) => j !== i) })}>✕</button>
          </div>
        ))}
        <Button data-testid="spec-ct-add" variant="secondary"
          onClick={() => set({ critical_tolerances: [...card.critical_tolerances, { id: `CT${card.critical_tolerances.length + 1}`, location: "", spec: "" }] })}>
          + 중요 공차
        </Button>
      </fieldset>

      <fieldset className="space-y-1 rounded-md border border-slate-200 p-2">
        <legend className="px-1 font-semibold text-slate-700">나사·인서트</legend>
        {card.threads_and_inserts.map((t, i) => (
          <div key={i} data-testid="spec-thread-row" className="grid grid-cols-[1fr_52px_60px_1fr_44px] gap-1">
            <input data-testid="spec-thread-spec" className={inputCls} value={t.spec} placeholder="M5x0.8"
              onChange={(e) => set({ threads_and_inserts: card.threads_and_inserts.map((x, j) => (j === i ? { ...x, spec: e.target.value } : x)) })} />
            <input data-testid="spec-thread-count" type="number" min={1} className={inputCls} value={t.count}
              onChange={(e) => set({ threads_and_inserts: card.threads_and_inserts.map((x, j) => (j === i ? { ...x, count: Number(e.target.value) } : x)) })} />
            <input data-testid="spec-thread-depth" type="number" min={0} step="0.5" className={inputCls} value={t.depth_mm ?? ""} placeholder="깊이"
              onChange={(e) => set({ threads_and_inserts: card.threads_and_inserts.map((x, j) => (j === i ? { ...x, depth_mm: num(e.target.value) } : x)) })} />
            <select data-testid="spec-thread-type" className={inputCls} value={t.insert_type}
              onChange={(e) => set({ threads_and_inserts: card.threads_and_inserts.map((x, j) => (j === i ? { ...x, insert_type: e.target.value as typeof t.insert_type } : x)) })}>
              {INSERT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <button type="button" aria-label="삭제" className="min-h-[44px] rounded-md bg-slate-100"
              onClick={() => set({ threads_and_inserts: card.threads_and_inserts.filter((_, j) => j !== i) })}>✕</button>
          </div>
        ))}
        <Button data-testid="spec-thread-add" variant="secondary"
          onClick={() => set({ threads_and_inserts: [...card.threads_and_inserts, { spec: "", count: 1, insert_type: "TAPPED_DIRECT" }] })}>
          + 나사·인서트
        </Button>
      </fieldset>

      {printing && (
        <fieldset className="grid grid-cols-2 gap-2 rounded-md border border-sky-200 bg-sky-50 p-2">
          <legend className="px-1 font-semibold text-sky-900">적층 사양 (3D 프린팅)</legend>
          <Field label="적층 방향 (필수 권장)">
            <input data-testid="spec-build-orientation" className={inputCls} value={card.additive_spec?.build_orientation ?? ""} placeholder="Z+ = 도면 정면"
              onChange={(e) => set({ additive_spec: { ...(card.additive_spec ?? { build_orientation: "" }), build_orientation: e.target.value } })} />
          </Field>
          <Field label="층 높이 (mm)">
            <input data-testid="spec-layer-height" type="number" step="0.05" min={0} className={inputCls} value={card.additive_spec?.layer_height_mm ?? ""}
              onChange={(e) => set({ additive_spec: { ...(card.additive_spec ?? { build_orientation: "" }), layer_height_mm: num(e.target.value) } })} />
          </Field>
          <Field label="충전율 (%)">
            <input data-testid="spec-infill" type="number" min={0} max={100} className={inputCls} value={card.additive_spec?.infill_pct ?? ""}
              onChange={(e) => set({ additive_spec: { ...(card.additive_spec ?? { build_orientation: "" }), infill_pct: num(e.target.value) } })} />
          </Field>
          <Field label="벽 루프">
            <input data-testid="spec-wall-loops" type="number" min={0} className={inputCls} value={card.additive_spec?.wall_loops ?? ""}
              onChange={(e) => set({ additive_spec: { ...(card.additive_spec ?? { build_orientation: "" }), wall_loops: num(e.target.value) } })} />
          </Field>
        </fieldset>
      )}

      <div className="grid grid-cols-3 gap-2">
        <Field label="수량">
          <input data-testid="spec-order-qty" type="number" min={1} className={inputCls} value={card.order.quantity ?? ""}
            onChange={(e) => set({ order: { ...card.order, quantity: num(e.target.value) } })} />
        </Field>
        <Field label="납기">
          <input data-testid="spec-order-due" type="date" className={inputCls} value={card.order.due_date ?? ""}
            onChange={(e) => set({ order: { ...card.order, due_date: e.target.value } })} />
        </Field>
        <Field label="검사">
          <select data-testid="spec-order-inspection" className={inputCls} value={card.order.inspection_level}
            onChange={(e) => set({ order: { ...card.order, inspection_level: e.target.value as SpecCardV2["order"]["inspection_level"] } })}>
            {INSPECTION_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
      </div>
      <Button data-testid="spec-save" type="submit" className="w-full" disabled={busy}>
        {busy ? "저장 중…" : `${side === "TARGET" ? "Target" : "Base"} 제조 사양 저장`}
      </Button>
      {msg && <p data-testid="spec-saved" className="text-emerald-700">{msg}</p>}
      <ErrorText error={error} />
    </form>
  );
}
