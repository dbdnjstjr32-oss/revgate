/**
 * DXF (ASCII, parsed by dxf-parser) -> flat 2D drawing primitives for a Canvas2D preview (Phase 4 §4).
 * Lines, (bulged) polylines, circles, arcs, ellipses, splines (fit / control polygon) and text are drawn;
 * other entity types (INSERT blocks, dimensions, solids) are counted as skipped.
 */

import type { IDxf, IEntity } from "dxf-parser";

export type Pt = [number, number];
export type Prim = { kind: "path"; points: Pt[]; closed: boolean } | { kind: "text"; at: Pt; text: string; height: number };

export interface Drawing {
  prims: Prim[];
  bounds: { minX: number; minY: number; maxX: number; maxY: number } | null;
  drawn: number;
  skipped: number;
}

type P = { x: number; y: number };
type AnyEntity = IEntity & Record<string, unknown>;

const SEG = 64; // segments per full circle

function arcPoints(cx: number, cy: number, r: number, a0: number, a1: number): Pt[] {
  let sweep = a1 - a0;
  while (sweep <= 0) sweep += 2 * Math.PI; // DXF arcs run counter-clockwise from start to end
  const n = Math.max(4, Math.ceil((SEG * sweep) / (2 * Math.PI)));
  return Array.from({ length: n + 1 }, (_, i) => {
    const a = a0 + (sweep * i) / n;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)] as Pt;
  });
}

/** One polyline segment p -> q with DXF bulge = tan(theta / 4). */
function bulgeSegment(p: P, q: P, bulge: number): Pt[] {
  if (!bulge) return [[q.x, q.y]];
  const theta = 4 * Math.atan(bulge);
  const chord = Math.hypot(q.x - p.x, q.y - p.y);
  if (chord === 0) return [[q.x, q.y]];
  const r = chord / (2 * Math.sin(Math.abs(theta) / 2));
  const mid = { x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 };
  const h = Math.sqrt(Math.max(0, r * r - (chord / 2) ** 2));
  const dir = { x: (q.x - p.x) / chord, y: (q.y - p.y) / chord };
  const side = bulge > 0 === Math.abs(theta) < Math.PI ? 1 : -1; // centre left of the chord for CCW minor arcs
  const c = { x: mid.x - dir.y * h * side, y: mid.y + dir.x * h * side };
  const a0 = Math.atan2(p.y - c.y, p.x - c.x);
  const n = Math.max(4, Math.ceil((SEG * Math.abs(theta)) / (2 * Math.PI)));
  return Array.from({ length: n }, (_, i) => {
    const a = a0 + (theta * (i + 1)) / n;
    return [c.x + r * Math.cos(a), c.y + r * Math.sin(a)] as Pt;
  });
}

function polyline(vertices: (P & { bulge?: number })[], closed: boolean): Pt[] {
  if (!vertices.length) return [];
  const pts: Pt[] = [[vertices[0].x, vertices[0].y]];
  const n = vertices.length;
  for (let i = 0; i < (closed ? n : n - 1); i++) {
    pts.push(...bulgeSegment(vertices[i], vertices[(i + 1) % n], vertices[i].bulge ?? 0));
  }
  return pts;
}

