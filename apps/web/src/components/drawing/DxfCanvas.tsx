"use client";

/** 2D DXF drawing: dxf-parser (ASCII DXF) -> Canvas2D vector preview, no download UI (Phase 4 §4). */

import { useEffect, useRef, useState } from "react";

import { type Drawing, drawToCanvas, toDrawing } from "@/lib/dxf";

type State = { status: "loading" | "rendered" | "error"; drawn: number; skipped: number; error?: string };

export default function DxfCanvas({ url }: { url: string }) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [drawing, setDrawing] = useState<Drawing | null>(null);
  const [state, setState] = useState<State>({ status: "loading", drawn: 0, skipped: 0 });

  useEffect(() => {
    let cancelled = false;
    setDrawing(null);
    setState({ status: "loading", drawn: 0, skipped: 0 });
    (async () => {
      const [{ default: DxfParser }, res] = await Promise.all([import("dxf-parser"), fetch(url, { cache: "no-store" })]);
      if (!res.ok) throw new Error(`DXF download failed (${res.status})`);
      const parsed = new DxfParser().parseSync(await res.text());
      if (!cancelled) setDrawing(toDrawing(parsed));
    })().catch((e: unknown) => {
      if (!cancelled) setState((s) => ({ ...s, status: "error", error: e instanceof Error ? e.message : String(e) }));
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

  useEffect(() => {
    if (!drawing || !canvas.current || !host.current) return;
    const draw = () => {
      const el = canvas.current!;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(host.current?.clientWidth ?? 0, 320);
      const h = Math.max(host.current?.clientHeight ?? 0, 320);
      el.width = Math.floor(w * dpr);
      el.height = Math.floor(h * dpr);
      el.style.width = `${w}px`;
      el.style.height = `${h}px`;
      const ctx = el.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawToCanvas(ctx, drawing, w, h);
      setState({ status: "rendered", drawn: drawing.drawn, skipped: drawing.skipped });
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(host.current);
    return () => ro.disconnect();
  }, [drawing]);

  return (
    <div ref={host} className="relative h-full w-full overflow-hidden bg-white" data-testid="dxf-view" data-status={state.status}
      data-entities={state.drawn} data-skipped={state.skipped}>
      <canvas ref={canvas} data-testid="dxf-canvas" className="block" />
      {state.status === "loading" && <p className="absolute inset-0 grid place-items-center text-xs text-slate-500">DXF 도면 해석 중…</p>}
      {state.status === "error" && <p className="absolute inset-x-0 top-0 p-3 text-xs text-red-600">DXF를 열 수 없습니다: {state.error}</p>}
      {state.status === "rendered" && state.skipped > 0 && (
        <p className="absolute bottom-1 left-1 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-800">미지원 엔티티 {state.skipped}개 생략 (블록·치수)</p>
      )}
    </div>
  );
}
