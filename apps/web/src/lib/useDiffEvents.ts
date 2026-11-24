"use client";

/** SSE /v1/diffs/{id}/events (Redis Pub/Sub fan-out from the CAD worker): progress | ready | needs_align | failed. */

import { useEffect, useState } from "react";

import { API_URL } from "./api";
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
}

const TERMINAL = new Set(["ready", "needs_align", "failed"]);
const STAGE_PROGRESS: Record<string, number> = { QUEUED: 0.05, DOWNLOADING: 0.2, COMPUTING: 0.5, UPLOADING: 0.85 };

export function stageProgress(state: DiffEventsState): number {
  if (state.done) return 1;
  return STAGE_PROGRESS[state.stage ?? state.status ?? "QUEUED"] ?? 0.05;
}

export function useDiffEvents(diffId: string | null, nonce = 0): DiffEventsState {
  const [state, setState] = useState<DiffEventsState>({ status: null, stage: null, events: [], done: false });

  useEffect(() => {
    if (!diffId) return;
    setState({ status: null, stage: null, events: [], done: false });
    const es = new EventSource(`${API_URL}/v1/diffs/${diffId}/events`, { withCredentials: true });
    const handle = (event: string) => (msg: MessageEvent<string>) => {
      const data = JSON.parse(msg.data) as Record<string, unknown>;
      setState((s) => ({
        status: (data.status as DiffStatus) ?? s.status,
        stage: (data.stage as string) ?? (TERMINAL.has(event) ? null : s.stage),
        events: [...s.events, { event, data }],
        done: TERMINAL.has(event),
      }));
      if (TERMINAL.has(event)) es.close(); // otherwise EventSource reconnects after the server closes
    };
    for (const name of ["progress", "ready", "needs_align", "failed"]) es.addEventListener(name, handle(name) as EventListener);
    return () => es.close();
  }, [diffId, nonce]);

  return state;
}
