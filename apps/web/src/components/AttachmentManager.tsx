"use client";

/** Designer: 2D drawings (PDF / DXF) per revision — presign -> PUT -> verified complete; delete = DELETED.
 *  Adding / deleting a drawing of an approved target supersedes that approval (DRAWING_UPDATED). */

import type { AttachmentItem } from "@revgate/contracts";
import { useState } from "react";

import { errorText } from "@/lib/api";
import { bytes } from "@/lib/format";
import type { Revision } from "@/lib/types";
import { deleteDrawing, uploadDrawing } from "@/lib/upload";

import { Dropzone } from "./Dropzone";
import { ErrorText } from "./ui";

export function AttachmentManager({ revision, side, attachments, onChanged }: {
  revision: Revision;
  side: "base" | "target";
  attachments: AttachmentItem[];
  onChanged: (superseded: number) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <div data-testid={`attachments-${side}`} className="space-y-1.5">
      <Dropzone
        testId={`attach-dropzone-${side}`}
        label={`${side === "base" ? "Base" : "Target"} Rev ${revision.rev_label} 2D 도면 첨부 (PDF/DXF, ≤25MB)`}
        hint="공차·나사·표면처리는 2D 도면이 STEP보다 우선합니다"
        accept=".pdf,.dxf"
        multiple
        disabled={busy}
        onFiles={async (files) => {
          setBusy(true);
          setError(null);
          setMsg(null);
          let superseded = 0;
          try {
            for (const f of files) superseded += (await uploadDrawing(revision.id, f)).superseded_links;
            setMsg(`첨부 완료 (SHA-256 검증)${superseded ? ` · 기존 승인 ${superseded}건 구버전 처리` : ""}`);
            onChanged(superseded);
          } catch (e) {
            setError(errorText(e));
            onChanged(superseded);
          } finally {
            setBusy(false);
          }
        }}
      />
      <ul className="space-y-1">
        {attachments.map((a) => (
          <li key={a.id} data-testid="attachment-item" data-kind={a.kind} className="flex items-center justify-between gap-1 rounded border border-slate-200 px-2 text-[11px]">
            <span className="truncate">
              <b>{a.kind === "2D_PDF" ? "PDF" : a.kind === "2D_DXF" ? "DXF" : "SHEET"}</b> {a.filename} <span className="text-slate-400">{bytes(a.file_size_bytes)}</span>
            </span>
            <button
              type="button"
              data-testid="attachment-delete"
              className="min-h-[44px] min-w-[44px] text-red-600 underline"
              onClick={async () => {
                setError(null);
                try {
                  const res = await deleteDrawing(revision.id, a.id);
                  setMsg(`삭제됨${res.superseded_links ? ` · 기존 승인 ${res.superseded_links}건 구버전 처리` : ""}`);
                  onChanged(res.superseded_links);
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              삭제
            </button>
          </li>
        ))}
      </ul>
      {msg && <p data-testid={`attach-msg-${side}`} className="text-[11px] text-emerald-700">{msg}</p>}
      <ErrorText error={error} />
    </div>
  );
}
