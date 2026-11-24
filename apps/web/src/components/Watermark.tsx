"use client";

/**
 * Dynamic watermark (IMPLEMENTATION_BLUEPRINT §6.3): full-screen SVG tile, -28deg, opacity 0.07,
 * `{업체명} · {마스킹연락처} · {YYYY-MM-DD HH:mm} · {diff_short_id}`. A MutationObserver (+ a periodic
 * computed-style check) reports removal / hiding so the page can blur the 3D canvas immediately.
 */

import { useLayoutEffect, useRef } from "react";

export const WATERMARK_ANGLE = -28;
export const WATERMARK_OPACITY = 0.07;

export function Watermark({ text, onTamper }: { text: string; onTamper: () => void }) {
  const root = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    let alive = true;
    const tampered = () => {
      const el = root.current;
      if (!el || !el.isConnected) return true;
      const svg = el.querySelector("svg");
      if (!svg) return true;
      const cs = getComputedStyle(el);
      const ss = getComputedStyle(svg);
      return cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) < 0.5 || ss.display === "none" || Number(ss.opacity) < 0.05;
    };
    const check = () => {
      if (alive && tampered()) onTamper();
    };
    const mo = new MutationObserver(check);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["style", "class", "hidden"] });
    const timer = window.setInterval(check, 1000);
    return () => {
      alive = false; // runs during React's own commit, before the observer fires for a legit unmount
      mo.disconnect();
      window.clearInterval(timer);
    };
  }, [onTamper]);

  return (
    <div
      ref={root}
      data-testid="watermark"
      data-text={text}
      aria-hidden
      style={{ position: "fixed", inset: 0, pointerEvents: "none", zIndex: 40 }}
    >
      <svg width="100%" height="100%" style={{ opacity: WATERMARK_OPACITY }}>
        <defs>
          <pattern id="rg-wm" width="460" height="200" patternUnits="userSpaceOnUse" patternTransform={`rotate(${WATERMARK_ANGLE})`}>
            <text x="0" y="100" fontSize="18" fontWeight="700" fill="#0f172a">
              {text}
            </text>
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#rg-wm)" />
      </svg>
    </div>
  );
}
