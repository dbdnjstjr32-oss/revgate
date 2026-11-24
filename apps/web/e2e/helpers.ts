/** Shared steps for the full-stack browser suite (real api + cad-worker + postgres + redis + minio). */

import path from "node:path";

import { type Browser, type Locator, type Page, expect } from "@playwright/test";

export const GOLDEN = path.resolve(__dirname, "../../../workers/cad/tests/golden");
export const golden = (name: string, side: "A" | "B") => path.join(GOLDEN, name, `rev_${side}.step`);

export const unique = (prefix: string) => `${prefix}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
export const email = (who: string) => `${unique(who)}@e2e.revgate.test`;

/** Magic link login through the real API; the dev outbox hint stands in for the mailbox. */
export async function login(page: Page, address: string): Promise<void> {
  await page.goto("/login?next=/workspace");
  await page.getByTestId("login-email").fill(address);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("login-sent")).toBeVisible();
  await page.getByTestId("dev-magic-open").click();
  await page.waitForURL(/\/workspace/);
  await expect(page.getByTestId("me-email")).toHaveText(address);
}

export async function createPart(page: Page, partNumber: string, name = "브래킷"): Promise<string> {
  await page.getByTestId("part-number").fill(partNumber);
  await page.getByTestId("part-name").fill(name);
  await page.getByTestId("part-material").fill("AL6061-T6");
  await page.getByTestId("part-finish").fill("흑색 아노다이징");
  await page.getByTestId("part-create").click();
  const panel = page.getByTestId("part-panel");
  await expect(panel).toBeVisible();
  await expect(page.locator(`[data-testid=part-item][data-part-number="${partNumber}"]`)).toBeVisible();
  const id = await panel.getAttribute("data-part-id");
  expect(id).toBeTruthy();
  return id!;
}

export const revisionRow = (page: Page, label: string) => page.locator(`[data-testid=revision-row][data-rev-label="${label}"]`);

/** Drop a STEP into the workspace dropzone and wait until the CAD worker has parsed it. */
export async function uploadRevision(page: Page, label: string, file: string): Promise<void> {
  await page.getByTestId("rev-label").fill(label);
  await page.getByTestId("upload-dropzone-input").setInputFiles(file);
  await expect(revisionRow(page, label)).toHaveAttribute("data-status", "READY", { timeout: 120_000 });
}

/** Create a diff run from the workspace and follow its live SSE progress to a terminal state. */
export async function runDiff(page: Page, base: string, target: string): Promise<{ status: string; reviewUrl: string }> {
  await page.getByTestId("diff-base").selectOption({ label: `Rev ${base}` });
  await page.getByTestId("diff-target").selectOption({ label: `Rev ${target}` });
  const progress = page.getByTestId("diff-progress");
  const previous = (await progress.count()) ? await progress.getAttribute("data-diff-id") : null;
  await page.getByTestId("diff-create").click();
  await expect(progress).toHaveAttribute("data-diff-id", previous ? new RegExp(`^(?!${previous}$)[0-9a-f-]{36}$`) : /^[0-9a-f-]{36}$/);
  const diffId = await progress.getAttribute("data-diff-id");
  const open = page.getByTestId("open-review");
  await expect(open).toHaveAttribute("href", new RegExp(`/diffs/${diffId}$`), { timeout: 150_000 });
  const events = await page.getByTestId("diff-events").textContent();
  expect(events ?? "").toMatch(/(ready|needs_align|failed)$/);
  return { status: (await open.getAttribute("data-status")) ?? "", reviewUrl: (await open.getAttribute("href")) ?? "" };
}

export async function waitViewer(page: Page): Promise<void> {
  await page.waitForFunction(() => window.__REVGATE_VIEWER__?.ready === true, undefined, { timeout: 90_000 });
}

export async function viewerState(page: Page) {
  return page.evaluate(() => window.__REVGATE_VIEWER__ ?? null);
}

export async function clickCanvas(page: Page, dx = 0, dy = 0): Promise<void> {
  const box = await page.getByTestId("viewer-shell").locator("canvas").boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + box!.width / 2 + dx, box!.y + box!.height / 2 + dy);
}

/** value of the first <option> whose label matches. */
export async function optionValue(select: Locator, pattern: RegExp): Promise<string> {
  const options = await select
    .locator("option")
    .evaluateAll((els) => els.map((e) => ({ value: (e as HTMLOptionElement).value, text: e.textContent ?? "" })));
  const hit = options.find((o) => pattern.test(o.text));
  if (!hit) throw new Error(`no option matching ${pattern}: ${options.map((o) => o.text).join(" | ")}`);
  return hit.value;
}

/** Datum list entries read `평면 #id n=(x, y, z) AREAmm²`: pick the largest plane with this normal. */
export async function planeOption(select: Locator, normal: [number, number, number]): Promise<string> {
  const options = await select
    .locator("option")
    .evaluateAll((els) => els.map((e) => ({ value: (e as HTMLOptionElement).value, text: e.textContent ?? "" })));
  let best: { value: string; area: number } | null = null;
  for (const o of options) {
    const m = /n=\(([-\d.]+), ([-\d.]+), ([-\d.]+)\) (\d+)mm²/.exec(o.text);
    if (!m) continue;
    const n = [Number(m[1]), Number(m[2]), Number(m[3])];
    if (n.some((v, i) => Math.abs(v - normal[i]) > 0.01)) continue;
    const area = Number(m[4]);
    if (!best || area > best.area) best = { value: o.value, area };
  }
  if (!best) throw new Error(`no plane with normal ${normal.join(",")}: ${options.map((o) => o.text).join(" | ")}`);
  return best.value;
}

