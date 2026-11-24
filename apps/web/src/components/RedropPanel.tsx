"use client";

/** ZERO_RETENTION: the raw STEP was destroyed after processing; re-drop the original to recompute.
 *  The browser checks SHA-256 first (instant feedback); the API re-verifies the stored object. */

import { useState } from "react";

import { errorText } from "@/lib/api";
import type { Revision } from "@/lib/types";
import { sha256Hex, uploadRevision } from "@/lib/upload";

import { Dropzone } from "./Dropzone";

export function RedropPanel({ partId, revisions, onDone }: { partId: string; revisions: Revision[]; onDone: () => void }) {
  const [msg, setMsg] = useState<Record<string, { ok: boolean; text: string }>>({});
  // purged revisions, plus the ones handled here (so the result stays visible after the reload)
  const shown = revisions.filter((r) => (!r.raw_available && r.raw_purged_at) || msg[r.id]);
  if (shown.length === 0) return null;
  return (
    <div data-testid="redrop-panel" className="space-y-2">
      <p className="text-xs text-slate-600">Zero-Retention: 원본 STEP이 파기되었습니다. 발주처 로컬의 원본을 다시 놓으면 SHA-256 일치 확인 후 즉시 재연산합니다.</p>
      {shown.map((rev) => (
        <div key={rev.id}>
          {rev.raw_available ? (
            <p className="rounded-md bg-emerald-50 px-2 py-1.5 text-xs text-emerald-800">Rev {rev.rev_label} 원본 복원됨 (SHA-256 일치)</p>
          ) : (
          <Dropzone
            testId={`redrop-${rev.rev_label}`}
            label={`Rev ${rev.rev_label} 원본 재드롭`}
            hint={`sha256 ${rev.file_sha256.slice(0, 12)}…`}
            onFiles={async ([file]) => {
              setMsg((m) => ({ ...m, [rev.id]: { ok: true, text: "SHA-256 확인 중…" } }));
              try {
                const sha = await sha256Hex(file);
                if (sha !== rev.file_sha256) {
                  setMsg((m) => ({ ...m, [rev.id]: { ok: false, text: `SHA-256 불일치 — 파기된 Rev ${rev.rev_label} 원본이 아닙니다 (${sha.slice(0, 12)}…)` } }));
                  return;
                }
                const res = await uploadRevision(partId, file, rev.rev_label);
                setMsg((m) => ({ ...m, [rev.id]: { ok: res.redrop, text: res.redrop ? "해시 검증 재드롭 완료 — 재연산할 수 있습니다" : "업로드됨" } }));
                onDone();
              } catch (e) {
                setMsg((m) => ({ ...m, [rev.id]: { ok: false, text: errorText(e) } }));
              }
            }}
          />
          )}
          {msg[rev.id] && (
            <p data-testid={`redrop-msg-${rev.rev_label}`} data-ok={msg[rev.id].ok ? "true" : "false"} className={`mt-1 text-[11px] ${msg[rev.id].ok ? "text-emerald-700" : "text-red-600"}`}>
              {msg[rev.id].text}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