function entityPrims(e: AnyEntity): Prim[] | null {
  switch (e.type) {
    case "LINE": {
      const v = (e.vertices as P[]) ?? [];
      return v.length >= 2 ? [{ kind: "path", points: v.map((p) => [p.x, p.y] as Pt), closed: false }] : [];
    }
    case "LWPOLYLINE":
    case "POLYLINE": {
      const closed = Boolean(e.shape) || Boolean(e.closed);
      return [{ kind: "path", points: polyline((e.vertices as (P & { bulge?: number })[]) ?? [], closed), closed }];
    }
    case "CIRCLE": {
      const c = e.center as P;
      return [{ kind: "path", points: arcPoints(c.x, c.y, e.radius as number, 0, 2 * Math.PI), closed: true }];
    }
    case "ARC": {
      const c = e.center as P;
      return [{ kind: "path", points: arcPoints(c.x, c.y, e.radius as number, e.startAngle as number, e.endAngle as number), closed: false }];
    }
    case "ELLIPSE": {
      const c = e.center as P;
      const m = e.majorAxisEndPoint as P;
      const a = Math.hypot(m.x, m.y);
      const b = a * (e.axisRatio as number);
      const rot = Math.atan2(m.y, m.x);
      const t0 = (e.startAngle as number) ?? 0;
      let t1 = (e.endAngle as number) ?? 2 * Math.PI;
      if (t1 <= t0) t1 += 2 * Math.PI;
      const pts = Array.from({ length: SEG + 1 }, (_, i) => {
        const t = t0 + ((t1 - t0) * i) / SEG;
        const x = a * Math.cos(t);
        const y = b * Math.sin(t);
        return [c.x + x * Math.cos(rot) - y * Math.sin(rot), c.y + x * Math.sin(rot) + y * Math.cos(rot)] as Pt;
      });
      return [{ kind: "path", points: pts, closed: Math.abs(t1 - t0 - 2 * Math.PI) < 1e-9 }];
    }
    case "SPLINE": {
      const pts = ((e.fitPoints as P[] | undefined)?.length ? e.fitPoints : e.controlPoints) as P[] | undefined;
      return pts?.length ? [{ kind: "path", points: pts.map((p) => [p.x, p.y] as Pt), closed: Boolean(e.closed) }] : [];
    }
    case "TEXT":
    case "MTEXT": {
      const at = (e.startPoint ?? e.position) as P | undefined;
      const text = String(e.text ?? "").replace(/\\P/g, " ").trim();
      return at && text ? [{ kind: "text", at: [at.x, at.y], text, height: Number(e.textHeight ?? e.height ?? 2.5) }] : [];
    }
    default:
      return null;
  }
}

export function toDrawing(dxf: IDxf | null): Drawing {
  const prims: Prim[] = [];
  let skipped = 0;
  for (const e of dxf?.entities ?? []) {
    const p = entityPrims(e as AnyEntity);
    if (p === null) skipped += 1;
    else prims.push(...p);
  }
  let bounds: Drawing["bounds"] = null;
  const grow = ([x, y]: Pt) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    bounds = bounds
      ? { minX: Math.min(bounds.minX, x), minY: Math.min(bounds.minY, y), maxX: Math.max(bounds.maxX, x), maxY: Math.max(bounds.maxY, y) }
      : { minX: x, minY: y, maxX: x, maxY: y };
  };
  for (const p of prims) (p.kind === "path" ? p.points : [p.at]).forEach(grow);
  return { prims, bounds, drawn: prims.length, skipped };
}

/** Fit drawing bounds into a w x h canvas (Y up in DXF -> Y down on canvas). */
export function fitTransform(bounds: NonNullable<Drawing["bounds"]>, w: number, h: number, pad = 16) {
  const bw = Math.max(bounds.maxX - bounds.minX, 1e-9);
  const bh = Math.max(bounds.maxY - bounds.minY, 1e-9);
  const s = Math.min((w - 2 * pad) / bw, (h - 2 * pad) / bh);
  const ox = (w - bw * s) / 2 - bounds.minX * s;
  const oy = (h + bh * s) / 2 + bounds.minY * s;
  return { s, map: ([x, y]: Pt): Pt => [ox + x * s, oy - y * s] };
}

export function drawToCanvas(ctx: CanvasRenderingContext2D, drawing: Drawing, w: number, h: number): void {
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
  if (!drawing.bounds) return;
  const { s, map } = fitTransform(drawing.bounds, w, h);
  ctx.strokeStyle = "#0f172a";
  ctx.fillStyle = "#0f172a";
  ctx.lineWidth = 1.2;
  for (const p of drawing.prims) {
    if (p.kind === "path") {
      if (p.points.length < 2) continue;
      ctx.beginPath();
      p.points.forEach((pt, i) => {
        const [x, y] = map(pt);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      if (p.closed) ctx.closePath();
      ctx.stroke();
    } else {
      const [x, y] = map(p.at);
      ctx.font = `${Math.max(9, p.height * s)}px sans-serif`;
      ctx.fillText(p.text, x, y);
    }
  }
}
