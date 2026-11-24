/** Phase 4 §5.4 — factory-floor tablet interaction rules (pure, unit-tested). */

/** A tap is a short, still press: duration < 200 ms and movement < 8 px (rotation drags never pick). */
export const TAP_MAX_MS = 200;
export const TAP_MAX_MOVE_PX = 8;

export interface PointerSample {
  t: number;
  x: number;
  y: number;
}

export function isTap(down: PointerSample | null, up: PointerSample): boolean {
  if (!down) return false;
  return up.t - down.t < TAP_MAX_MS && Math.hypot(up.x - down.x, up.y - down.y) < TAP_MAX_MOVE_PX;
}

/** Signed GET URLs live 15 min; refresh them at 12 min so a long review never hits an expired asset. */
export const SIGNED_URL_TTL_MS = 15 * 60 * 1000;
export const SIGNED_URL_REFRESH_LEAD_MS = 3 * 60 * 1000;

export function refreshDelayMs(issuedAtMs: number, nowMs: number): number {
  return Math.max(0, issuedAtMs + SIGNED_URL_TTL_MS - SIGNED_URL_REFRESH_LEAD_MS - nowMs);
}

/** SSE reconnect: exponential backoff 1 s, 2 s, 4 s ... then the 4 s polling fallback takes over. */
export const SSE_MAX_RETRIES = 3;
export const POLL_INTERVAL_MS = 4000;

export function sseBackoffMs(attempt: number): number {
  return 1000 * 2 ** Math.max(0, attempt - 1);
}

/** Coarse pointer (touch tablet) or a low-spec device -> start on lod1.glb with a "고화질 전환" toggle. */
export function prefersLowQuality(win: Pick<Window, "matchMedia"> & { navigator: Navigator }): boolean {
  const coarse = typeof win.matchMedia === "function" && win.matchMedia("(pointer: coarse)").matches;
  const nav = win.navigator as Navigator & { deviceMemory?: number };
  const lowCores = typeof nav.hardwareConcurrency === "number" && nav.hardwareConcurrency > 0 && nav.hardwareConcurrency <= 2;
  const lowMemory = typeof nav.deviceMemory === "number" && nav.deviceMemory <= 2;
  return coarse || lowCores || lowMemory;
}
