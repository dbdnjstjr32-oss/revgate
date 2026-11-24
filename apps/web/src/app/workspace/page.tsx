"use client";

/**
 * Designer workspace: security settings (OWNER), shop inbox, parts, drag & drop STEP upload
 * (browser SHA-256 -> presigned PUT -> complete), Zero-Retention re-drop, diff creation + live SSE progress.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { Dropzone } from "@/components/Dropzone";
import { DiffProgress } from "@/components/misc";
import { RedropPanel } from "@/components/RedropPanel";
import { Button, Card, ErrorText, Field, StatusBadge, inputCls } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { bytes, shortId, when } from "@/lib/format";
import type { DiffStatus, Part, PartDetail, Revision, SecurityMode, Workspace } from "@/lib/types";
import { type StagedUpload, type UploadPhase, completeRevision, stageRevision } from "@/lib/upload";
import { type Me, useMe } from "@/lib/useMe";

const PHASE_LABEL: Record<UploadPhase, string> = {
  hashing: "브라우저에서 SHA-256 계산 중",
  uploading: "R2(MinIO)로 직접 업로드 중",
  verifying: "서버 해시 재검증 중",
  done: "업로드 완료 — 파싱 대기열 등록",
};

function nextLabel(revisions: Revision[]): string {
  const used = new Set(revisions.map((r) => r.rev_label));
  for (let i = 0; i < 26; i++) {
    const l = String.fromCharCode(65 + i);
    if (!used.has(l)) return l;
  }
  return `R${revisions.length + 1}`;
}

function bumpLabel(label: string): string {
  if (/^[A-Y]$/.test(label)) return String.fromCharCode(label.charCodeAt(0) + 1);
  if (/^\d+$/.test(label)) return String(Number(label) + 1);
  return `${label}+`;
}

// ------------------------------------------------------------------------------------------------
function SettingsCard({ me, onSaved }: { me: Me; onSaved: (ws: Workspace) => void }) {
  const [mode, setMode] = useState<SecurityMode>(me.workspace.security_mode);
  const [days, setDays] = useState(me.workspace.retention_days);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const owner = me.user.role === "OWNER";
  const dirty = mode !== me.workspace.security_mode || days !== me.workspace.retention_days;

  return (
    <Card title="보안 설정 (Security Mode)" testId="settings-card">
      <div className="space-y-2 text-xs text-slate-700">
        <label className="flex items-start gap-2">
          <input type="radio" name="security-mode" data-testid="mode-ttl" className="mt-0.5" checked={mode === "TTL_VAULT"} disabled={!owner} onChange={() => setMode("TTL_VAULT")} />
          <span>
            <b>Mode A · TTL Vault</b> — 원본 STEP을 보관 기간 동안만 격리 보관 후 자동 파기
          </span>
        </label>
        <label className="flex items-start gap-2">
          <input type="radio" name="security-mode" data-testid="mode-zero" className="mt-0.5" checked={mode === "ZERO_RETENTION"} disabled={!owner} onChange={() => setMode("ZERO_RETENTION")} />
          <span>
            <b>Mode B · Zero-Retention</b> — 연산 직후 원본 즉시 파기, 재연산은 로컬 원본 재드롭(SHA-256 검증)
          </span>
        </label>
        <Field label="원본 보관 기간 (30~90일)">
          <input
            data-testid="retention-days"
            type="number"
            min={30}
            max={90}
            className={inputCls}
            value={days}
            disabled={!owner || mode === "ZERO_RETENTION"}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </Field>
        <Button
          data-testid="settings-save"
          className="w-full"
          disabled={!owner || !dirty || days < 30 || days > 90}
          onClick={async () => {
            setError(null);
            setMsg(null);
            try {
              const res = await api<{ workspace: Workspace; purges_enqueued: number }>("/v1/workspace/settings", {
                method: "PATCH",
                json: { security_mode: mode, retention_days: days },
              });
              onSaved(res.workspace);
              setMsg(`저장됨 · ${res.workspace.security_mode}${res.purges_enqueued ? ` · 원본 ${res.purges_enqueued}건 즉시 파기 예약` : ""}`);
            } catch (e) {
              setError(errorText(e));
            }
          }}
        >
          보안 설정 저장
        </Button>
        {!owner && <p className="text-slate-500">워크스페이스 OWNER만 변경할 수 있습니다.</p>}
        {msg && (
          <p data-testid="settings-msg" className="text-emerald-700">
            {msg}
          </p>
        )}
        <ErrorText error={error} />
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------
function ShopInboxCard() {
  const [slug, setSlug] = useState("");
  const [shopName, setShopName] = useState("");
  const [contact, setContact] = useState("");
  const [created, setCreated] = useState<{ slug: string; url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <Card title="공장 전용 무료 도면 접수함" testId="shop-inbox-card" id="shop-inbox">
      <form
        className="space-y-2"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            const res = await api<{ inbox: { slug: string }; public_url: string }>("/v1/shop-inboxes", {
              json: { slug: slug.trim(), shop_name: shopName.trim(), owner_contact: contact.trim() },
            });
            setCreated({ slug: res.inbox.slug, url: res.public_url });
          } catch (err) {
            setError(errorText(err));
          }
        }}
      >
        <p className="text-xs text-slate-600">거래처가 계정 없이 v1·v2 STEP을 올리면 변경점이 자동 분석되어 이 워크스페이스로 들어옵니다.</p>
        <Field label="주소 (영문 소문자·숫자·-)">
          <input data-testid="inbox-slug" className={inputCls} value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} placeholder="oo-precision" />
        </Field>
        <Field label="공장 이름">
          <input data-testid="inbox-name" className={inputCls} value={shopName} onChange={(e) => setShopName(e.target.value)} placeholder="OO정밀" />
        </Field>
        <Field label="알림 받을 연락처">
          <input data-testid="inbox-contact" className={inputCls} value={contact} onChange={(e) => setContact(e.target.value)} placeholder="owner@shop.com" />
        </Field>
        <Button data-testid="inbox-create" type="submit" className="w-full" disabled={slug.length < 3 || !shopName.trim() || contact.trim().length < 3}>
          접수함 만들기
        </Button>
      </form>
      {created && (
        <p className="mt-2 rounded-md bg-emerald-50 p-2 text-xs text-emerald-900">
          접수함 주소:{" "}
          <Link data-testid="inbox-link" href={`/shop/${created.slug}`} className="break-all font-mono underline">
            {created.url}
          </Link>
        </p>
      )}
      <ErrorText error={error} />
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------
function PartsCard({ parts, selectedId, onSelect, onCreated }: {
  parts: Part[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreated: (p: Part) => void;
}) {
  const [partNumber, setPartNumber] = useState("");
  const [name, setName] = useState("");
  const [material, setMaterial] = useState("");
  const [finish, setFinish] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <Card title={`부품 (${parts.length})`} testId="parts-card">
      <form
        className="space-y-2"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            const { part } = await api<{ part: Part }>("/v1/parts", {
              json: { part_number: partNumber.trim(), name: name.trim(), default_material: material.trim() || null, default_finish: finish.trim() || null },
            });
            setPartNumber("");
            setName("");
            setMaterial("");
            setFinish("");
            onCreated(part);
          } catch (err) {
            setError(errorText(err));
          }
        }}
      >
        <div className="grid grid-cols-2 gap-2">
          <Field label="품번">
            <input data-testid="part-number" className={inputCls} value={partNumber} onChange={(e) => setPartNumber(e.target.value)} placeholder="BRK-001" />
          </Field>
          <Field label="품명">
            <input data-testid="part-name" className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="브래킷" />
          </Field>
          <Field label="소재">
            <input data-testid="part-material" className={inputCls} value={material} onChange={(e) => setMaterial(e.target.value)} placeholder="AL6061-T6" />
          </Field>
          <Field label="후처리">
            <input data-testid="part-finish" className={inputCls} value={finish} onChange={(e) => setFinish(e.target.value)} placeholder="흑색 아노다이징" />
          </Field>
        </div>
        <Button data-testid="part-create" type="submit" className="w-full" disabled={!partNumber.trim() || !name.trim()}>
          부품 등록
        </Button>
      </form>
      <ErrorText error={error} />
      <ul className="mt-3 max-h-[420px] space-y-1 overflow-y-auto">
        {parts.map((p) => (
          <li key={p.id}>
            <button
              type="button"
              data-testid="part-item"
              data-part-number={p.part_number}
              onClick={() => onSelect(p.id)}
              className={`w-full rounded-md border px-2 py-1.5 text-left text-sm ${selectedId === p.id ? "border-indigo-400 bg-indigo-50" : "border-slate-200 hover:bg-slate-50"}`}
            >
              <span className="font-semibold">{p.part_number}</span> <span className="text-slate-600">{p.name}</span>
              {p.shop_inbox_id && <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-800">접수함</span>}
            </button>
          </li>
        ))}
        {parts.length === 0 && <li className="text-xs text-slate-500">아직 부품이 없습니다.</li>}
      </ul>
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------
function PartPanel({ partId, mode }: { partId: string; mode: SecurityMode }) {
  const [detail, setDetail] = useState<PartDetail | null>(null);
  const [revLabel, setRevLabel] = useState("");
  const [phase, setPhase] = useState<{ file: string; phase: UploadPhase; progress?: number } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [baseId, setBaseId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [activeDiff, setActiveDiff] = useState<string | null>(null);
  const [activeDone, setActiveDone] = useState<DiffStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setDetail(await api<PartDetail>(`/v1/parts/${partId}`));
    } catch (e) {
      setError(errorText(e));
    }
  }, [partId]);

  useEffect(() => {
    setDetail(null);
    setActiveDiff(null);
    setActiveDone(null);
    setBaseId("");
    setTargetId("");
    void reload();
  }, [reload]);

  // revisions are parsed by the CAD worker (q-ingest): poll until every one is terminal
  useEffect(() => {
    if (!detail) return;
    const busy =
      detail.revisions.some((r) => r.status === "QUEUED" || r.status === "PARSING") ||
      detail.diff_runs.some((d) => d.status === "QUEUED" || d.status === "COMPUTING");
    if (!busy) return;
    const t = window.setTimeout(() => void reload(), 1500);
    return () => window.clearTimeout(t);
  }, [detail, reload]);

  const onDiffDone = useCallback(
    (status: DiffStatus) => {
      setActiveDone(status);
      void reload();
    },
    [reload],
  );

  const labelOf = useMemo(() => new Map((detail?.revisions ?? []).map((r) => [r.id, r.rev_label])), [detail]);
  if (!detail) return <Card testId="part-panel">불러오는 중…</Card>;

  const suggested = nextLabel(detail.revisions);
  const ready = detail.revisions.filter((r) => r.status === "READY").slice().reverse(); // oldest -> newest
  const base = baseId || ready[ready.length - 2]?.id || "";
  const target = targetId || ready[ready.length - 1]?.id || "";

  /**
   * Stage every file first (SHA-256 -> presign -> PUT), then complete them back to back. Two files
   * dropped together (file-name order = Base -> Target) also request the diff right away: under
   * ZERO_RETENTION the worker destroys a raw STEP as soon as no queued diff run still needs it.
   */
  const upload = async (files: File[]) => {
    setUploading(true);
    setError(null);
    const ordered = [...files].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    let label = revLabel.trim() || suggested;
    try {
      const staged: StagedUpload[] = [];
      for (const file of ordered) {
        const s = await stageRevision(partId, file, label, (p, progress) => setPhase({ file: file.name, phase: p, progress }));
        staged.push(s);
        if (!s.redrop) label = bumpLabel(label);
      }
      const names = ordered.map((f) => f.name).join(", ");
      setPhase({ file: names, phase: "verifying" });
      for (const s of staged) await completeRevision(s.revisionId);
      setPhase({ file: names, phase: "done" });
      if (staged.length === 2) {
        const res = await api<{ diff_run_id: string }>(`/v1/parts/${partId}/diffs`, {
          json: { base_rev_id: staged[0].revisionId, target_rev_id: staged[1].revisionId },
        });
        setActiveDone(null);
        setActiveDiff(res.diff_run_id);
      }
      setRevLabel("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setUploading(false);
      await reload();
    }
  };

  return (
    <div className="space-y-3" data-testid="part-panel" data-part-id={partId}>
      <Card
        title={
          <span>
            {detail.part.part_number} <span className="font-normal text-slate-500">{detail.part.name}</span>
          </span>
        }
        actions={
          <span className="text-[11px] text-slate-500">
            {detail.part.default_material ?? "소재 미지정"} · {detail.part.default_finish ?? "후처리 미지정"}
          </span>
        }
      >
        <div className="grid gap-3 lg:grid-cols-[180px_minmax(0,1fr)]">
          <Field label={`리비전 라벨 (기본 ${suggested})`}>
            <input data-testid="rev-label" className={inputCls} value={revLabel} onChange={(e) => setRevLabel(e.target.value)} placeholder={suggested} />
          </Field>
          <Dropzone
            testId="upload-dropzone"
            label="STEP 파일을 끌어다 놓거나 클릭해서 선택 (.step / .stp, 최대 50MB)"
            hint="두 파일을 함께 놓으면 파일명 순서대로 Base → Target 업로드 후 바로 변경점 분석 · 브라우저 SHA-256 → Presigned PUT → 서버 해시 재검증"
            multiple
            disabled={uploading}
            onFiles={(files) => void upload(files)}
          />
        </div>
        {mode === "ZERO_RETENTION" && (
          <p data-testid="zero-retention-note" className="mt-2 rounded-md bg-sky-50 px-2 py-1.5 text-xs text-sky-900">
            Zero-Retention: 원본은 처리 직후 파기됩니다. 비교할 두 리비전은 한 번에 놓으세요. 이전 리비전과 다시 비교할 때는 아래에서 원본을 재드롭(SHA-256 검증)합니다.
          </p>
        )}
        {phase && (
          <p data-testid="upload-phase" data-phase={phase.phase} className="mt-2 text-xs text-slate-600">
            {phase.file} · {PHASE_LABEL[phase.phase]}
            {phase.phase === "uploading" && phase.progress !== undefined ? ` ${Math.round(phase.progress * 100)}%` : ""}
          </p>
        )}
        <ErrorText error={error} />

        <table data-testid="revision-table" className="mt-3 w-full text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="py-1">Rev</th>
              <th>상태</th>
              <th>파일</th>
              <th>크기</th>
              <th>SHA-256</th>
              <th>원본</th>
              <th>업로드</th>
            </tr>
          </thead>
          <tbody>
            {detail.revisions.map((r) => (
              <tr
                key={r.id}
                data-testid="revision-row"
                data-rev-label={r.rev_label}
                data-status={r.status}
                data-raw={r.raw_available ? "kept" : "purged"}
                className="border-t border-slate-100"
              >
                <td className="py-1 font-semibold">{r.rev_label}</td>
                <td>
                  <StatusBadge status={r.status} />
                  {r.error_code && <span className="ml-1 text-red-600">{r.error_code}</span>}
                </td>
                <td className="max-w-[160px] truncate">{String(r.spec_card_json.filename ?? "-")}</td>
                <td>{bytes(r.file_size_bytes)}</td>
                <td className="font-mono text-[10px]">{r.file_sha256.slice(0, 12)}…</td>
                <td>{r.raw_available ? "보관 중" : r.raw_purged_at ? "파기됨" : "-"}</td>
                <td className="text-slate-500">{when(r.created_at)}</td>
              </tr>
            ))}
            {detail.revisions.length === 0 && (
              <tr>
                <td colSpan={7} className="py-2 text-slate-500">
                  첫 리비전(Rev A)과 수정본(Rev B)을 올리세요.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="mt-3">
          <RedropPanel partId={partId} revisions={detail.revisions} onDone={() => void reload()} />
        </div>
      </Card>

      <Card title="변경점 분석 (B-Rep Diff)" testId="diff-card">
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Base (Before)">
            <select data-testid="diff-base" className={inputCls} value={base} onChange={(e) => setBaseId(e.target.value)}>
              <option value="">선택</option>
              {ready.map((r) => (
                <option key={r.id} value={r.id}>
                  Rev {r.rev_label}
                </option>
              ))}
            </select>
          </Field>
          <span className="pb-2 text-slate-400">→</span>
          <Field label="Target (After)">
            <select data-testid="diff-target" className={inputCls} value={target} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">선택</option>
              {ready.map((r) => (
                <option key={r.id} value={r.id}>
                  Rev {r.rev_label}
                </option>
              ))}
            </select>
          </Field>
          <Button
            data-testid="diff-create"
            disabled={!base || !target || base === target}
            onClick={async () => {
              setError(null);
              setActiveDone(null);
              try {
                const res = await api<{ diff_run_id: string }>(`/v1/parts/${partId}/diffs`, { json: { base_rev_id: base, target_rev_id: target } });
                setActiveDiff(res.diff_run_id);
                void reload();
              } catch (e) {
                setError(errorText(e));
              }
            }}
          >
            변경점 분석 시작
          </Button>
        </div>
        {ready.length < 2 && <p className="mt-2 text-xs text-slate-500">파싱이 끝난(READY) 리비전이 두 개 이상 있어야 합니다.</p>}
        {activeDiff && (
          <div className="mt-3 space-y-2">
            <DiffProgress diffId={activeDiff} onDone={onDiffDone} />
            {activeDone && (
              <Link
                data-testid="open-review"
                data-status={activeDone}
                href={`/parts/${partId}/diffs/${activeDiff}`}
                className="inline-block rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white"
              >
                {activeDone === "NEEDS_ALIGN" ? "3-2-1 수동 정합 열기 →" : activeDone === "FAILED" ? "실패 내역 보기 →" : "PR 스타일 리뷰 열기 →"}
              </Link>
            )}
          </div>
        )}
        <ul className="mt-3 space-y-1">
          {detail.diff_runs.map((d) => (
            <li key={d.id} data-testid="diff-run-row" data-status={d.status} data-diff-id={d.id} className="flex items-center justify-between rounded-md border border-slate-200 px-2 py-1 text-xs">
              <span>
                <span className="font-mono text-slate-500">{shortId(d.id)}</span> · Rev {labelOf.get(d.base_rev_id) ?? "?"} → Rev {labelOf.get(d.target_rev_id) ?? "?"} · {d.align_method}
                {d.align_confidence !== null ? ` (${d.align_confidence.toFixed(3)})` : ""}
              </span>
              <span className="flex items-center gap-2">
                <StatusBadge status={d.status} />
                <Link href={`/parts/${partId}/diffs/${d.id}`} className="text-indigo-600 underline">
                  열기
                </Link>
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------
function WorkspaceInner() {
  const { me, setMe } = useMe();
  const router = useRouter();
  const params = useSearchParams();
  const selected = params.get("part");
  const [parts, setParts] = useState<Part[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reloadParts = useCallback(async () => {
    try {
      setParts((await api<{ items: Part[] }>("/v1/parts")).items);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  const signedIn = me !== null;
  useEffect(() => {
    if (signedIn) void reloadParts();
  }, [signedIn, reloadParts]);

  const select = (id: string) => router.replace(`/workspace?part=${id}`);

  return (
    <>
      <AppHeader me={me}>{me ? me.workspace.name : null}</AppHeader>
      {!me ? (
        <p className="p-6 text-sm text-slate-500">로그인 확인 중…</p>
      ) : (
        <main className="mx-auto grid max-w-[1600px] gap-3 p-4 lg:grid-cols-[340px_minmax(0,1fr)]">
          <div className="space-y-3">
            <PartsCard
              parts={parts}
              selectedId={selected}
              onSelect={select}
              onCreated={(p) => {
                void reloadParts();
                select(p.id);
              }}
            />
            <SettingsCard me={me} onSaved={(ws) => setMe({ ...me, workspace: ws })} />
            <ShopInboxCard />
            <ErrorText error={error} />
          </div>
          <div>
            {selected ? (
              <PartPanel key={selected} partId={selected} mode={me.workspace.security_mode} />
            ) : (
              <Card>
                <p className="text-sm text-slate-600">왼쪽에서 부품을 등록하거나 선택하세요. Rev A·B STEP을 끌어다 놓으면 변경점이 자동으로 분석됩니다.</p>
              </Card>
            )}
          </div>
        </main>
      )}
    </>
  );
}

export default function WorkspacePage() {
  return (
    <Suspense>
      <WorkspaceInner />
    </Suspense>
  );
}
