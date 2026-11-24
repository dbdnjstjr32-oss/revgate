export const SEVERITY_STYLE: Record<string, string> = {
  CRITICAL: "bg-red-600 text-white",
  MAJOR: "bg-amber-500 text-white",
  INFO: "bg-slate-400 text-white",
};

export const STATUS_STYLE: Record<string, string> = {
  READY: "bg-emerald-100 text-emerald-800",
  NEEDS_ALIGN: "bg-sky-100 text-sky-800",
  FAILED: "bg-red-100 text-red-800",
  QUEUED: "bg-slate-100 text-slate-700",
  COMPUTING: "bg-indigo-100 text-indigo-800",
  PARSING: "bg-indigo-100 text-indigo-800",
  PENDING_UPLOAD: "bg-slate-100 text-slate-700",
  ACTIVE: "bg-emerald-100 text-emerald-800",
  APPROVED: "bg-violet-100 text-violet-800",
  REVOKED: "bg-slate-200 text-slate-600",
  EXPIRED: "bg-slate-200 text-slate-600",
  SUPERSEDED: "bg-red-100 text-red-800",
};

const UNIT_SUFFIX: [RegExp, string][] = [
  [/_mm3$/, " mm³"],
  [/_mm2$/, " mm²"],
  [/_mm$/, " mm"],
  [/_deg$/, "°"],
];

export function factValue(key: string, value: number | string): string {
  if (typeof value === "string") return value;
  for (const [re, unit] of UNIT_SUFFIX) if (re.test(key)) return `${value.toFixed(3)}${unit}`;
  return Number.isInteger(value) ? String(value) : value.toFixed(3);
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function bytes(n: number): string {
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${(n / 1024).toFixed(0)} KB`;
}

export function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("ko-KR", { hour12: false }) : "-";
}
