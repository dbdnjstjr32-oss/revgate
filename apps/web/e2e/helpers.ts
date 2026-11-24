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
