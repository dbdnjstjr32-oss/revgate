"use client";

/**
 * Designer PR-style review: left checklist ([확정] Fact vs (참고 추정) hint), center R3F viewer,
 * right measure / pins / spec card / share link. NEEDS_ALIGN swaps the checklist for the 3-2-1
 * Datum Align panel; QUEUED / COMPUTING (incl. a realign) shows the live SSE progress.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { DatumAlignPanel } from "@/components/DatumAlignPanel";
import { DiffProgress, SpecCard } from "@/components/misc";
import { RedropPanel } from "@/components/RedropPanel";
import { type NewPin, ReviewWorkbench } from "@/components/ReviewWorkbench";
import { ShareLinkPanel } from "@/components/ShareLinkPanel";
import { Card, ErrorText, StatusBadge } from "@/components/ui";
import type { PickEvent } from "@/components/viewer/DiffViewer";
import { api, errorText } from "@/lib/api";
import { shortId } from "@/lib/format";
import type { DiffItem, DiffResponse, PartDetail, Pin } from "@/lib/types";
import { useMe } from "@/lib/useMe";
import { useViewerData } from "@/lib/useViewerData";

const NO_FACES = { base: [] as number[], target: [] as number[] };

export default function DiffReviewPage() {
  const { partId, diffId } = useParams<{ partId: string; diffId: string }>();
  const { me } = useMe();
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [part, setPart] = useState<PartDetail | null>(null);
  const [pins, setPins] = useState<Pin[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [realign, setRealign] = useState(0); // > 0 while a 3-2-1 realign is being recomputed
  const [applying, setApplying] = useState(false);
  const [preview, setPreview] = useState<number[] | null>(null);
  const [datumFaces, setDatumFaces] = useState(NO_FACES);
  const pickRef = useRef<((p: PickEvent) => boolean) | null>(null);

  const loadPart = useCallback(async () => {
    try {
      setPart(await api<PartDetail>(`/v1/parts/${partId}`));
    } catch (e) {
      setError(errorText(e));
    }
  }, [partId]);

  const load = useCallback(async () => {
    try {
      const [d, pn] = await Promise.all([api<DiffResponse>(`/v1/diffs/${diffId}`), api<{ pins: Pin[] }>(`/v1/review/diffs/${diffId}/pins`)]);
      setDiff(d);
      setPins(pn.pins);
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

  const status = diff?.diff_run.status ?? null;
  const viewable = status === "READY" || status === "NEEDS_ALIGN";
  const viewer = useViewerData(viewable && diff ? diff.signed_urls : null);

  const onProgressDone = useCallback(async () => {
    await load();
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

  const run = diff?.diff_run;
  const revs = part?.revisions ?? [];
  const base = revs.find((r) => r.id === run?.base_rev_id);
  const target = revs.find((r) => r.id === run?.target_rev_id);
  const computing = realign > 0 || status === "QUEUED" || status === "COMPUTING";

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

        {diff && !computing && viewable && run && (
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
                {part && (
                  <Card title="가공 스펙 카드">
                    <SpecCard part={part.part} base={base} target={target} />
                  </Card>
                )}
                <Card title="가공업체 공유 링크 (6자리 OTP)">
                  {status === "READY" ? (
                    <ShareLinkPanel diffId={diffId} enabled />
                  ) : (
                    <p className="text-xs text-slate-500">정합이 끝나 READY가 되면 공유 링크를 발급할 수 있습니다.</p>
                  )}
                </Card>
                {base && target && [base, target].some((r) => r.raw_purged_at) && (
                  <Card title="원본 재드롭 (Zero-Retention)">
                    <RedropPanel partId={partId} revisions={[base, target]} onDone={() => void loadPart()} />
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
