"use client";

/**
 * Review workbench shared by the designer page and the external /r/[token] gate.
 *  >= 1024 px: [checklist | viewer (+ 2D split) | measure / pins / side panels]
 *  <  1024 px: viewer with [3D 뷰 | 2D 도면] tabs, checklist below, side panels in a bottom sheet (factory tablets)
 * Phase 4: 3D+2D Split View, spec-only / STEP warning banners, floating [회전 | 측정 | 핀 추가] segment,
 * lod1 by default on coarse-pointer / low-spec devices with a "고화질 전환" toggle.
 */

import type { AnalyticalMeta, Change, DiffManifest, SpecDiffItem } from "@revgate/contracts";
import dynamic from "next/dynamic";
import { type ReactNode, useCallback, useMemo, useRef, useState } from "react";

import { useLowQualityDefault, useMediaQuery } from "@/lib/hooks";
import { buildLut } from "@/lib/lut";
import { describeFace, measureFaces } from "@/lib/measure";
import { specOnlyChange } from "@/lib/spec";
import type { DiffItem, DiffRun, Pin, SideAttachments, SignedUrls } from "@/lib/types";
import type { V3 } from "@/lib/vec";

import { BottomSheet } from "./BottomSheet";
import { ChangeList } from "./ChangeList";
import { DrawingPanel } from "./drawing/DrawingPanel";
import { MeasurePanel, type MeasureSlot } from "./MeasurePanel";
import { PinPanel, type PinDraft } from "./PinPanel";
import { Card, Toggle } from "./ui";
import type { FocusRequest, PickEvent, ViewMode } from "./viewer/DiffViewer";
import { ModeSegment, StepInfoDrawer, type Tool, ViewerBanners } from "./viewer/ViewerChrome";

const DiffViewer = dynamic(() => import("./viewer/DiffViewer"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center text-sm text-slate-500">3D 뷰어 로딩 중…</div>,
});

export type { Tool };
type Layout = "3D" | "SPLIT" | "2D";

export interface NewPin {
  face_id: number;
  rev_side: "BASE" | "TARGET";
  pin_xyz: V3;
  body: string;
  diff_item_id?: string | null;
}

export interface WorkbenchProps {
  run: DiffRun;
  items: DiffItem[];
  manifest: DiffManifest | null;
  baseMeta: AnalyticalMeta | null;
  targetMeta: AnalyticalMeta | null;
  urls: SignedUrls;
  pins: Pin[];
  canWrite: boolean;
  onToggleCheck: (item: DiffItem, checked: boolean) => Promise<void>;
  onCreatePin: (pin: NewPin) => Promise<void>;
  onResolvePin: (pin: Pin, resolved: boolean) => Promise<void>;
  /** replaces the change list (e.g. 3-2-1 datum align while NEEDS_ALIGN) */
  left?: ReactNode;
  right?: ReactNode;
  previewTransform?: number[] | null;
  datumFaces?: { base: number[]; target: number[] };
  pickInterceptor?: React.RefObject<((p: PickEvent) => boolean) | null>;
  blurred?: boolean;
  // Phase 4
  attachments?: SideAttachments;
  specDiffs?: SpecDiffItem[];
  watermarkText?: string | null;
  allowDrawingDownload?: boolean;
}

const NO_ATTACHMENTS: SideAttachments = { base: [], target: [] };

