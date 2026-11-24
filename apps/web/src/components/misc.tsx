"use client";

import Link from "next/link";
import { useEffect } from "react";

import { bytes, shortId } from "@/lib/format";
import type { DiffStatus, Part, Revision } from "@/lib/types";
import { stageProgress, useDiffEvents } from "@/lib/useDiffEvents";

const STAGE_LABEL: Record<string, string> = {
  QUEUED: "대기열",
  DOWNLOADING: "원본 STEP 내려받는 중",
  COMPUTING: "자동 정합 + B-Rep Diff 연산 중",
  UPLOADING: "뷰어 번들(Brotli) 업로드 중",
  READY: "완료",
  NEEDS_ALIGN: "수동 정합 필요",
  FAILED: "실패",
};

/** Live SSE progress of one diff run. */
export function DiffProgress({ diffId, nonce = 0, onDone }: { diffId: string; nonce?: number; onDone?: (status: DiffStatus) => void }) {
  const ev = useDiffEvents(diffId, nonce);
  useEffect(() => {
    if (ev.done && ev.status) onDone?.(ev.status);
  }, [ev.done, ev.status, onDone]);
  const status = ev.status ?? "QUEUED";
  const label = ev.done ? STAGE_LABEL[status] : STAGE_LABEL[ev.stage ?? status] ?? status;
  return (
    <div data-testid="diff-progress" data-diff-id={diffId} data-status={ev.done ? status : "RUNNING"} className="space-y-1">
      <div className="flex justify-between text-[11px] text-slate-600">
        <span>Diff {shortId(diffId)} · {label}</span>
        <span>{Math.round(stageProgress(ev) * 100)}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded bg-slate-200">
        <div
          className={`h-full transition-all ${status === "FAILED" ? "bg-red-500" : status === "NEEDS_ALIGN" ? "bg-sky-500" : "bg-indigo-600"}`}
          style={{ width: `${stageProgress(ev) * 100}%` }}
        />
      </div>
      <p className="font-mono text-[10px] text-slate-400" data-testid="diff-events">
        {ev.events.map((e) => e.event + (e.data.stage ? `:${String(e.data.stage)}` : "")).join(" → ")}
      </p>
    </div>
  );
}

export function SpecCard({ part, base, target }: { part: Part; base?: Revision; target?: Revision }) {
  const row = (rev: Revision | undefined, label: string) =>
    rev ? (
      <div className="rounded-md bg-slate-50 p-2">
        <p className="font-semibold text-slate-800">
          {label} · Rev {rev.rev_label}
        </p>
        <p className="font-mono text-[10px] text-slate-500">sha256 {rev.file_sha256.slice(0, 16)}… · {bytes(rev.file_size_bytes)}</p>
        <p className="text-[11px] text-slate-600">원본: {rev.raw_available ? "보관 중" : "파기됨 (Zero-Retention)"}</p>
        {Object.entries(rev.spec_card_json).map(([k, v]) => (
          <p key={k} className="text-[11px] text-slate-600">
            {k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}
          </p>
        ))}
      </div>
    ) : null;
  return (
    <div data-testid="spec-card" className="space-y-1.5 text-xs">
      <p>
        <span className="font-semibold">{part.part_number}</span> {part.name}
      </p>
      <p className="text-slate-600">
        소재 {part.default_material ?? "-"} · 후처리 {part.default_finish ?? "-"}
      </p>
      {row(base, "Base")}
      {row(target, "Target")}
    </div>
  );
}

/** Hub-and-Spoke CTA on the reviewer page (product spec §5). */
export function ShopCta() {
  return (
    <div data-testid="shop-cta" className="mt-6 rounded-xl border border-indigo-200 bg-indigo-50 p-4 text-sm text-indigo-950">
      <p className="font-semibold">다른 거래처 수정 도면도 카톡 말고 여기서 받아보세요</p>
      <p className="mt-1 text-indigo-800">우리 공장 전용 무료 도면 접수함을 만들면 발주처가 올린 v1→v2 변경점이 자동으로 체크돼서 오가공이 사라집니다. (revgate.io/shop/OO정밀)</p>
      <Link href="/login?next=/workspace%23shop-inbox" className="mt-2 inline-block rounded-md bg-indigo-600 px-3 py-1.5 font-medium text-white">
        우리 공장 전용 무료 도면 접수함 만들기
      </Link>
    </div>
  );
}
