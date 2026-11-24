"use client";

/**
 * 2D drawing pane of the Split View (Phase 4 §4): Base / Target switch, attachment chips, PDF (pdfjs-dist canvas)
 * or DXF (dxf-parser + Canvas2D) preview with the same dynamic SVG watermark tile on top. The original file can
 * only be opened when the link allows raw download (`allowDownload`).
 */

import type { AttachmentItem } from "@revgate/contracts";
import dynamic from "next/dynamic";
import { useState } from "react";

import { bytes } from "@/lib/format";
import type { SideAttachments } from "@/lib/types";

import { WATERMARK_ANGLE, WATERMARK_OPACITY } from "../Watermark";

const PdfCanvas = dynamic(() => import("./PdfCanvas"), { ssr: false });
const DxfCanvas = dynamic(() => import("./DxfCanvas"), { ssr: false });

export function WatermarkTile({ text }: { text: string }) {
  return (
    <div data-testid="drawing-watermark" data-text={text} aria-hidden className="pointer-events-none absolute inset-0 z-20">
      <svg width="100%" height="100%" style={{ opacity: WATERMARK_OPACITY }}>
        <defs>
          <pattern id="rg-wm-2d" width="420" height="180" patternUnits="userSpaceOnUse" patternTransform={`rotate(${WATERMARK_ANGLE})`}>
            <text x="0" y="90" fontSize="16" fontWeight="700" fill="#0f172a">
              {text}
            </text>
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#rg-wm-2d)" />
      </svg>
    </div>
  );
}

export function DrawingPanel({ attachments, watermarkText, allowDownload, blurred = false }: {
  attachments: SideAttachments;
  watermarkText?: string | null;
  allowDownload: boolean;
  blurred?: boolean;
}) {
  const [side, setSide] = useState<"base" | "target">("target");
  const [selected, setSelected] = useState<string | null>(null);
  const list = attachments[side].filter((a) => a.kind === "2D_PDF" || a.kind === "2D_DXF");
  const current: AttachmentItem | null = list.find((a) => a.id === selected) ?? list[0] ?? null;

  return (
    <div data-testid="drawing-panel" data-side={side} className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 p-1.5">
        {(["base", "target"] as const).map((s) => (
          <button
            key={s}
            type="button"
            data-testid={`drawing-side-${s}`}
            aria-pressed={side === s}
            onClick={() => {
              setSide(s);
              setSelected(null);
            }}
            className={`min-h-[44px] min-w-[44px] rounded-md px-2.5 text-xs font-medium ${side === s ? "bg-slate-900 text-white" : "bg-white text-slate-700 ring-1 ring-slate-300"}`}
          >
            {s === "base" ? "Base 도면" : "Target 도면"} ({attachments[s].filter((a) => a.kind !== "SPEC_SHEET").length})
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-slate-200" />
        {list.map((a) => (
          <button
            key={a.id}
            type="button"
            data-testid="drawing-item"
            data-kind={a.kind}
            aria-pressed={current?.id === a.id}
            onClick={() => setSelected(a.id)}
            className={`min-h-[44px] rounded-md px-2 text-[11px] ${current?.id === a.id ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-700"}`}
          >
            {a.kind === "2D_PDF" ? "PDF" : "DXF"} · {a.filename}
          </button>
        ))}
        {current && allowDownload && current.url && (
          <a data-testid="drawing-download" href={current.url} target="_blank" rel="noreferrer"
            className="ml-auto inline-flex min-h-[44px] items-center rounded-md border border-slate-300 px-2 text-[11px] text-slate-700">
            원본 열기
          </a>
        )}
      </div>
      <div className="relative min-h-0 flex-1" style={blurred ? { filter: "blur(24px)" } : undefined}>
        {!current ? (
          <p data-testid="drawing-empty" className="grid h-full place-items-center p-4 text-center text-xs text-slate-500">
            {side === "base" ? "Base" : "Target"} 리비전에 첨부된 2D 도면(PDF/DXF)이 없습니다.
          </p>
        ) : !current.url ? (
          <p className="p-4 text-xs text-slate-500">도면 URL이 아직 준비되지 않았습니다.</p>
        ) : current.kind === "2D_PDF" ? (
          <PdfCanvas key={current.id} url={current.url} />
        ) : (
          <DxfCanvas key={current.id} url={current.url} />
        )}
        {watermarkText && current && <WatermarkTile text={watermarkText} />}
      </div>
      {current && (
        <p className="border-t border-slate-100 px-2 py-1 font-mono text-[10px] text-slate-500" data-testid="drawing-meta">
          {current.filename} · {bytes(current.file_size_bytes)} · sha256 {current.file_sha256.slice(0, 16)}…
        </p>
      )}
    </div>
  );
}