export function ReviewWorkbench(props: WorkbenchProps) {
  const [mode, setMode] = useState<ViewMode>("OVERLAY");
  const [ghost, setGhost] = useState(false);
  const [showAdded, setShowAdded] = useState(true);
  const [showRemoved, setShowRemoved] = useState(true);
  const [tool, setTool] = useState<Tool>("SELECT");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [slots, setSlots] = useState<[MeasureSlot | null, MeasureSlot | null]>([null, null]);
  const [pinDraft, setPinDraft] = useState<PinDraft | null>(null);
  const [lastPick, setLastPick] = useState<PickEvent | null>(null);
  const [layout, setLayout] = useState<Layout>("3D");
  const [hq, setHq] = useState<boolean | null>(null); // null = follow the device default
  const focusNonce = useRef(0);

  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const lowDefault = useLowQualityDefault();
  const lod: "lod0" | "lod1" = (hq ?? lowDefault === false) ? "lod0" : "lod1";

  const { manifest, baseMeta, targetMeta } = props;
  const attachments = props.attachments ?? NO_ATTACHMENTS;
  const specDiffs = props.specDiffs ?? [];
  const changes: Change[] = useMemo(() => manifest?.changes ?? [], [manifest]);
  const selected = changes.find((c) => c.change_id === selectedKey) ?? null;
  const transform = props.previewTransform ?? props.run.transform_4x4;
  const effectiveLayout: Layout = !isDesktop && layout === "SPLIT" ? "3D" : layout;
  const show3d = effectiveLayout !== "2D";
  const show2d = effectiveLayout !== "3D";

  const measureIds = (side: "BASE" | "TARGET") => slots.filter((s): s is MeasureSlot => s?.side === side).map((s) => s.faceId);
  const baseLut = useMemo(
    () => buildLut(manifest ? { modified: manifest.face_status_map.base.modified, removed: manifest.face_status_map.base.removed } : null,
      { selected: selected?.base_face_ids, measure: measureIds("BASE"), datum: props.datumFaces?.base }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manifest, selected, slots, props.datumFaces],
  );
  const targetLut = useMemo(
    () => buildLut(manifest ? { modified: manifest.face_status_map.target.modified, added: manifest.face_status_map.target.added } : null,
      { selected: selected?.target_face_ids, measure: measureIds("TARGET"), datum: props.datumFaces?.target }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manifest, selected, slots, props.datumFaces],
  );

  const metaOf = useCallback((side: "BASE" | "TARGET") => (side === "BASE" ? baseMeta : targetMeta), [baseMeta, targetMeta]);
  const measurement = useMemo(() => {
    const [a, b] = slots;
    if (!a || !b) return null;
    const fa = metaOf(a.side)?.faces[a.faceId];
    const fb = metaOf(b.side)?.faces[b.faceId];
    if (!fa || !fb) return null;
    return measureFaces(
      { face: fa, point: a.point, transform: a.side === "TARGET" ? transform : null },
      { face: fb, point: b.point, transform: b.side === "TARGET" ? transform : null },
    );
  }, [slots, metaOf, transform]);

  const selectChange = (key: string) => {
    setSelectedKey(key);
    const c = changes.find((x) => x.change_id === key);
    if (c) {
      focusNonce.current += 1;
      setFocus({ target: c.camera_focus.target, dir: c.camera_focus.suggested_dir, radius: c.camera_focus.radius_mm, nonce: focusNonce.current });
    }
  };

  const onPick = (p: PickEvent) => {
    setLastPick(p);
    if (props.pickInterceptor?.current?.(p)) return;
    if (tool === "MEASURE") {
      const slot: MeasureSlot = { side: p.side, faceId: p.faceId, point: p.point };
      setSlots(([a, b]) => (a && !b ? [a, slot] : [slot, null]));
    } else if (tool === "PIN") {
      if (props.canWrite) setPinDraft({ side: p.side, faceId: p.faceId, point: p.point });
    } else {
      // 회전 mode: a tap only identifies the face (never measures / pins by accident)
      const hit = changes.find((c) => (p.side === "BASE" ? c.base_face_ids : c.target_face_ids).includes(p.faceId));
      if (hit) setSelectedKey(hit.change_id);
    }
  };

  const pickedFace = lastPick ? metaOf(lastPick.side)?.faces[lastPick.faceId] : null;
  const selectedItem = props.items.find((i) => i.change_key === selectedKey) ?? null;
  const drawingCount = attachments.base.length + attachments.target.length;

  // Signed URLs are refreshed every 12 min (and after an approval reload): keep the GLB URLs of each LOD
  // that is already loaded for this run, so a refresh never re-downloads the 3D model.
  const glbCache = useRef<{ key: string; byLod: Partial<Record<"lod0" | "lod1", { base: string | null; target: string | null }>>;
    delta: { added: string | null; removed: string | null } | null } | null>(null);
  const cacheKey = `${props.run.id}:${props.run.updated_at}`;
  if (glbCache.current?.key !== cacheKey) glbCache.current = { key: cacheKey, byLod: {}, delta: null };
  const pickLod = (v: SignedUrls["base"]) => (v ? (lod === "lod1" ? v.lod1 : v.lod0) : null);
  const prev = glbCache.current.byLod[lod];
  const glb = { base: prev?.base ?? pickLod(props.urls.base), target: prev?.target ?? pickLod(props.urls.target) };
  glbCache.current.byLod[lod] = glb;
  const delta = glbCache.current.delta?.added || glbCache.current.delta?.removed
    ? glbCache.current.delta
    : { added: props.urls.delta_added_glb ?? null, removed: props.urls.delta_removed_glb ?? null };
  glbCache.current.delta = delta;

  const leftCard = (
    <Card title={props.left ? "3-2-1 Datum Align" : "변경점 체크리스트"} className="max-h-[760px] overflow-hidden">
      {props.left ?? (
        <ChangeList
          items={props.items}
          changes={changes}
          selectedKey={selectedKey}
          onSelect={selectChange}
          onToggle={(it, checked) => void props.onToggleCheck(it, checked)}
          canCheck={props.canWrite}
        />
      )}
    </Card>
  );

  const rightPanels = (
    <>
      <Card title="측정 (analytical_meta)">
        <MeasurePanel slots={slots} metas={{ BASE: baseMeta, TARGET: targetMeta }} measurement={measurement} onChange={setSlots} />
      </Card>
      <Card title={`3D 핀 코멘트 (${props.pins.length})`}>
        <PinPanel
          pins={props.pins}
          draft={pinDraft}
          canWrite={props.canWrite}
          onCancel={() => setPinDraft(null)}
          onResolve={props.onResolvePin}
          onSave={async (body) => {
            if (!pinDraft) return;
            await props.onCreatePin({ face_id: pinDraft.faceId, rev_side: pinDraft.side, pin_xyz: pinDraft.point, body, diff_item_id: selectedItem?.id ?? null });
            setPinDraft(null);
          }}
        />
      </Card>
      {props.right}
    </>
  );

  return (
    <div className="grid grid-cols-1 gap-3 pb-24 lg:grid-cols-[300px_minmax(0,1fr)_330px] lg:pb-0">
      {isDesktop && leftCard}

      <div className="min-w-0 space-y-2">
        <ViewerBanners
          specOnly={props.run.status === "READY" && specOnlyChange(props.items.length, specDiffs)}
          specDiffCount={specDiffs.length}
          metas={[baseMeta, targetMeta]}
        />
        <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-slate-200 bg-white p-2">
          <Toggle data-testid="view-base" active={mode === "BASE"} onClick={() => setMode("BASE")}>Before (Base)</Toggle>
          <Toggle data-testid="view-target" active={mode === "TARGET"} onClick={() => setMode("TARGET")}>After (Target)</Toggle>
          <Toggle data-testid="view-overlay" active={mode === "OVERLAY"} onClick={() => setMode("OVERLAY")}>Overlay</Toggle>
          <span className="mx-1 h-5 w-px bg-slate-200" />
          <Toggle data-testid="toggle-ghost" active={ghost} onClick={() => setGhost((g) => !g)}>고스트</Toggle>
          <Toggle data-testid="toggle-added" active={showAdded} onClick={() => setShowAdded((v) => !v)}>+ 추가 살</Toggle>
          <Toggle data-testid="toggle-removed" active={showRemoved} onClick={() => setShowRemoved((v) => !v)}>− 깎인 살</Toggle>
          <span className="mx-1 h-5 w-px bg-slate-200" />
          {isDesktop ? (
            <span className="flex gap-1" data-testid="layout-segment">
              <Toggle data-testid="layout-3d" active={layout === "3D"} onClick={() => setLayout("3D")}>3D 단독</Toggle>
              <Toggle data-testid="layout-split" active={layout === "SPLIT"} onClick={() => setLayout("SPLIT")}>3D+2D 분할</Toggle>
              <Toggle data-testid="layout-2d" active={layout === "2D"} onClick={() => setLayout("2D")}>2D 단독</Toggle>
            </span>
          ) : (
            <span className="flex gap-1" data-testid="layout-tabs" role="tablist">
              <Toggle data-testid="tab-3d" role="tab" active={effectiveLayout !== "2D"} onClick={() => setLayout("3D")}>3D 뷰</Toggle>
              <Toggle data-testid="tab-2d" role="tab" active={effectiveLayout === "2D"} onClick={() => setLayout("2D")}>2D 도면 ({drawingCount})</Toggle>
            </span>
          )}
          <Toggle data-testid="toggle-hq" data-lod={lod} active={lod === "lod0"} onClick={() => setHq(lod !== "lod0")}>
            {lod === "lod0" ? "고화질" : "고화질 전환"}
          </Toggle>
        </div>

        <div className={show3d && show2d ? "grid grid-cols-2 gap-2" : ""}>
          <div
            data-testid="viewer-shell"
            data-blurred={props.blurred ? "true" : "false"}
            data-lod={lod}
            className={`relative h-[560px] overflow-hidden rounded-xl border border-slate-200 bg-slate-100 ${show3d ? "" : "hidden"}`}
            style={props.blurred ? { filter: "blur(24px)" } : undefined}
          >
            {lowDefault === null ? (
              <div className="grid h-full place-items-center text-sm text-slate-500">3D 뷰어 준비 중…</div>
            ) : (
              <DiffViewer
                baseUrl={glb.base}
                targetUrl={glb.target}
                deltaAddedUrl={delta.added}
                deltaRemovedUrl={delta.removed}
                transform={transform}
                baseLut={baseLut}
                targetLut={targetLut}
                mode={mode}
                ghost={ghost}
                showAdded={showAdded}
                showRemoved={showRemoved}
                focus={focus}
                pins={props.pins.map((p) => ({ id: p.id, xyz: p.pin_xyz as V3, label: p.author_label, resolved: p.resolved }))}
                onPick={onPick}
                lod={lod}
              />
            )}
            <ModeSegment tool={tool} canPin={props.canWrite} onChange={setTool} />
          </div>
          {show2d && (
            <div className="h-[560px] min-w-0">
              <DrawingPanel attachments={attachments} watermarkText={props.watermarkText} allowDownload={Boolean(props.allowDrawingDownload)} blurred={props.blurred} />
            </div>
          )}
        </div>
        <p data-testid="pick-info" className="min-h-5 text-xs text-slate-600">
          {lastPick && pickedFace ? `선택: ${lastPick.side} · ${describeFace(pickedFace)}` : "모델을 탭하면 면 정보가 표시됩니다 (O(1) _FACE_ID 피킹 · 회전 모드에서는 측정·핀이 생기지 않습니다)"}
        </p>
        <StepInfoDrawer />
        {!isDesktop && leftCard}
      </div>

      {isDesktop ? <div className="space-y-3">{rightPanels}</div> : <BottomSheet title="측정 · 핀 · 승인">{rightPanels}</BottomSheet>}
    </div>
  );
}