/** A second, cookie-less browser context (e.g. the anonymous shop-inbox submitter). */
export async function newPage(browser: Browser): Promise<Page> {
  const ctx = await browser.newContext({
    baseURL: process.env.WEB_URL ?? "http://localhost:3000",
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
  });
  return ctx.newPage();
}

// ---- Phase 4 fixtures (generated at runtime, deterministic) -------------------------------------
import fs from "node:fs";
import os from "node:os";

const FIXTURE_DIR = path.join(os.tmpdir(), "revgate-e2e-fixtures");

export function writeFixture(name: string, data: Buffer | string): string {
  fs.mkdirSync(FIXTURE_DIR, { recursive: true });
  const file = path.join(FIXTURE_DIR, name);
  fs.writeFileSync(file, data);
  return file;
}

/** A valid one-page PDF drawing (title block text + frame + dimension lines), xref offsets computed exactly. */
export function makePdf(title: string): Buffer {
  const content = [
    "BT /F1 20 Tf 72 730 Td (" + title.replace(/[()\\]/g, "") + ") Tj ET",
    "BT /F1 11 Tf 72 708 Td (ISO 2768-mK  /  Motor shaft bore 6 H7) Tj ET",
    "3 w 36 36 540 720 re S",
    "2 w 100 300 m 500 300 l S",
    "2 w 100 300 m 100 600 l S",
    "1 w 200 380 200 140 re S",
  ].join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${Buffer.byteLength(content, "latin1")} >>\nstream\n${content}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((o, i) => {
    offsets.push(Buffer.byteLength(out, "latin1"));
    out += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = Buffer.byteLength(out, "latin1");
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  out += offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

/** ASCII DXF: a line and a circle (same entities as the Vitest fixture). */
export const DXF_TEXT = [
  "0", "SECTION", "2", "ENTITIES",
  "0", "LINE", "8", "0", "10", "0.0", "20", "0.0", "30", "0.0", "11", "100.0", "21", "0.0", "31", "0.0",
  "0", "CIRCLE", "8", "0", "10", "50.0", "20", "30.0", "30", "0.0", "40", "10.0",
  "0", "ENDSEC", "0", "EOF", "",
].join("\n");

/** Two STEP files with identical B-Rep but different SHA-256 (header label edited, same length). */
export function sameGeometryPair(): [string, string] {
  const a = fs.readFileSync(golden("01_hole_diam", "A"));
  const b = Buffer.from(a.toString("latin1").replace("revgate-01_hole_diam-rev_A", "revgate-01_hole_diam-rev_Z"), "latin1");
  if (b.equals(a) || b.length !== a.length) throw new Error("could not derive a same-geometry STEP");
  return [writeFixture("bracket_v1.step", a), writeFixture("bracket_v2.step", b)];
}

/** Count clearly non-white pixels of a canvas (proves something was actually drawn). */
export async function inkPixels(canvas: Locator): Promise<number> {
  return canvas.evaluate((el) => {
    const c = el as HTMLCanvasElement;
    const ctx = c.getContext("2d");
    if (!ctx || !c.width || !c.height) return 0;
    const { data } = ctx.getImageData(0, 0, c.width, c.height);
    let n = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 0 && data[i] + data[i + 1] + data[i + 2] < 600) n += 1;
    return n;
  });
}
