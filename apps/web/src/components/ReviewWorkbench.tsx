"use client";

/** Three-column review workbench shared by the designer page and the external /r/[token] gate. */

import type { AnalyticalMeta, Change, DiffManifest } from "@revgate/contracts";
import dynamic from "next/dynamic";
import { type ReactNode, useCallback, useMemo, useRef, useState } from "react";

import { buildLut } from "@/lib/lut";
import { describeFace, measureFaces } from "@/lib/measure";
import type { DiffItem, DiffRun, Pin, SignedUrls } from "@/lib/types";
import type { V3 } from "@/lib/vec";

import { ChangeList } from "./ChangeList";
import { MeasurePanel, type MeasureSlot } from "./MeasurePanel";
import { PinPanel, type PinDraft } from "./PinPanel";
import { Card, Toggle } from "./ui";
import type { FocusRequest, PickEvent, ViewMode } from "./viewer/DiffViewer";

const DiffViewer = dynamic(() => import("./viewer/DiffViewer"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center text-sm text-slate-500">3D 뷰어 로딩 중…</div>,
});

export type Tool = "SELECT" | "MEASURE" | "PIN";

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
}

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
  const focusNonce = useRef(0);

  const { manifest, baseMeta, targetMeta } = props;
  const changes: Change[] = useMemo(() => manifest?.changes ?? [], [manifest]);
  const selected = changes.find((c) => c.change_id === selectedKey) ?? null;
  const transform = props.previewTransform ?? props.run.transform_4x4;

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
      const hit = changes.find((c) => (p.side === "BASE" ? c.base_face_ids : c.target_face_ids).includes(p.faceId));
      if (hit) setSelectedKey(hit.change_id);
    }
  };

  const pickedFace = lastPick ? metaOf(lastPick.side)?.faces[lastPick.faceId] : null;
  const selectedItem = props.items.find((i) => i.change_key === selectedKey) ?? null;

  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[320px_minmax(0,1fr)_340px]">
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

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-slate-200 bg-white p-2">
          <Toggle data-testid="view-base" active={mode === "BASE"} onClick={() => setMode("BASE")}>Before (Base)</Toggle>
          <Toggle data-testid="view-target" active={mode === "TARGET"} onClick={() => setMode("TARGET")}>After (Target)</Toggle>
          <Toggle data-testid="view-overlay" active={mode === "OVERLAY"} onClick={() => setMode("OVERLAY")}>Overlay</Toggle>
          <span className="mx-1 h-5 w-px bg-slate-200" />
          <Toggle data-testid="toggle-ghost" active={ghost} onClick={() => setGhost((g) => !g)}>고스트</Toggle>
          <Toggle data-testid="toggle-added" active={showAdded} onClick={() => setShowAdded((v) => !v)}>+ 추가 살</Toggle>
          <Toggle data-testid="toggle-removed" active={showRemoved} onClick={() => setShowRemoved((v) => !v)}>− 깎인 살</Toggle>
          <span className="mx-1 h-5 w-px bg-slate-200" />
          <Toggle data-testid="tool-select" active={tool === "SELECT"} onClick={() => setTool("SELECT")}>선택</Toggle>
          <Toggle data-testid="tool-measure" active={tool === "MEASURE"} onClick={() => setTool("MEASURE")}>측정</Toggle>
          <Toggle data-testid="tool-pin" active={tool === "PIN"} disabled={!props.canWrite} onClick={() => setTool("PIN")}>핀</Toggle>
        </div>
        <div
          data-testid="viewer-shell"
          data-blurred={props.blurred ? "true" : "false"}
          className="relative h-[560px] overflow-hidden rounded-xl border border-slate-200 bg-slate-100"
          style={props.blurred ? { filter: "blur(24px)" } : undefined}
        >
          <DiffViewer
            baseUrl={props.urls.base?.lod0 ?? null}
            targetUrl={props.urls.target?.lod0 ?? null}
            deltaAddedUrl={props.urls.delta_added_glb ?? null}
            deltaRemovedUrl={props.urls.delta_removed_glb ?? null}
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
          />
        </div>
        <p data-testid="pick-info" className="min-h-5 text-xs text-slate-600">
          {lastPick && pickedFace ? `선택: ${lastPick.side} · ${describeFace(pickedFace)}` : "모델을 클릭하면 면 정보가 표시됩니다 (O(1) _FACE_ID 피킹)"}
        </p>
      </div>

      <div className="space-y-3">
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
      </div>
    </div>
  );
}
