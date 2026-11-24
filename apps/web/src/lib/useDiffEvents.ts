"use client";

/**
 * SSE /v1/diffs/{id}/events (Redis Pub/Sub fan-out from the CAD worker): progress | ready | needs_align | failed.
 * Phase 4 §5.4: a dropped stream reconnects with exponential backoff (1 s, 2 s, 4 s); after SSE_MAX_RETRIES the
 * hook falls back to polling GET /v1/diffs/{id} every 4 s until the run is terminal.
 */

import { useEffect, useState } from "react";

import { API_URL, api } from "./api";
import { POLL_INTERVAL_MS, SSE_MAX_RETRIES, sseBackoffMs } from "./interaction";
import type { DiffStatus } from "./types";

export interface DiffEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface DiffEventsState {
  status: DiffStatus | null;
  stage: string | null;
  events: DiffEvent[];
  done: boolean;
  transport: "sse" | "poll";
}

const EVENTS = ["progress", "ready", "needs_align", "failed"] as const;
const TERMINAL = new Set(["ready", "needs_align", "failed"]);
const TERMINAL_EVENT: Partial<Record<DiffStatus, string>> = { READY: "ready", NEEDS_ALIGN: "needs_align", FAILED: "failed" };
const STAGE_PROGRESS: Record<string, number> = { QUEUED: 0.05, DOWNLOADING: 0.2, COMPUTING: 0.5, UPLOADING: 0.85 };
const INITIAL: DiffEventsState = { status: null, stage: null, events: [], done: false, transport: "sse" };

export function stageProgress(state: DiffEventsState): number {
  if (state.done) return 1;
  return STAGE_PROGRESS[state.stage ?? state.status ?? "QUEUED"] ?? 0.05;
}

export function useDiffEvents(diffId: string | null, nonce = 0): DiffEventsState {
  const [state, setState] = useState<DiffEventsState>(INITIAL);

  useEffect(() => {
    if (!diffId) return;
    let stopped = false;
    let es: EventSource | null = null;
    let timer: number | undefined;
    let attempt = 0;
    setState(INITIAL);

    const deliver = (event: string, data: Record<string, unknown>) => {
      const terminal = TERMINAL.has(event);
      if (terminal) {
        stopped = true; // otherwise EventSource reconnects after the server closes the stream
        es?.close();
      }
      setState((s) => ({
        ...s,
        status: (data.status as DiffStatus) ?? s.status,
        stage: (data.stage as string) ?? (terminal ? null : s.stage),
        events: [...s.events, { event, data }],
        done: terminal,
      }));
    };

    const poll = async () => {
      if (stopped) return;
      try {
        const d = await api<{ diff_run: { status: DiffStatus } }>(`/v1/diffs/${diffId}`);
        const event = TERMINAL_EVENT[d.diff_run.status];
        if (event) return deliver(event, { status: d.diff_run.status, polled: true });
        setState((s) => ({ ...s, status: d.diff_run.status }));
      } catch {
        // keep polling: the next tick may succeed
      }
      if (!stopped) timer = window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
    };

    const connect = () => {
      if (stopped) return;
      es = new EventSource(`${API_URL}/v1/diffs/${diffId}/events`, { withCredentials: true });
      es.onopen = () => {
        attempt = 0;
      };
      for (const name of EVENTS) {
        es.addEventListener(name, ((msg: MessageEvent<string>) => deliver(name, JSON.parse(msg.data))) as EventListener);
      }
      es.onerror = () => {
        if (stopped) return;
        es?.close();
        es = null;
        attempt += 1;
        if (attempt > SSE_MAX_RETRIES) {
          setState((s) => ({ ...s, transport: "poll" }));
          void poll();
        } else {
          timer = window.setTimeout(connect, sseBackoffMs(attempt));
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      es?.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [diffId, nonce]);

  return state;
}
