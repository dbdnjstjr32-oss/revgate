"use client";

import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { when } from "@/lib/format";
import type { ReviewLink } from "@/lib/types";

import { Button, ErrorText, Field, StatusBadge, inputCls } from "./ui";

export function ShareLinkPanel({ diffId, enabled }: { diffId: string; enabled: boolean }) {
  const [links, setLinks] = useState<ReviewLink[]>([]);
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [days, setDays] = useState(7);
  const [allowRaw, setAllowRaw] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setLinks((await api<{ items: ReviewLink[] }>(`/v1/diffs/${diffId}/links`)).items);
    } catch (e) {
      setError(errorText(e));
    }
  }, [diffId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div data-testid="share-panel" className="space-y-2">
      <Field label="가공업체 이름">
        <input data-testid="share-name" className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="OO정밀" />
      </Field>
      <Field label="연락처 (이메일/휴대폰)">
        <input data-testid="share-contact" className={inputCls} value={contact} onChange={(e) => setContact(e.target.value)} placeholder="shop@example.com" />
      </Field>
      <div className="flex items-end gap-2">
        <Field label="유효 기간(일)">
          <input type="number" min={1} max={90} className={inputCls} value={days} onChange={(e) => setDays(Number(e.target.value))} />
        </Field>
        <label className="mb-1.5 flex items-center gap-1 text-xs text-slate-600">
          <input type="checkbox" checked={allowRaw} onChange={(e) => setAllowRaw(e.target.checked)} /> 승인 후 원본 다운로드
        </label>
      </div>
      <Button
        data-testid="share-create"
        className="w-full"
        disabled={!enabled || !name.trim() || contact.trim().length < 3}
        onClick={async () => {
          setError(null);
          try {
            const res = await api<{ link_id: string; share_url: string }>(`/v1/diffs/${diffId}/links`, {
              json: { recipient_name: name.trim(), recipient_contact: contact.trim(), expires_in_days: days, allow_raw_download: allowRaw },
            });
            setShareUrl(res.share_url);
            await reload();
          } catch (e) {
            setError(errorText(e));
          }
        }}
      >
        공유 링크 발급
      </Button>
      {shareUrl && (
        <div className="rounded-md bg-emerald-50 p-2 text-[11px] text-emerald-900">
          <p className="font-semibold">카톡에 파일 대신 이 링크를 보내세요 (토큰은 다시 표시되지 않습니다)</p>
          <a data-testid="share-url" href={shareUrl} target="_blank" rel="noreferrer" className="break-all font-mono underline">
            {shareUrl}
          </a>
        </div>
      )}
      <ErrorText error={error} />
      <ul className="space-y-1">
        {links.map((l) => (
          <li key={l.id} data-testid="link-item" data-status={l.status} className="flex items-center justify-between gap-1 rounded border border-slate-200 px-2 py-1 text-[11px]">
            <span>
              {l.recipient_name} <span className="text-slate-400">{l.masked_contact}</span>
            </span>
            <span className="flex items-center gap-1">
              <StatusBadge status={l.status} />
              {l.status === "APPROVED" && (
                <button
                  type="button"
                  data-testid="link-pdf"
                  className="text-indigo-600 underline"
                  title={`승인 ${when(l.approved_at)}`}
                  onClick={async () => {
                    const { pdf_signed_url } = await api<{ pdf_signed_url: string }>(`/v1/links/${l.id}/approval-pdf`);
                    window.open(pdf_signed_url, "_blank", "noopener");
                  }}
                >
                  증빙 PDF
                </button>
              )}
              {l.status === "ACTIVE" && (
                <button
                  type="button"
                  className="text-slate-500 underline"
                  onClick={async () => {
                    await api(`/v1/links/${l.id}`, { method: "DELETE" });
                    await reload();
                  }}
                >
                  폐기
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
