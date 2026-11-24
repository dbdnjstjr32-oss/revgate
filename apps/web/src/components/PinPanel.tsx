"use client";

import { useState } from "react";

import type { Pin } from "@/lib/types";
import type { V3 } from "@/lib/vec";

import { Button, inputCls } from "./ui";
import type { Side } from "./viewer/DiffViewer";

export interface PinDraft {
  side: Side;
  faceId: number;
  point: V3;
}

export function PinPanel({ pins, draft, canWrite, onSave, onCancel, onResolve }: {
  pins: Pin[];
  draft: PinDraft | null;
  canWrite: boolean;
  onSave: (body: string) => Promise<void>;
  onCancel: () => void;
  onResolve: (pin: Pin, resolved: boolean) => Promise<void>;
}) {
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div data-testid="pin-panel" className="space-y-2">
      {draft && (
        <div data-testid="pin-draft" className="rounded-md border border-fuchsia-300 bg-fuchsia-50 p-2">
          <p className="mb-1 text-[11px] text-fuchsia-900">
            {draft.side} 면 #{draft.faceId} · ({draft.point.map((v) => v.toFixed(1)).join(", ")})
          </p>
          <textarea data-testid="pin-body" className={inputCls} rows={2} value={body} onChange={(e) => setBody(e.target.value)} placeholder="코멘트" />
          <div className="mt-1 flex gap-1">
            <Button
              data-testid="pin-save"
              disabled={!body.trim() || busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await onSave(body.trim());
                  setBody("");
                } finally {
                  setBusy(false);
                }
              }}
            >
              핀 저장
            </Button>
            <Button variant="ghost" onClick={onCancel}>
              취소
            </Button>
          </div>
        </div>
      )}
      {!draft && canWrite && <p className="text-xs text-slate-500">핀 모드에서 3D 모델을 클릭해 면에 고정된 코멘트를 남기세요.</p>}
      <ul className="space-y-1.5">
        {pins.map((p) => (
          <li key={p.id} data-testid="pin-item" data-resolved={p.resolved ? "true" : "false"} className="rounded-md border border-slate-200 p-2 text-xs">
            <div className="flex items-center justify-between gap-1">
              <span className="font-semibold">
                📍 {p.author_label} <span className="font-normal text-slate-500">({p.author_type === "EXTERNAL" ? "가공업체" : "설계"})</span>
              </span>
              <label className="flex items-center gap-1 text-[11px] text-slate-600">
                <input
                  data-testid="pin-resolve"
                  type="checkbox"
                  checked={p.resolved}
                  disabled={!canWrite}
                  onChange={(e) => void onResolve(p, e.target.checked)}
                />
                해결
              </label>
            </div>
            <p className="mt-0.5 text-slate-800">{p.body}</p>
            <p className="text-[10px] text-slate-400">
              {p.rev_side} · face {p.face_id}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
