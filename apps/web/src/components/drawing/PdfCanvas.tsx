"use client";

/** 2D PDF drawing rendered with pdfjs-dist onto a canvas (no <iframe>, no download UI — Phase 4 §4). */

import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { useEffect, useRef, useState } from "react";

type State = { status: "loading" | "rendered" | "error"; pages: number; page: number; error?: string };

export default function PdfCanvas({ url }: { url: string }) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [state, setState] = useState<State>({ status: "loading", pages: 0, page: 1 });

  useEffect(() => {
    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;
    setDoc(null);
    setState({ status: "loading", pages: 0, page: 1 });
    (async () => {
      const pdfjs = await import("pdfjs-dist");
      pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
      loaded = await pdfjs.getDocument({ url }).promise;
      if (cancelled) return void loaded.destroy();
      setDoc(loaded);
      setState({ status: "loading", pages: loaded.numPages, page: 1 });
    })().catch((e: unknown) => {
      if (!cancelled) setState((s) => ({ ...s, status: "error", error: e instanceof Error ? e.message : String(e) }));
    });
    return () => {
      cancelled = true;
      void loaded?.destroy();
    };
  }, [url]);

  useEffect(() => {
    if (!doc || !canvas.current || !host.current) return;
    let task: RenderTask | null = null;
    let cancelled = false;
    (async () => {
      const page = await doc.getPage(state.page);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(host.current?.clientWidth ?? 0, 320);
      const scale = width / page.getViewport({ scale: 1 }).width;
      const viewport = page.getViewport({ scale: scale * dpr });
      const el = canvas.current!;
      el.width = Math.floor(viewport.width);
      el.height = Math.floor(viewport.height);
      el.style.width = `${Math.floor(viewport.width / dpr)}px`;
      el.style.height = `${Math.floor(viewport.height / dpr)}px`;
      task = page.render({ canvas: el, viewport });
      await task.promise;
      if (!cancelled) setState((s) => ({ ...s, status: "rendered" }));
    })().catch((e: unknown) => {
      if (!cancelled && (e as { name?: string })?.name !== "RenderingCancelledException") {
        setState((s) => ({ ...s, status: "error", error: e instanceof Error ? e.message : String(e) }));
      }
    });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, state.page]);

  return (
    <div ref={host} className="relative h-full w-full overflow-auto bg-slate-200" data-testid="pdf-view" data-status={state.status}
      data-pages={state.pages} data-page={state.page}>
      {state.pages > 1 && (
        <div className="sticky top-0 z-10 flex items-center justify-center gap-2 bg-white/90 py-1 text-xs">
          <button type="button" className="min-h-[44px] min-w-[44px] rounded border border-slate-300 px-2"
            disabled={state.page <= 1} onClick={() => setState((s) => ({ ...s, page: s.page - 1 }))}>◀</button>
          <span>{state.page} / {state.pages}</span>
          <button type="button" className="min-h-[44px] min-w-[44px] rounded border border-slate-300 px-2"
            disabled={state.page >= state.pages} onClick={() => setState((s) => ({ ...s, page: s.page + 1 }))}>▶</button>
        </div>
      )}
      <canvas ref={canvas} data-testid="pdf-canvas" className="mx-auto block bg-white shadow" />
      {state.status === "loading" && <p className="absolute inset-0 grid place-items-center text-xs text-slate-500">PDF 도면 렌더링 중…</p>}
      {state.status === "error" && <p className="p-3 text-xs text-red-600">PDF를 열 수 없습니다: {state.error}</p>}
    </div>
  );
}
