"use client";

/**
 * External reviewer gate (no account): link meta -> 6-digit OTP -> rg_ext_sid (24 h, this link only)
 * -> watermarked viewer with measure / pins / checklist / 2D drawings -> "가공 착수 승인" -> approval PDF (WORM)
 * -> Hub-and-Spoke shop-inbox CTA. Removing or hiding the watermark blurs the 3D canvas and the drawing at once.
 * Phase 4: SUPERSEDED banner (no original STEP), spec diff + missing-spec warnings with forced acknowledgement,
 * process DFM checklist, offline queue for checks / pins, 12 min signed URL refresh.
 */

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ShopCta } from "@/components/misc";
import { type NewPin, ReviewWorkbench } from "@/components/ReviewWorkbench";
import { SpecCard, SpecDiffCard, SpecWarnings } from "@/components/spec";
import { Button, Card, ErrorText, StatusBadge, inputCls } from "@/components/ui";
import { Watermark } from "@/components/Watermark";
import { ApiError, api, devOutbox, errorText } from "@/lib/api";
import { when } from "@/lib/format";
import { useSignedUrlRefresh } from "@/lib/hooks";
import { OfflineQueue, type QueuedOp, isNetworkError } from "@/lib/offlineQueue";
import { PROCESS_LABEL, SPEC_ACK_TEXT, SUPERSEDED_REASON, SUPERSEDED_TEXT, ackRequired, dfmChecksFor } from "@/lib/spec";
import type { BundleResponse, DiffItem, Pin, SignedUrls } from "@/lib/types";
import { useViewerData } from "@/lib/useViewerData";

interface LinkMeta {
  part_number: string;
  rev_pair: { base: string; target: string };
  change_count: number;
  masked_contact: string;
  status: string;
  effective_status?: string;
}

interface Approval {
  approved_at: string;
  pdf_signed_url: string;
  pdf_sha256: string;
  snapshot_sha256: string;
}

