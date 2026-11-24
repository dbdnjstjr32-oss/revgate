"use client";

/**
 * Public shop inbox (Hub-and-Spoke PLG): a client of the machine shop drops v1 + v2 STEP without an
 * account -> browser SHA-256 -> 2 presigned PUTs -> complete (hash-verified) -> the shop's workspace
 * gets the part, both revisions and an immediately queued diff run.
 */

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Dropzone } from "@/components/Dropzone";
import { Button, Card, ErrorText, Field, inputCls } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { bytes, shortId } from "@/lib/format";
import { MAX_UPLOAD_BYTES, isStepFile, putFile, sha256Hex } from "@/lib/upload";

interface PresignOut {
  submission_id: string;
  uploads: Array<{ revision_id: string; rev_label: string; filename: string; put_url: string }>;
}

interface Submitted {
  part_id: string;
  diff_run_id: string;
  revision_ids: string[];
  status: string;
}

export default function ShopInboxPage() {
  const { slug } = useParams<{ slug: string }>();
  const [inbox, setInbox] = useState<{ shop_name: string; is_active: boolean } | null>(null);
  const [senderName, setSenderName] = useState("");
  const [senderContact, setSenderContact] = useState("");
  const [partNumber, setPartNumber] = useState("");
  const [files, setFiles] = useState<[File | null, File | null]>([null, null]);
  const [phase, setPhase] = useState<string | null>(null);
  const [done, setDone] = useState<Submitted | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ shop_name: string; is_active: boolean }>(`/v1/public/shop/${slug}`)
      .then(setInbox)
      .catch((e: unknown) => setError(errorText(e)));
  }, [slug]);

  const pick = (idx: 0 | 1) => (picked: File[]) => {
    const f = picked[0];
    setError(null);
    if (!isStepFile(f.name)) return setError("STEP(.step/.stp) 파일만 접수할 수 있습니다");
    if (f.size > MAX_UPLOAD_BYTES) return setError("파일당 최대 50MB까지 접수할 수 있습니다");
    setFiles((cur) => (idx === 0 ? [f, cur[1]] : [cur[0], f]));
  };

  const submit = async () => {
    const [v1, v2] = files;
    if (!v1 || !v2) return;
    setBusy(true);
    setError(null);
    try {
      setPhase("브라우저에서 SHA-256 계산 중");
      const [s1, s2] = await Promise.all([sha256Hex(v1), sha256Hex(v2)]);
      const pre = await api<PresignOut>(`/v1/public/shop/${slug}/submissions/presign`, {
        json: {
          sender_name: senderName.trim(),
          sender_contact: senderContact.trim(),
          part_number: partNumber.trim(),
          files: [
            { filename: v1.name, file_size_bytes: v1.size, file_sha256: s1, rev_label: "v1" },
            { filename: v2.name, file_size_bytes: v2.size, file_sha256: s2, rev_label: "v2" },
          ],
        },
      });
      setPhase("공장 전용 보관함으로 직접 업로드 중");
      await Promise.all(pre.uploads.map((u, i) => putFile(u.put_url, i === 0 ? v1 : v2)));
      setPhase("해시 재검증 후 변경점 분석 요청 중");
      setDone(await api<Submitted>(`/v1/public/shop/${slug}/submissions/complete`, { json: { submission_id: pre.submission_id } }));
      setPhase(null);
    } catch (e) {
      setError(errorText(e));
      setPhase(null);
    } finally {
      setBusy(false);
    }
  };

  const ready = files[0] && files[1] && senderName.trim() && senderContact.trim().length >= 3 && partNumber.trim();

  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      <p className="text-sm font-black tracking-tight text-indigo-700">RevGate</p>
      <h1 data-testid="shop-title" className="mt-1 text-2xl font-bold text-slate-900">
        {inbox ? `${inbox.shop_name} 전용 도면 접수함` : "도면 접수함"}
      </h1>
      <p className="mt-1 text-sm text-slate-600">카톡 대신 여기에 수정 전(v1)·수정 후(v2) STEP을 올리면 변경점이 자동으로 체크되어 공장에 전달됩니다. 회원가입은 필요 없습니다.</p>

      {inbox && !inbox.is_active && (
        <Card className="mt-6" testId="shop-closed">
          <p className="text-sm text-slate-700">현재 접수를 받지 않는 접수함입니다.</p>
        </Card>
      )}

      {done ? (
        <Card className="mt-6" testId="shop-success" title="접수 완료">
          <div data-diff-run-id={done.diff_run_id} data-part-id={done.part_id} data-testid="shop-receipt" className="space-y-1 text-sm text-slate-700">
            <p>
              {inbox?.shop_name ?? "공장"}에 <b>{partNumber}</b> v1 → v2 도면이 접수되었고, 변경점 자동 분석을 바로 시작했습니다.
            </p>
            <p className="font-mono text-xs text-slate-500">접수번호 {shortId(done.diff_run_id)} · 상태 {done.status}</p>
          </div>
        </Card>
      ) : (
        inbox?.is_active && (
          <Card className="mt-6" testId="shop-form">
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              <div className="grid gap-2 sm:grid-cols-3">
                <Field label="보내는 분 (회사/이름)">
                  <input data-testid="shop-sender" className={inputCls} value={senderName} onChange={(e) => setSenderName(e.target.value)} placeholder="ㅇㅇ전자 김설계" />
                </Field>
                <Field label="연락처">
                  <input data-testid="shop-contact" className={inputCls} value={senderContact} onChange={(e) => setSenderContact(e.target.value)} placeholder="designer@client.com" />
                </Field>
                <Field label="품번">
                  <input data-testid="shop-part-number" className={inputCls} value={partNumber} onChange={(e) => setPartNumber(e.target.value)} placeholder="BRK-001" />
                </Field>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {([0, 1] as const).map((idx) => (
                  <div key={idx}>
                    <Dropzone
                      testId={idx === 0 ? "shop-v1" : "shop-v2"}
                      label={idx === 0 ? "수정 전 (v1) STEP" : "수정 후 (v2) STEP"}
                      hint={files[idx] ? `${files[idx]!.name} · ${bytes(files[idx]!.size)}` : ".step / .stp, 최대 50MB"}
                      disabled={busy}
                      onFiles={pick(idx)}
                    />
                  </div>
                ))}
              </div>
              <Button data-testid="shop-submit" type="submit" className="w-full" disabled={!ready || busy}>
                변경점 자동 분석 요청
              </Button>
              {phase && (
                <p data-testid="shop-phase" className="text-xs text-slate-600">
                  {phase}…
                </p>
              )}
            </form>
          </Card>
        )
      )}
      <ErrorText error={error} />
    </main>
  );
}
