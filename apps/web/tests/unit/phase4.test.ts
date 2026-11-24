import DxfParser from "dxf-parser";
import { describe, expect, it } from "vitest";

import { fitTransform, toDrawing } from "@/lib/dxf";
import {
  POLL_INTERVAL_MS, SIGNED_URL_TTL_MS, SSE_MAX_RETRIES, isTap, prefersLowQuality, refreshDelayMs, sseBackoffMs,
} from "@/lib/interaction";
import type { Measurement } from "@/lib/measure";
import { CAD_NOMINAL, MESH_APPROX, formatMeasureValue, reliability } from "@/lib/measureFormat";
import { type KeyValueStore, OfflineQueue, isNetworkError } from "@/lib/offlineQueue";
import { ackRequired, cleanSpecCard, dfmChecksFor, emptySpecCard, specOnlyChange } from "@/lib/spec";

export const DXF_FIXTURE = [
  "0", "SECTION", "2", "ENTITIES",
  "0", "LINE", "8", "0", "10", "0.0", "20", "0.0", "30", "0.0", "11", "100.0", "21", "0.0", "31", "0.0",
  "0", "CIRCLE", "8", "0", "10", "50.0", "20", "30.0", "30", "0.0", "40", "10.0",
  "0", "ENDSEC", "0", "EOF", "",
].join("\n");

describe("tap vs rotate (tablet)", () => {
  it("only a short, still press is a tap", () => {
    expect(isTap({ t: 0, x: 10, y: 10 }, { t: 120, x: 13, y: 14 })).toBe(true);
    expect(isTap({ t: 0, x: 10, y: 10 }, { t: 200, x: 10, y: 10 })).toBe(false); // 200 ms is a hold
    expect(isTap({ t: 0, x: 10, y: 10 }, { t: 50, x: 18, y: 10 })).toBe(false); // 8 px is a drag
    expect(isTap(null, { t: 1, x: 0, y: 0 })).toBe(false);
  });

  it("starts on lod1 for coarse pointers / low-spec devices", () => {
    const mk = (coarse: boolean, cores = 8, mem = 8) => ({
      matchMedia: (q: string) => ({ matches: coarse && q === "(pointer: coarse)" }) as MediaQueryList,
      navigator: { hardwareConcurrency: cores, deviceMemory: mem } as unknown as Navigator,
    });
    expect(prefersLowQuality(mk(true))).toBe(true);
    expect(prefersLowQuality(mk(false))).toBe(false);
    expect(prefersLowQuality(mk(false, 2))).toBe(true);
    expect(prefersLowQuality(mk(false, 8, 2))).toBe(true);
  });
});

describe("signed URL refresh + SSE fallback", () => {
  it("refreshes 15 min URLs at the 12 min mark", () => {
    expect(SIGNED_URL_TTL_MS).toBe(15 * 60_000);
    expect(refreshDelayMs(0, 0)).toBe(12 * 60_000);
    expect(refreshDelayMs(0, 5 * 60_000)).toBe(7 * 60_000);
    expect(refreshDelayMs(0, 13 * 60_000)).toBe(0);
  });

  it("backs off 1 s, 2 s, 4 s before the 4 s polling fallback", () => {
    expect([1, 2, 3].map(sseBackoffMs)).toEqual([1000, 2000, 4000]);
    expect(SSE_MAX_RETRIES).toBe(3);
    expect(POLL_INTERVAL_MS).toBe(4000);
  });
});

describe("offline queue (sessionStorage)", () => {
  const memory = (): KeyValueStore & { data: Map<string, string> } => {
    const data = new Map<string, string>();
    return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k) };
  };

  it("coalesces checks per item, keeps pins in order and replays them", async () => {
    const store = memory();
    const q = new OfflineQueue(store, "rg:q");
    q.push({ kind: "check", itemId: "a", checked: true });
    q.push({ kind: "pin", diffId: "d", pin: { body: "1" } });
    q.push({ kind: "check", itemId: "a", checked: false }); // last state wins
    q.push({ kind: "pin", diffId: "d", pin: { body: "2" } });
    expect(q.list().map((o) => (o.kind === "check" ? `c:${o.checked}` : `p:${o.pin.body}`))).toEqual(["p:1", "c:false", "p:2"]);
    expect(new OfflineQueue(store, "rg:q").list()).toHaveLength(3); // survives a reload (same storage)

    const seen: string[] = [];
    const res = await q.flush(async (o) => void seen.push(o.kind));
    expect(seen).toEqual(["pin", "check", "pin"]);
    expect(res).toEqual({ done: 3, remaining: [] });
    expect(store.data.has("rg:q")).toBe(false);
  });

  it("stops at a network error and drops ops the server rejects", async () => {
    const q = new OfflineQueue(memory(), "rg:q");
    q.push({ kind: "check", itemId: "a", checked: true });
    q.push({ kind: "check", itemId: "b", checked: true });
    const offline = await q.flush(async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(offline.done).toBe(0);
    expect(offline.remaining).toHaveLength(2);
    const rejected = await q.flush(async (o) => {
      if (o.kind === "check" && o.itemId === "a") throw new Error("LINK_LOCKED");
    });
    expect(rejected).toEqual({ done: 1, remaining: [] });
    expect(isNetworkError(new TypeError("x"), true)).toBe(true);
    expect(isNetworkError(new Error("409"), true)).toBe(false);
    expect(isNetworkError(new Error("409"), false)).toBe(true);
  });
});