type Gate = "loading" | "otp" | "ready" | "closed";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function OtpGate({ token, meta, onVerified }: { token: string; meta: LinkMeta | null; onVerified: () => void }) {
  const [sent, setSent] = useState<{ ttl_sec: number; masked_contact: string } | null>(null);
  const [code, setCode] = useState("");
  const [devCode, setDevCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const request = async () => {
    setBusy(true);
    setError(null);
    setDevCode(null);
    try {
      setSent(await api<{ ttl_sec: number; masked_contact: string }>(`/v1/public/links/${token}/otp/request`, { json: {} }));
      // local-development hint: no SMS/mail provider yet, the code is mirrored into rg:dev:outbox
      for (let i = 0; i < 10; i++) {
        const [msg] = await devOutbox({ link_token: token, kind: "OTP", limit: "1" });
        if (msg?.data?.code) {
          setDevCode(msg.data.code);
          break;
        }
        await sleep(300);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/v1/public/links/${token}/otp/verify`, { json: { code } });
      onVerified();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="가공업체 본인 확인 (6자리 인증번호)" testId="otp-card" className="mx-auto max-w-md">
      {meta && (
        <div data-testid="link-meta" className="mb-3 rounded-md bg-slate-50 p-2 text-xs text-slate-700">
          <p className="text-sm font-semibold text-slate-900">
            {meta.part_number} · Rev {meta.rev_pair.base} → Rev {meta.rev_pair.target}
          </p>
          <p>
            변경점 {meta.change_count}건 · 수신 {meta.masked_contact}
          </p>
          {meta.effective_status === "SUPERSEDED" && <p className="mt-1 font-semibold text-red-700">이 승인은 구버전(SUPERSEDED)입니다.</p>}
        </div>
      )}
      <p className="mb-2 text-xs text-slate-600">회원가입 없이 등록된 연락처로 받은 6자리 인증번호만 입력하면 바로 열람합니다.</p>
      <Button data-testid="otp-request" className="w-full" disabled={busy} onClick={() => void request()}>
        {sent ? "인증번호 다시 받기" : "인증번호 받기"}
      </Button>
      {sent && (
        <p data-testid="otp-sent" className="mt-2 text-xs text-emerald-700">
          {sent.masked_contact} 으로 인증번호를 보냈습니다 ({Math.round(sent.ttl_sec / 60)}분 유효)
        </p>
      )}
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void verify();
        }}
      >
        <input
          data-testid="otp-code"
          className={`${inputCls} min-h-[44px] text-center font-mono text-lg tracking-[0.4em]`}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
          placeholder="000000"
        />
        <Button data-testid="otp-verify" type="submit" disabled={busy || !/^\d{6}$/.test(code)}>
          확인
        </Button>
      </form>
      {devCode && (
        <div data-testid="dev-otp" className="mt-3 flex items-center justify-between rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
          <span>
            DEV · rg:dev:outbox 인증번호 <b data-testid="dev-otp-code" className="font-mono">{devCode}</b>
          </span>
          <Button data-testid="dev-otp-fill" variant="secondary" onClick={() => setCode(devCode)}>
            채우기
          </Button>
        </div>
      )}
      <ErrorText error={error} />
    </Card>
  );
}

function DfmChecklist({ process, checked, disabled, onChange }: {
  process: BundleResponse["revisions"]["target"]["spec_card"]["process"];
  checked: string[];
  disabled: boolean;
  onChange: (ids: string[]) => void;
}) {
  const checks = dfmChecksFor(process);
  if (!checks.length) {
    return <p data-testid="dfm-none" className="text-[11px] text-slate-500">{PROCESS_LABEL[process]} 공정은 DFM 체크 항목이 없습니다.</p>;
  }
  return (
    <fieldset data-testid="dfm-checklist" data-process={process} className="space-y-0.5 rounded-md border border-slate-200 p-2">
      <legend className="px-1 text-[11px] font-semibold text-slate-700">공정별 주의 체크 · {PROCESS_LABEL[process]} (자동 판정 아님)</legend>
      {checks.map((c) => (
        <label key={c.id} className="flex min-h-[44px] cursor-pointer items-center gap-2 rounded px-1 hover:bg-slate-50">
          <input
            type="checkbox"
            data-testid={`dfm-${c.id}`}
            className="h-5 w-5 shrink-0"
            checked={checked.includes(c.id)}
            disabled={disabled}
            onChange={(e) => onChange(e.target.checked ? [...checked, c.id] : checked.filter((x) => x !== c.id))}
          />
          <span>
            <b>{c.label}</b> <span className="text-slate-500">{c.hint}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function ApproveCard({ token, bundle, approval, pending, onApproved }: {
  token: string;
  bundle: BundleResponse;
  approval: Approval | null;
  pending: number;
  onApproved: (a: Approval) => void;
}) {
  const [agree, setAgree] = useState(false);
  const [specAck, setSpecAck] = useState(false);
  const [dfm, setDfm] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const total = bundle.items.length;
  const checked = bundle.items.filter((i) => i.checked).length;
  const approved = bundle.link.status === "APPROVED" || approval !== null;
  const superseded = bundle.link.effective_status === "SUPERSEDED";
  const warnings = bundle.spec_warnings;
  const forced = ackRequired(warnings);
  const process = bundle.revisions.target.spec_card.process;
  const raw = bundle.signed_urls.raw;

  if (approved) {
    return (
      <Card title={superseded ? "승인 기록 (구버전)" : "가공 착수 승인 완료"} testId="approved-card">
        <div className="space-y-1.5 text-xs text-slate-700">
          <p data-testid="approved-at" className={`font-semibold ${superseded ? "text-red-700" : "text-violet-800"}`}>
            ✔ {when(approval?.approved_at ?? bundle.link.approved_at)} 승인 · 스냅샷이 잠겼습니다
          </p>
          <p data-testid="snapshot-sha" className="break-all font-mono text-[10px] text-slate-500">
            snapshot sha256 {approval?.snapshot_sha256 ?? bundle.link.approval_snapshot_sha256 ?? "-"}
          </p>
          {approval && (
            <>
              <a data-testid="approval-pdf" href={approval.pdf_signed_url} target="_blank" rel="noreferrer"
                className="inline-flex min-h-[44px] items-center rounded-md bg-violet-600 px-3 font-medium text-white">
                승인 증빙 PDF 다운로드
              </a>
              <p className="break-all font-mono text-[10px] text-slate-500">pdf sha256 {approval.pdf_sha256}</p>
            </>
          )}
          {!approval && <p className="text-slate-500">증빙 PDF는 WORM 버킷에 보관되어 있으며 발주처에서 다시 받을 수 있습니다.</p>}
          {superseded ? (
            <p data-testid="raw-blocked" className="rounded bg-red-50 px-2 py-1 font-semibold text-red-700">
              구버전 승인 — 원본 STEP 다운로드가 차단되었습니다 ({SUPERSEDED_REASON[bundle.link.superseded_reason ?? ""] ?? bundle.link.superseded_reason})
            </p>
          ) : (
            raw && (raw.base || raw.target) && (
              <div data-testid="raw-downloads" className="space-x-2 pt-1">
                {raw.base && <a href={raw.base} className="text-indigo-600 underline">원본 STEP (Rev {bundle.revisions.base.rev_label})</a>}
                {raw.target && <a href={raw.target} className="text-indigo-600 underline">원본 STEP (Rev {bundle.revisions.target.rev_label})</a>}
              </div>
            )
          )}
        </div>
      </Card>
    );
  }

  const ready = agree && checked === total && (forced.length === 0 || specAck) && pending === 0;
  return (
    <Card title="가공 착수 승인" testId="approve-card">
      <div className="space-y-2 text-xs text-slate-700">
        <SpecWarnings warnings={warnings} />
        {forced.length > 0 && (
          <label className="flex min-h-[44px] items-start gap-2 rounded-md bg-red-50 p-2 font-semibold text-red-800 ring-1 ring-red-200">
            <input data-testid="spec-ack" type="checkbox" className="mt-0.5 h-5 w-5 shrink-0" checked={specAck} onChange={(e) => setSpecAck(e.target.checked)} />
            <span>{SPEC_ACK_TEXT}</span>
          </label>
        )}
        <DfmChecklist process={process} checked={dfm} disabled={busy} onChange={setDfm} />
        <p>
          변경점 확인 <b data-testid="approve-progress">{checked}/{total}</b>
        </p>
        <label className="flex min-h-[44px] items-start gap-2">
          <input data-testid="approve-agree" type="checkbox" className="mt-0.5 h-5 w-5 shrink-0" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
          <span>표시된 변경점·제조 사양·2D 도면을 확인했으며, 이 스냅샷(형상·사양·체크·핀) 기준으로 가공 착수를 승인합니다.</span>
        </label>
        <Button
          data-testid="approve-submit"
          className="w-full"
          disabled={!ready || busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              onApproved(await api<Approval>(`/v1/public/links/${token}/approve`, {
                json: { acked_warnings: specAck ? warnings.map((w) => w.code) : [], dfm_ack: { process, checked_ids: dfm } },
              }));
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          가공 착수 승인
        </Button>
        {checked < total && <p className="text-amber-700">모든 변경점을 체크해야 승인할 수 있습니다.</p>}
        {forced.length > 0 && !specAck && <p className="text-red-700">누락된 사양 확인란을 체크해야 승인할 수 있습니다.</p>}
        {pending > 0 && <p className="text-amber-700">오프라인 대기 {pending}건이 동기화된 뒤 승인할 수 있습니다.</p>}
        <ErrorText error={error} />
      </div>
    </Card>
  );
}

export default function ExternalReviewPage() {
  const { token } = useParams<{ token: string }>();
  const [meta, setMeta] = useState<LinkMeta | null>(null);
  const [gate, setGate] = useState<Gate>("loading");
  const [bundle, setBundle] = useState<BundleResponse | null>(null);
  const [viewerUrls, setViewerUrls] = useState<SignedUrls | null>(null); // manifest/meta fetched once
  const [issuedAt, setIssuedAt] = useState<number | null>(null);
  const [pins, setPins] = useState<Pin[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [tampered, setTampered] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
  const [online, setOnline] = useState(true);

  const queue = useMemo(
    () => new OfflineQueue(typeof window === "undefined" ? null : window.sessionStorage, `rg:offline:${token}`),
    [token],
  );

  const loadBundle = useCallback(async () => {
    try {
      const b = await api<BundleResponse>(`/v1/public/links/${token}/bundle`);
      setBundle(b);
      setPins(b.pins);
      setViewerUrls((u) => u ?? b.signed_urls);
      setIssuedAt(Date.now());
      setGate("ready");
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && (e.code === "OTP_REQUIRED" || e.code === "LINK_SCOPE")) {
        setGate("otp");
      } else if (isNetworkError(e)) {
        setError(errorText(e)); // keep showing the current bundle while offline
      } else {
        setGate("closed");
        setError(errorText(e));
      }
    }
  }, [token]);

  useEffect(() => {
    api<LinkMeta>(`/v1/public/links/${token}/meta`)
      .then(setMeta)
      .catch((e: unknown) => {
        setGate("closed");
        setError(errorText(e));
      });
    void loadBundle();
  }, [token, loadBundle]);

  useSignedUrlRefresh(issuedAt, loadBundle); // 15 min signed URLs -> refreshed at 12 min

  const viewer = useViewerData(viewerUrls);
  const onTamper = useCallback(() => setTampered(true), []);

  const runOp = useCallback(async (op: QueuedOp) => {
    if (op.kind === "check") {
      const res = await api<{ item: DiffItem }>(`/v1/review/diff-items/${op.itemId}/check`, { method: "PATCH", json: { checked: op.checked } });
      setBundle((b) => (b ? { ...b, items: b.items.map((i) => (i.id === res.item.id ? res.item : i)) } : b));
    } else {
      const res = await api<{ pin: Pin }>(`/v1/review/diffs/${op.diffId}/pins`, { json: op.pin });
      setPins((p) => [...p, res.pin]);
    }
  }, []);

  // offline queue: replay on `online` (and leftovers from a previous load)
  useEffect(() => {
    const flush = async () => {
      setOnline(true);
      const res = await queue.flush(runOp);
      setPending(res.remaining.length);
    };
    const offline = () => setOnline(false);
    setPending(queue.list().length);
    setOnline(navigator.onLine);
    if (navigator.onLine && queue.list().length) void flush();
    window.addEventListener("online", flush);
    window.addEventListener("offline", offline);
    return () => {
      window.removeEventListener("online", flush);
      window.removeEventListener("offline", offline);
    };
  }, [queue, runOp]);

  const toggleCheck = useCallback(async (item: DiffItem, checked: boolean) => {
    setBundle((b) => (b ? { ...b, items: b.items.map((i) => (i.id === item.id ? { ...i, checked } : i)) } : b)); // optimistic
    try {
      await runOp({ id: "now", kind: "check", itemId: item.id, checked });
    } catch (e) {
      if (isNetworkError(e)) {
        setPending(queue.push({ kind: "check", itemId: item.id, checked }).length);
      } else {
        setBundle((b) => (b ? { ...b, items: b.items.map((i) => (i.id === item.id ? item : i)) } : b));
        setError(errorText(e));
      }
    }
  }, [queue, runOp]);

  const createPin = useCallback(async (pin: NewPin) => {
    if (!bundle) return;
    try {
      await runOp({ id: "now", kind: "pin", diffId: bundle.diff_run.id, pin: { ...pin } });
    } catch (e) {
      if (!isNetworkError(e)) throw e;
      setPending(queue.push({ kind: "pin", diffId: bundle.diff_run.id, pin: { ...pin } }).length);
    }
  }, [bundle, queue, runOp]);

  const resolvePin = useCallback(async (pin: Pin, resolved: boolean) => {
    try {
      const res = await api<{ pin: Pin }>(`/v1/review/pins/${pin.id}/resolve`, { method: "PATCH", json: { resolved } });
      setPins((p) => p.map((x) => (x.id === res.pin.id ? res.pin : x)));
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  const approved = bundle?.link.status === "APPROVED" || approval !== null;
  const superseded = bundle?.link.effective_status === "SUPERSEDED";
  const newer = bundle && bundle.link.latest_revision_label && bundle.link.latest_revision_label !== bundle.revisions.target.rev_label && !superseded;

  return (
    <div className="min-h-screen">
      {superseded && (
        <div role="alert" data-testid="superseded-banner" data-reason={bundle?.link.superseded_reason ?? ""}
          className="sticky top-0 z-50 bg-red-700 px-4 py-3 text-center text-sm font-bold text-white shadow-lg">
          {SUPERSEDED_TEXT}
          {bundle?.link.superseded_reason && (
            <span className="ml-2 rounded bg-white/20 px-1.5 py-0.5 text-xs font-semibold">{SUPERSEDED_REASON[bundle.link.superseded_reason]}</span>
          )}
        </div>
      )}
      <header className={`sticky ${superseded ? "top-12" : "top-0"} z-30 border-b border-slate-200 bg-white/90 backdrop-blur`}>
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
          <span className="text-base font-black tracking-tight text-indigo-700">RevGate</span>
          {(bundle || meta) && (
            <span className="text-slate-700">
              {bundle?.part.part_number ?? meta?.part_number} · Rev {bundle?.revisions.base.rev_label ?? meta?.rev_pair.base} → Rev{" "}
              {bundle?.revisions.target.rev_label ?? meta?.rev_pair.target}
            </span>
          )}
          {bundle && <span className="text-xs text-slate-500">열람: {bundle.link.recipient_name} ({bundle.link.masked_contact})</span>}
          {bundle && <StatusBadge testId="link-status" status={superseded ? "SUPERSEDED" : approved ? "APPROVED" : bundle.link.status} />}
          {!online && <span data-testid="offline-indicator" className="rounded bg-slate-800 px-2 py-0.5 text-xs font-semibold text-white">오프라인</span>}
          {pending > 0 && (
            <span data-testid="offline-queue" data-count={pending} className="rounded bg-amber-500 px-2 py-0.5 text-xs font-semibold text-white">
              동기화 대기 {pending}건
            </span>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] space-y-3 p-4">
        {gate === "loading" && <p className="text-sm text-slate-500">링크 확인 중…</p>}
        {gate === "otp" && <OtpGate token={token} meta={meta} onVerified={() => void loadBundle()} />}
        {gate === "closed" && (
          <Card title="열람할 수 없는 링크" testId="link-closed" className="mx-auto max-w-md">
            <p className="text-sm text-slate-700">{meta ? `이 링크는 ${meta.status} 상태입니다.` : "링크를 찾을 수 없습니다."}</p>
            <ErrorText error={error} />
          </Card>
        )}

        {gate === "ready" && bundle && (
          <>
            <Watermark text={bundle.watermark_text} onTamper={onTamper} />
            {tampered && (
              <div role="alert" data-testid="tamper-alert" className="rounded-md bg-red-600 px-3 py-2 text-sm font-semibold text-white">
                워터마크 변조가 감지되어 3D 화면을 가렸습니다. 페이지를 새로고침하세요.
              </div>
            )}
            {newer && (
              <p data-testid="newer-revision-note" className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900 ring-1 ring-amber-200">
                이 부품에 더 최신 리비전(Rev {bundle.link.latest_revision_label})이 등록되어 있습니다. 발주처에 최신 링크인지 확인하세요.
              </p>
            )}
            <ErrorText error={error ?? viewer.error} />
            <ReviewWorkbench
              run={bundle.diff_run}
              items={bundle.items}
              manifest={viewer.manifest}
              baseMeta={viewer.baseMeta}
              targetMeta={viewer.targetMeta}
              urls={bundle.signed_urls}
              pins={pins}
              canWrite={!approved && bundle.link.status === "ACTIVE"}
              onToggleCheck={toggleCheck}
              onCreatePin={createPin}
              onResolvePin={resolvePin}
              blurred={tampered}
              attachments={bundle.attachments}
              specDiffs={bundle.spec_diffs}
              watermarkText={bundle.watermark_text}
              allowDrawingDownload={bundle.link.allow_raw_download && !superseded}
              right={
                <>
                  <ApproveCard
                    token={token}
                    bundle={bundle}
                    approval={approval}
                    pending={pending}
                    onApproved={(a) => {
                      setApproval(a);
                      void loadBundle();
                    }}
                  />
                  <Card title="Base ↔ Target 제조 사양 변경">
                    <SpecDiffCard diffs={bundle.spec_diffs} geometryChanges={bundle.items.length} />
                  </Card>
                  <Card title="가공 스펙 카드">
                    <SpecCard part={bundle.part} base={bundle.revisions.base} target={bundle.revisions.target} />
                  </Card>
                </>
              }
            />
            {approved && <ShopCta />}
          </>
        )}
      </main>
    </div>
  );
}
