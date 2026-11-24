"use client";

/** < 1024 px (factory tablets / phones): the side panels live in a bottom sheet over the viewer. */

import { type ReactNode, useState } from "react";

export function BottomSheet({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div data-testid="bottom-sheet" data-open={open ? "true" : "false"}
      className="fixed inset-x-0 bottom-0 z-30 rounded-t-2xl border-t border-slate-300 bg-white shadow-[0_-8px_24px_rgba(15,23,42,0.18)]">
      <button
        type="button"
        data-testid="sheet-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-[48px] w-full items-center justify-center gap-2 text-sm font-semibold text-slate-700"
      >
        <span className="h-1 w-10 rounded-full bg-slate-300" />
        {title} {open ? "▼" : "▲"}
      </button>
      <div className={`${open ? "max-h-[70vh]" : "max-h-0"} overflow-y-auto px-3 transition-[max-height] duration-200`}>
        <div className="space-y-3 pb-4">{children}</div>
      </div>
    </div>
  );
}
