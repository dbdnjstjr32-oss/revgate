"use client";

import { useEffect, useRef, useState } from "react";

import { prefersLowQuality, refreshDelayMs } from "./interaction";

/** SSR-safe matchMedia hook (false until mounted). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(query);
    setMatches(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** Phase 4 §5.4: true on touch tablets / low-spec devices (decided once on mount). */
export function useLowQualityDefault(): boolean | null {
  const [low, setLow] = useState<boolean | null>(null);
  useEffect(() => setLow(prefersLowQuality(window)), []);
  return low;
}

/** Re-fetch 15 min signed URLs at the 12 min mark (`issuedAt` = when the current batch was fetched). */
export function useSignedUrlRefresh(issuedAt: number | null, refresh: () => Promise<void>): void {
  const latest = useRef(refresh);
  useEffect(() => {
    latest.current = refresh;
  });
  useEffect(() => {
    if (issuedAt === null) return;
    const t = window.setTimeout(() => void latest.current(), refreshDelayMs(issuedAt, Date.now()));
    return () => window.clearTimeout(t);
  }, [issuedAt]);
}
