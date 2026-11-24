/** Browser client for apps/api. Cookies (rg_sid / rg_ext_sid) are HttpOnly, so every call is `credentials: include`. */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public body: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export async function api<T>(path: string, opts: { method?: string; json?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: opts.method ?? (opts.json === undefined ? "GET" : "POST"),
    credentials: "include",
    headers: opts.json === undefined ? undefined : { "Content-Type": "application/json" },
    body: opts.json === undefined ? undefined : JSON.stringify(opts.json),
    signal: opts.signal,
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = (body?.error ?? {}) as Record<string, unknown>;
    const code = typeof err.code === "string" ? err.code : `HTTP_${res.status}`;
    const message = typeof err.message === "string" ? err.message : res.statusText;
    throw new ApiError(res.status, code, message, err);
  }
  return (await res.json()) as T;
}

/** Signed URLs (MinIO / R2) — no cookies; Brotli (`Content-Encoding: br`) is decoded by the browser. */
export async function fetchSignedJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`signed URL fetch failed: ${res.status}`);
  return (await res.json()) as T;
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return `${e.code}: ${e.message}`;
  return e instanceof Error ? e.message : String(e);
}

/** Local-development helper (GET /v1/dev/outbox) — absent in production, so failures are silent. */
export async function devOutbox(params: Record<string, string>): Promise<Array<{ to: string; kind: string; data: Record<string, string> }>> {
  try {
    const qs = new URLSearchParams(params).toString();
    const res = await fetch(`${API_URL}/v1/dev/outbox?${qs}`, { credentials: "include", cache: "no-store" });
    if (!res.ok) return [];
    return ((await res.json()) as { messages: Array<{ to: string; kind: string; data: Record<string, string> }> }).messages;
  } catch {
    return [];
  }
}
