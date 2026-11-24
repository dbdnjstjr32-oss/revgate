/**
 * Phase 4 §5.4 — factory Wi-Fi drops: checklist ticks and 3D pins that fail with a network error are kept in
 * sessionStorage and replayed when the browser reports `online` again. Checks are coalesced per item
 * (the last state wins); pins are replayed in order.
 */

export type QueuedOp =
  | { id: string; kind: "check"; itemId: string; checked: boolean }
  | { id: string; kind: "pin"; diffId: string; pin: Record<string, unknown> };

type NewOp = QueuedOp extends infer T ? (T extends QueuedOp ? Omit<T, "id"> : never) : never;

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** fetch() rejects with a TypeError when the network is unreachable (HTTP errors resolve normally). */
export function isNetworkError(e: unknown, online: boolean = typeof navigator === "undefined" || navigator.onLine !== false): boolean {
  return !online || e instanceof TypeError;
}

export class OfflineQueue {
  constructor(private store: KeyValueStore | null, private key: string) {}

  list(): QueuedOp[] {
    try {
      const raw = this.store?.getItem(this.key);
      return raw ? (JSON.parse(raw) as QueuedOp[]) : [];
    } catch {
      return [];
    }
  }

  private save(ops: QueuedOp[]): void {
    try {
      if (ops.length) this.store?.setItem(this.key, JSON.stringify(ops));
      else this.store?.removeItem(this.key);
    } catch {
      // storage unavailable (private mode / quota): the op is simply not persisted
    }
  }

  push(op: NewOp): QueuedOp[] {
    const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    let ops = this.list();
    if (op.kind === "check") ops = ops.filter((o) => !(o.kind === "check" && o.itemId === op.itemId));
    ops = [...ops, { ...op, id } as QueuedOp];
    this.save(ops);
    return ops;
  }

  /** Replay in order; stop at the first network failure (still offline), drop ops the server rejects. */
  async flush(run: (op: QueuedOp) => Promise<void>): Promise<{ done: number; remaining: QueuedOp[] }> {
    let done = 0;
    for (const op of this.list()) {
      try {
        await run(op);
        done += 1;
        this.save(this.list().filter((o) => o.id !== op.id));
      } catch (e) {
        if (isNetworkError(e)) break;
        this.save(this.list().filter((o) => o.id !== op.id)); // e.g. LINK_LOCKED: replaying again would never work
      }
    }
    return { done, remaining: this.list() };
  }
}
