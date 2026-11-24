"use client";

/**
 * External reviewer gate (no account): link meta -> 6-digit OTP -> rg_ext_sid (24 h, this link only)
 * -> watermarked viewer with measure / pins / checklist -> "가공 착수 승인" -> approval PDF (WORM)
 * -> Hub-and-Spoke shop-inbox CTA. Removing or hiding the watermark blurs the 3D canvas at once.
 */

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ShopCta, SpecCard } from "@/components/misc";
import { type NewPin, ReviewWorkbench } from "@/components/ReviewWorkbench";
import { Button, Card, ErrorText, StatusBadge, inputCls } from "@/components/ui";
import { Watermark } from "@/components/Watermark";
import { ApiError, api, devOutbox, errorText } from "@/lib/api";
import { when } from "@/lib/format";
import type { BundleResponse, DiffItem, Pin, SignedUrls } from "@/lib/types";
import { useViewerData } from "@/lib/useViewerData";

interface LinkMeta {
  part_number: string;
  rev_pair: { base: string; target: string };
  change_count: number;
  masked_contact: string;
  status: string;
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
          className={`${inputCls} text-center font-mono text-lg tracking-[0.4em]`}
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

function ApproveCard({ token, bundle, approval, onApproved }: {
  token: string;
  bundle: BundleResponse;
  approval: Approval | null;
  onApproved: (a: Approval) => void;
}) {
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const total = bundle.items.length;
  const checked = bundle.items.filter((i) => i.checked).length;
  const approved = bundle.link.status === "APPROVED" || approval !== null;
  const raw = bundle.signed_urls.raw;

  if (approved) {
    return (
      <Card title="가공 착수 승인 완료" testId="approved-card">
        <div className="space-y-1.5 text-xs text-slate-700">
          <p data-testid="approved-at" className="font-semibold text-violet-800">
            ✔ {when(approval?.approved_at ?? bundle.link.approved_at)} 승인 · 스냅샷이 잠겼습니다
          </p>
          {approval && (
            <>
              <a
                data-testid="approval-pdf"
                href={approval.pdf_signed_url}
                target="_blank"
                rel="noreferrer"
                className="inline-block rounded-md bg-violet-600 px-3 py-1.5 font-medium text-white"
              >
                승인 증빙 PDF 다운로드
              </a>
              <p className="break-all font-mono text-[10px] text-slate-500">pdf sha256 {approval.pdf_sha256}</p>
              <p className="break-all font-mono text-[10px] text-slate-500">snapshot sha256 {approval.snapshot_sha256}</p>
            </>
          )}
          {!approval && <p className="text-slate-500">증빙 PDF는 WORM 버킷에 보관되어 있으며 발주처에서 다시 받을 수 있습니다.</p>}
          {raw && (raw.base || raw.target) && (
            <div data-testid="raw-downloads" className="space-x-2 pt-1">
              {raw.base && (
                <a href={raw.base} className="text-indigo-600 underline">
                  원본 STEP (Rev {bundle.revisions.base.rev_label})
                </a>
              )}
              {raw.target && (
                <a href={raw.target} className="text-indigo-600 underline">
                  원본 STEP (Rev {bundle.revisions.target.rev_label})
                </a>
              )}
            </div>
          )}
        </div>
      </Card>
    );
  }

  return (
    <Card title="가공 착수 승인" testId="approve-card">
      <div className="space-y-2 text-xs text-slate-700">
        <p>
          변경점 확인 <b data-testid="approve-progress">{checked}/{total}</b>
        </p>
        <label className="flex items-start gap-2">
          <input data-testid="approve-agree" type="checkbox" className="mt-0.5" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
          <span>표시된 변경점을 모두 확인했으며, 이 스냅샷(형상·체크·핀) 기준으로 가공 착수를 승인합니다.</span>
        </label>
        <Button
          data-testid="approve-submit"
          className="w-full"
          disabled={!agree || checked < total || busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              onApproved(await api<Approval>(`/v1/public/links/${token}/approve`, { json: {} }));
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
  const [urls, setUrls] = useState<SignedUrls | null>(null); // viewer assets: fixed after the first bundle
  const [pins, setPins] = useState<Pin[]>([]);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [tampered, setTampered] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadBundle = useCallback(async () => {
    try {
      const b = await api<BundleResponse>(`/v1/public/links/${token}/bundle`);
      setBundle(b);
      setPins(b.pins);
      setUrls((u) => u ?? b.signed_urls);
      setGate("ready");
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && (e.code === "OTP_REQUIRED" || e.code === "LINK_SCOPE")) {
        setGate("otp");
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

  const viewer = useViewerData(urls);
  const onTamper = useCallback(() => setTampered(true), []);

  const toggleCheck = useCallback(async (item: DiffItem, checked: boolean) => {
    try {
      const res = await api<{ item: DiffItem }>(`/v1/review/diff-items/${item.id}/check`, { method: "PATCH", json: { checked } });
      setBundle((b) => (b ? { ...b, items: b.items.map((i) => (i.id === res.item.id ? res.item : i)) } : b));
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  const createPin = useCallback(
    async (pin: NewPin) => {
      if (!bundle) return;
      const res = await api<{ pin: Pin }>(`/v1/review/diffs/${bundle.diff_run.id}/pins`, { json: pin });
      setPins((p) => [...p, res.pin]);
    },
    [bundle],
  );

  const resolvePin = useCallback(async (pin: Pin, resolved: boolean) => {
    try {
      const res = await api<{ pin: Pin }>(`/v1/review/pins/${pin.id}/resolve`, { method: "PATCH", json: { resolved } });
      setPins((p) => p.map((x) => (x.id === res.pin.id ? res.pin : x)));
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  const approved = bundle?.link.status === "APPROVED" || approval !== null;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
          <span className="text-base font-black tracking-tight text-indigo-700">RevGate</span>
          {(bundle || meta) && (
            <span className="text-slate-700">
              {bundle?.part.part_number ?? meta?.part_number} · Rev {bundle?.revisions.base.rev_label ?? meta?.rev_pair.base} → Rev{" "}
              {bundle?.revisions.target.rev_label ?? meta?.rev_pair.target}
            </span>
          )}
          {bundle && <span className="text-xs text-slate-500">열람: {bundle.link.recipient_name} ({bundle.link.masked_contact})</span>}
          {bundle && <StatusBadge testId="link-status" status={approved ? "APPROVED" : bundle.link.status} />}
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
            <ErrorText error={error ?? viewer.error} />
            <ReviewWorkbench
              run={bundle.diff_run}
              items={bundle.items}
              manifest={viewer.manifest}
              baseMeta={viewer.baseMeta}
              targetMeta={viewer.targetMeta}
              urls={urls ?? bundle.signed_urls}
              pins={pins}
              canWrite={!approved && bundle.link.status === "ACTIVE"}
              onToggleCheck={toggleCheck}
              onCreatePin={createPin}
              onResolvePin={resolvePin}
              blurred={tampered}
              right={
                <>
                  <ApproveCard
                    token={token}
                    bundle={bundle}
                    approval={approval}
                    onApproved={(a) => {
                      setApproval(a);
                      void loadBundle();
                    }}
                  />
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