describe("DXF preview geometry", () => {
  it("parses ASCII DXF with dxf-parser and fits it into the canvas", () => {
    const drawing = toDrawing(new DxfParser().parseSync(DXF_FIXTURE));
    expect(drawing.drawn).toBe(2);
    expect(drawing.skipped).toBe(0);
    expect(drawing.bounds).toEqual({ minX: 0, minY: 0, maxX: 100, maxY: 40 });
    const { map } = fitTransform(drawing.bounds!, 300, 200, 10);
    const [x0, y0] = map([0, 0]);
    const [x1] = map([100, 0]);
    expect(x0).toBeGreaterThanOrEqual(10);
    expect(x1).toBeLessThanOrEqual(290);
    expect(y0).toBeGreaterThan(map([0, 40])[1]); // DXF Y up -> canvas Y down
  });

  it("turns LWPOLYLINE bulges into arcs and counts unsupported entities", () => {
    const drawing = toDrawing({
      entities: [
        { type: "LWPOLYLINE", shape: false, vertices: [{ x: 0, y: 0, bulge: Math.tan(Math.PI / 8) }, { x: 2, y: 0 }] },
        { type: "INSERT", name: "BLOCK1" },
      ],
    } as never);
    expect(drawing.skipped).toBe(1);
    const path = drawing.prims[0];
    expect(path.kind).toBe("path");
    if (path.kind !== "path") return;
    const last = path.points[path.points.length - 1];
    expect(last[0]).toBeCloseTo(2, 9);
    expect(last[1]).toBeCloseTo(0, 9);
    expect(drawing.bounds!.minY).toBeCloseTo(1 - Math.SQRT2, 2); // a CCW 90° arc dips below the chord
  });
});

describe("manufacturing spec helpers", () => {
  it("cleans an editor draft into a strict SpecCardV2 body", () => {
    const draft = emptySpecCard();
    draft.process = "PRINT_FDM";
    draft.material = { grade: " PA12 ", standard: "" };
    draft.surface_finish = { type: "", ra_um: Number.NaN, color: " 흑색 " };
    draft.critical_tolerances = [{ id: "", location: "축 홀", spec: "Ø6 H7", datum_ref: "" }, { id: "x", location: "", spec: "" }];
    draft.threads_and_inserts = [{ spec: "M3", count: 4, insert_type: "HEAT_SET_BRASS", note: "" }];
    draft.additive_spec = { build_orientation: "", infill_pct: 40 };
    const card = cleanSpecCard(draft);
    expect(card.material).toEqual({ grade: "PA12" });
    expect(card.surface_finish).toEqual({ type: "", color: "흑색" });
    expect(card.critical_tolerances).toEqual([{ id: "CT1", location: "축 홀", spec: "Ø6 H7" }]);
    expect(card.threads_and_inserts).toEqual([{ spec: "M3", count: 4, insert_type: "HEAT_SET_BRASS" }]);
    expect(card.additive_spec).toEqual({ build_orientation: "", infill_pct: 40 });
    expect(cleanSpecCard({ ...draft, process: "CNC_MILLING" }).additive_spec).toBeUndefined();
  });

  it("knows the process checklists, forced acks and the spec-only edge case", () => {
    expect(dfmChecksFor("CNC_TURNING").map((c) => c.id)).toEqual(["corner_r", "thin_wall", "precision_fit", "coating_shift", "datum_setup"]);
    expect(dfmChecksFor("PRINT_SLA_SLS_MJF").map((c) => c.id)).toEqual(["orientation", "overhang", "heat_insert", "shrinkage"]);
    expect(dfmChecksFor("SHEET_METAL")).toEqual([]);
    expect(ackRequired([
      { code: "MISSING_TOLERANCE", level: "ACK_REQUIRED", message: "" },
      { code: "UNDECLARED_THREAD", level: "CAUTION", message: "" },
    ]).map((w) => w.code)).toEqual(["MISSING_TOLERANCE"]);
    const diff = [{ field: "material.grade", label: "재질", before: "A", after: "B", severity: "CRITICAL" as const }];
    expect(specOnlyChange(0, diff)).toBe(true);
    expect(specOnlyChange(2, diff)).toBe(false);
    expect(specOnlyChange(0, [])).toBe(false);
  });

  it("labels analytical vs mesh measurements", () => {
    const m = (approximate: boolean): Measurement => ({
      kind: approximate ? "POINT_POINT" : "CYL_CYL", approximate, badges: [], summary: "",
      values: [{ key: "diameter_a", label: "Ø A", value: 4.2, unit: "mm" }],
    });
    expect(formatMeasureValue(m(false).values[0], false)).toBe("Ø4.200 mm");
    expect(formatMeasureValue(m(true).values[0], true)).toBe("~Ø4.20 mm");
    expect(formatMeasureValue({ key: "dihedral_angle", label: "", value: 90, unit: "deg" }, false)).toBe("90.00°");
    expect(reliability(m(false)).label).toBe(CAD_NOMINAL);
    expect(reliability(m(true)).label).toBe(MESH_APPROX);
  });
});
