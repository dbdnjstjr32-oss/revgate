"use client";

/**
 * Designer PR-style review: left checklist ([확정] Fact vs (참고 추정) hint), center R3F viewer (+ 2D split view),
 * right measure / pins / manufacturing spec (SpecCardV2 editor, Base↔Target spec diff, 2D drawings) / share
 * link. NEEDS_ALIGN swaps the checklist for the 3-2-1 Datum Align panel; QUEUED / COMPUTING (incl. a realign)
 * shows the live SSE progress (with backoff + polling fallback).
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { AttachmentManager } from "@/components/AttachmentManager";
import { DatumAlignPanel } from "@/components/DatumAlignPanel";
import { DiffProgress } from "@/components/misc";
import { RedropPanel } from "@/components/RedropPanel";
import { type NewPin, ReviewWorkbench } from "@/components/ReviewWorkbench";
import { ShareLinkPanel } from "@/components/ShareLinkPanel";
import { SpecCard, SpecDiffCard, SpecWarnings } from "@/components/spec";
import { SpecEditor } from "@/components/SpecEditor";
import { Card, ErrorText, StatusBadge, Toggle } from "@/components/ui";
import type { PickEvent } from "@/components/viewer/DiffViewer";
import { api, errorText } from "@/lib/api";
import { shortId } from "@/lib/format";
import { useSignedUrlRefresh } from "@/lib/hooks";
import type { DiffItem, DiffResponse, PartDetail, Pin, SignedUrls } from "@/lib/types";
import { useMe } from "@/lib/useMe";
import { useViewerData } from "@/lib/useViewerData";

const NO_FACES = { base: [] as number[], target: [] as number[] };

export default function DiffReviewPage() {
  const { partId, diffId } = useParams<{ partId: string; diffId: string }>();
  const { me } = useMe();
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [part, setPart] = useState<PartDetail | null>(null);
  const [pins, setPins] = useState<Pin[]>([]);
  const [viewerUrls, setViewerUrls] = useState<SignedUrls | null>(null); // manifest / meta: refetched only on purpose
  const [issuedAt, setIssuedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [realign, setRealign] = useState(0); // > 0 while a 3-2-1 realign is being recomputed
  const [applying, setApplying] = useState(false);
  const [preview, setPreview] = useState<number[] | null>(null);
  const [datumFaces, setDatumFaces] = useState(NO_FACES);
  const [specSide, setSpecSide] = useState<"BASE" | "TARGET">("TARGET");
  const [linksNonce, setLinksNonce] = useState(0);
  const pickRef = useRef<((p: PickEvent) => boolean) | null>(null);

  const loadPart = useCallback(async () => {
    try {
      setPart(await api<PartDetail>(`/v1/parts/${partId}`));
    } catch (e) {
      setError(errorText(e));
    }
  }, [partId]);

  const load = useCallback(async (opts: { freshViewer?: boolean } = {}) => {
    try {
      const [d, pn] = await Promise.all([api<DiffResponse>(`/v1/diffs/${diffId}`), api<{ pins: Pin[] }>(`/v1/review/diffs/${diffId}/pins`)]);
      setDiff(d);
      setPins(pn.pins);
      setIssuedAt(Date.now());
      const viewable = d.diff_run.status === "READY" || d.diff_run.status === "NEEDS_ALIGN";
      setViewerUrls((u) => (viewable && (opts.freshViewer || !u) ? d.signed_urls : u));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
    await loadPart();
  }, [diffId, loadPart]);

  const signedIn = me !== null;
  useEffect(() => {
    if (signedIn) void load();
  }, [signedIn, load]);

  useSignedUrlRefresh(issuedAt, load); // 15 min signed URLs -> refreshed at 12 min

  const status = diff?.diff_run.status ?? null;
  const viewable = status === "READY" || status === "NEEDS_ALIGN";
  const viewer = useViewerData(viewable ? viewerUrls : null);

  const onProgressDone = useCallback(async () => {
    await load({ freshViewer: true });
    setRealign(0);
    setApplying(false);
    setPreview(null);
    setDatumFaces(NO_FACES);
  }, [load]);

  const onPreview = useCallback((t: number[] | null, faces: { base: number[]; target: number[] }) => {
    setPreview(t);
    setDatumFaces(faces);
  }, []);
  const registerPick = useCallback((h: ((p: PickEvent) => boolean) | null) => {
    pickRef.current = h;
  }, []);

  const toggleCheck = useCallback(async (item: DiffItem, checked: boolean) => {
    try {
      const res = await api<{ item: DiffItem }>(`/v1/review/diff-items/${item.id}/check`, { method: "PATCH", json: { checked } });
      setDiff((d) => (d ? { ...d, items: d.items.map((i) => (i.id === res.item.id ? res.item : i)) } : d));
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  const createPin = useCallback(
    async (pin: NewPin) => {
      const res = await api<{ pin: Pin }>(`/v1/review/diffs/${diffId}/pins`, { json: pin });
      setPins((p) => [...p, res.pin]);
    },
    [diffId],
  );

  const resolvePin = useCallback(async (pin: Pin, resolved: boolean) => {
    try {
      const res = await api<{ pin: Pin }>(`/v1/review/pins/${pin.id}/resolve`, { method: "PATCH", json: { resolved } });
      setPins((p) => p.map((x) => (x.id === res.pin.id ? res.pin : x)));
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  const applyDatum = async (transform: number[]) => {
    setApplying(true);
    setError(null);
    try {
      await api(`/v1/diffs/${diffId}/realign`, { json: { transform_4x4: transform } });
      setRealign((n) => n + 1);
    } catch (e) {
      setError(errorText(e));
      setApplying(false);
    }
  };

  const afterSpecOrDrawingChange = useCallback(async () => {
    await load();
    setLinksNonce((n) => n + 1); // approvals of the target may now be SUPERSEDED
  }, [load]);

  const run = diff?.diff_run;
  const base = diff?.revisions.base;
  const target = diff?.revisions.target;
  const computing = realign > 0 || status === "QUEUED" || status === "COMPUTING";
  const specRev = specSide === "TARGET" ? target : base;

  return (
    <>
      <AppHeader me={me}>
        <span className="flex flex-wrap items-center gap-2">
          <Link href={`/workspace?part=${partId}`} className="text-indigo-600 underline">
            {part?.part.part_number ?? "부품"}
          </Link>
          <span>
            Rev {base?.rev_label ?? "?"} → Rev {target?.rev_label ?? "?"}
          </span>
          <span className="font-mono text-xs text-slate-400">diff {shortId(diffId)}</span>
          {status && <StatusBadge testId="diff-status" status={computing ? "COMPUTING" : status} />}
          {run && (
            <span className="text-xs text-slate-500">
              {run.align_method}
              {run.align_confidence !== null ? ` · 신뢰도 ${run.align_confidence.toFixed(3)}` : ""}
            </span>
          )}
        </span>
      </AppHeader>
      <main className="mx-auto max-w-[1600px] space-y-3 p-4">
        <ErrorText error={error ?? viewer.error} />
        {!diff && !error && <p className="text-sm text-slate-500">불러오는 중…</p>}

        {diff && computing && (
          <Card title={realign > 0 ? "3-2-1 정합으로 재계산 중 (q-diff-interactive)" : "변경점 분석 중"} testId="computing-card">
            <DiffProgress diffId={diffId} nonce={realign} onDone={onProgressDone} />
          </Card>
        )}

        {diff && !computing && status === "FAILED" && (
          <Card title="분석 실패" testId="failed-card">
            <p className="text-sm text-red-700">{run?.error_code ?? "UNKNOWN"}</p>
          </Card>
        )}

        {diff && !computing && viewable && run && base && target && (
          <ReviewWorkbench
            run={run}
            items={diff.items}
            manifest={viewer.manifest}
            baseMeta={viewer.baseMeta}
            targetMeta={viewer.targetMeta}
            urls={diff.signed_urls}
            pins={pins}
            canWrite={status === "READY"}
            onToggleCheck={toggleCheck}
            onCreatePin={createPin}
            onResolvePin={resolvePin}
            previewTransform={status === "NEEDS_ALIGN" ? preview : null}
            datumFaces={status === "NEEDS_ALIGN" ? datumFaces : undefined}
            pickInterceptor={pickRef}
            attachments={diff.attachments}
            specDiffs={diff.spec_diffs}
            allowDrawingDownload
            left={
              status === "NEEDS_ALIGN" ? (
                <DatumAlignPanel
                  baseMeta={viewer.baseMeta}
                  targetMeta={viewer.targetMeta}
                  confidence={run.align_confidence}
                  applying={applying}
                  onPreview={onPreview}
                  onApply={(t) => void applyDatum(t)}
                  registerPick={registerPick}
                />
              ) : undefined
            }
            right={
              <>
                <Card title="Base ↔ Target 제조 사양 변경" testId="spec-diff-panel">
                  <SpecDiffCard diffs={diff.spec_diffs} geometryChanges={diff.items.length} />
                </Card>
                <Card
                  title="제조 사양 (SpecCardV2)"
                  testId="spec-editor-card"
                  actions={
                    <span className="flex gap-1">
                      <Toggle data-testid="spec-side-base" active={specSide === "BASE"} onClick={() => setSpecSide("BASE")}>Base</Toggle>
                      <Toggle data-testid="spec-side-target" active={specSide === "TARGET"} onClick={() => setSpecSide("TARGET")}>Target</Toggle>
                    </span>
                  }
                >
                  {specRev && (
                    <SpecEditor key={specRev.id} revision={specRev} side={specSide} onSaved={() => void afterSpecOrDrawingChange()} />
                  )}
                </Card>
                <Card title="2D 도면 (PDF / DXF)" testId="attachments-card">
                  <div className="space-y-3">
                    <AttachmentManager revision={target} side="target" attachments={diff.attachments.target} onChanged={() => void afterSpecOrDrawingChange()} />
                    <AttachmentManager revision={base} side="base" attachments={diff.attachments.base} onChanged={() => void afterSpecOrDrawingChange()} />
                  </div>
                </Card>
                {part && (
                  <Card title="가공 스펙 카드">
                    <SpecCard part={part.part} base={base} target={target} />
                  </Card>
                )}
                <Card title="가공업체 공유 링크 (6자리 OTP)">
                  {status === "READY" ? (
                    <>
                      <SpecWarnings warnings={diff.spec_warnings} />
                      <ShareLinkPanel reloadKey={linksNonce} diffId={diffId} enabled />
                    </>
                  ) : (
                    <p className="text-xs text-slate-500">정합이 끝나 READY가 되면 공유 링크를 발급할 수 있습니다.</p>
                  )}
                </Card>
                {[base, target].some((r) => r.raw_purged_at) && (
                  <Card title="원본 재드롭 (Zero-Retention)">
                    <RedropPanel partId={partId} revisions={[base, target]} onDone={() => void load()} />
                    {base.raw_available && target.raw_available && <p className="text-xs text-slate-500">두 원본 모두 보관 중입니다.</p>}
                  </Card>
                )}
              </>
            }
          />
        )}
      </main>
    </>
  );
}
