/**
 * Full-stack browser E2E — no mocks: Next.js production build + FastAPI + ARQ CAD worker (real OCP)
 * + Postgres + Redis + MinIO, driven through Chromium with SwiftShader WebGL2.
 * Golden STEP pairs come from workers/cad/tests/golden (blueprint §7.1).
 */

import { expect, test } from "@playwright/test";

import {
  clickCanvas,
  createPart,
  email,
  golden,
  login,
  newPage,
  optionValue,
  planeOption,
  revisionRow,
  runDiff,
  unique,
  uploadRevision,
  viewerState,
  waitViewer,
} from "./helpers";

test.describe.serial("01_hole_diam: designer review → external OTP reviewer → approval", () => {
  const designer = email("designer");
  const shopContact = email("shop");
  let reviewPath = "";
  let shareUrl = "";

  test("designer uploads Rev A/B, follows the SSE diff and reviews it PR-style", async ({ page }) => {
    await login(page, designer);
    await createPart(page, unique("E2E-HOLE"));
    await uploadRevision(page, "A", golden("01_hole_diam", "A"));
    await uploadRevision(page, "B", golden("01_hole_diam", "B"));
    const run = await runDiff(page, "A", "B");
    expect(run.status).toBe("READY");

    await page.getByTestId("open-review").click();
    await page.waitForURL(/\/parts\/[^/]+\/diffs\/[^/]+$/);
    reviewPath = new URL(page.url()).pathname;
    await expect(page.getByTestId("diff-status")).toHaveAttribute("data-status", "READY");
    await waitViewer(page);

    // checklist: 2 x HOLE_DIAMETER, [확정] facts kept apart from the (참고 추정) engineering hint
    const items = page.getByTestId("change-item");
    await expect(items).toHaveCount(2);
    for (const item of await items.all()) {
      await expect(item).toHaveAttribute("data-category", "HOLE_DIAMETER");
      await expect(item.getByTestId("change-title")).toContainText("Ø3.30 → Ø4.20");
      const fact = item.getByTestId("change-fact");
      await expect(fact).toContainText("from_diameter_mm");
      await expect(fact).toContainText("3.300 mm");
      await expect(fact).toContainText("4.200 mm");
      await expect(fact).not.toContainText("추정");
      const hint = item.getByTestId("change-hint");
      await expect(hint).toContainText("(참고 추정 · HIGH)");
      await expect(hint).toContainText("M4 → M5");
    }
    await expect(page.getByTestId("change-progress")).toHaveText("확인 0/2");

    // one mesh = one draw call: Before (Base) with the delta overlays off renders exactly 1 call
    await page.getByTestId("toggle-added").click();
    await page.getByTestId("toggle-removed").click();
    await expect(page.getByTestId("toggle-added")).toHaveAttribute("aria-pressed", "false");
    await page.getByTestId("view-base").click();
    await page.waitForFunction(() => window.__REVGATE_VIEWER__?.mode === "BASE" && window.__REVGATE_VIEWER__?.calls === 1);
    await page.getByTestId("view-overlay").click();
    await page.waitForFunction(() => window.__REVGATE_VIEWER__?.mode === "OVERLAY" && window.__REVGATE_VIEWER__?.calls === 2);
    await page.getByTestId("toggle-ghost").click();
    await page.waitForFunction(() => window.__REVGATE_VIEWER__?.ghost === true);
    await page.getByTestId("toggle-ghost").click();
    await page.waitForFunction(() => window.__REVGATE_VIEWER__?.ghost === false);

    // O(1) picking: hit.face.a -> attributes._face_id
    await page.getByTestId("view-base").click();
    await clickCanvas(page);
    await expect(page.getByTestId("pick-info")).toContainText("선택: BASE");
    const pick = (await viewerState(page))?.lastPick;
    expect(pick?.side).toBe("BASE");
    expect(Number.isInteger(pick?.faceId)).toBe(true);

    // analytical measure (analytical_meta): the changed holes (88,12) & (80,50) are 38.833 mm apart
    const a = page.getByTestId("measure-a");
    const b = page.getByTestId("measure-b");
    await a.selectOption(await optionValue(a, /^BASE · (홀|보스) #\d+ Ø3\.30 @\(88\.0, 12\.0/));
    await b.selectOption(await optionValue(b, /^BASE · (홀|보스) #\d+ Ø3\.30 @\(80\.0, 50\.0/));
    await expect(page.getByTestId("measure-result")).toHaveAttribute("data-kind", "CYL_CYL");
    await expect(page.getByTestId("measure-summary")).toContainText("중심거리 38.833 mm");
    // Base Ø3.30 vs Target Ø4.20 at the same spot -> coaxial
    await b.selectOption(await optionValue(b, /^TARGET · (홀|보스) #\d+ Ø4\.20 @\(88\.0, 12\.0/));
    await expect(page.getByTestId("measure-summary")).toContainText("중심거리 0.000 mm");
    await expect(page.getByTestId("measure-badge")).toContainText(["동축(Coaxial)"]);

    // 3D pin anchored on a picked face
    await page.getByTestId("tool-pin").click();
    await clickCanvas(page);
    await expect(page.getByTestId("pin-draft")).toBeVisible();
    await page.getByTestId("pin-body").fill("설계: 이 면 기준으로 가공해 주세요");
    await page.getByTestId("pin-save").click();
    await expect(page.getByTestId("pin-item")).toHaveCount(1);
    await expect(page.getByTestId("pin-marker")).toHaveCount(1);
    await page.getByTestId("tool-select").click();

    // selecting a change highlights it and flies the camera there
    await items.first().getByTestId("change-title").click();
    await expect(items.first()).toHaveClass(/border-amber-400/);

    // spec card + share link (the token is shown exactly once)
    await expect(page.getByTestId("spec-card")).toContainText("AL6061-T6");
    await page.getByTestId("share-name").fill("OO정밀");
    await page.getByTestId("share-contact").fill(shopContact);
    await page.getByTestId("share-create").click();
    shareUrl = (await page.getByTestId("share-url").getAttribute("href")) ?? "";
    expect(shareUrl).toMatch(/^http:\/\/localhost:3000\/r\/[\w-]{20,}$/);
    await expect(page.locator("[data-testid=link-item][data-status=ACTIVE]")).toHaveCount(1);
  });

  test("external reviewer: OTP → watermark → checks + pin → approval PDF → shop CTA; tampering blurs", async ({ page }) => {
    expect(shareUrl, "share link from the designer step").not.toBe("");
    await page.goto(new URL(shareUrl).pathname);
    await expect(page.getByTestId("link-meta")).toContainText("변경점 2건");
    await page.getByTestId("otp-request").click();
    await expect(page.getByTestId("otp-sent")).toBeVisible();
    await expect(page.getByTestId("dev-otp-code")).toHaveText(/^\d{6}$/);
    await page.getByTestId("dev-otp-fill").click();
    await page.getByTestId("otp-verify").click();

    // dynamic watermark: fixed full-screen SVG tile, -28deg, opacity 0.07, company · contact · time · diff id
    const wm = page.getByTestId("watermark");
    await expect(wm).toHaveAttribute("data-text", /^OO정밀 · .+ · \d{4}-\d{2}-\d{2} \d{2}:\d{2} · [0-9a-f]{8}$/);
    expect(await wm.evaluate((el) => {
      const cs = getComputedStyle(el);
      return `${cs.position}|${cs.pointerEvents}|${cs.zIndex}`;
    })).toBe("fixed|none|40");
    expect(await wm.locator("svg").evaluate((el) => getComputedStyle(el).opacity)).toBe("0.07");
    await expect(wm.locator("pattern")).toHaveAttribute("patternTransform", "rotate(-28)");
    await waitViewer(page);
    await expect(page.getByTestId("viewer-shell")).toHaveAttribute("data-blurred", "false");

    // the designer's pin is there; the reviewer adds one
    await expect(page.getByTestId("pin-item")).toHaveCount(1);
    await page.getByTestId("view-base").click();
    await page.getByTestId("tool-pin").click();
    await clickCanvas(page);
    await page.getByTestId("pin-body").fill("가공: 이 홀 M5 탭 맞습니까?");
    await page.getByTestId("pin-save").click();
    await expect(page.getByTestId("pin-item")).toHaveCount(2);
    await expect(page.locator("[data-testid=pin-item]", { hasText: "가공업체" })).toHaveCount(1);

    // approval needs every change checked
    const submit = page.getByTestId("approve-submit");
    await page.getByTestId("approve-agree").check();
    await expect(submit).toBeDisabled();
    for (const box of await page.getByTestId("change-check").all()) await box.click();
    await expect(page.locator("[data-testid=change-item][data-checked=true]")).toHaveCount(2);
    await expect(page.getByTestId("approve-progress")).toHaveText("2/2");
    await expect(submit).toBeEnabled();
    await submit.click();

    await expect(page.getByTestId("approved-card")).toBeVisible();
    await expect(page.getByTestId("link-status")).toHaveAttribute("data-status", "APPROVED");
    const pdfUrl = await page.getByTestId("approval-pdf").getAttribute("href");
    const pdf = await page.request.get(pdfUrl!);
    expect(pdf.status()).toBe(200);
    expect((await pdf.body()).subarray(0, 5).toString("latin1")).toBe("%PDF-");
    expect(pdf.headers()["content-disposition"] ?? "").toContain("approval.pdf");
    await expect(page.getByTestId("shop-cta")).toContainText("우리 공장 전용 무료 도면 접수함 만들기");
    await expect(page.getByTestId("change-check").first()).toBeDisabled(); // snapshot locked

    // tamper 1: hide the watermark -> the 3D canvas is blurred at once
    await wm.evaluate((el) => {
      (el as HTMLElement).style.display = "none";
    });
    await expect(page.getByTestId("tamper-alert")).toBeVisible();
    await expect(page.getByTestId("viewer-shell")).toHaveAttribute("data-blurred", "true");
    expect(await page.getByTestId("viewer-shell").evaluate((el) => getComputedStyle(el).filter)).toContain("blur");

    // tamper 2 (fresh load, rg_ext_sid kept): delete the watermark node
    await page.reload();
    await waitViewer(page);
    await expect(page.getByTestId("approved-card")).toBeVisible();
    await expect(page.getByTestId("viewer-shell")).toHaveAttribute("data-blurred", "false");
    await page.evaluate(() => document.querySelector('[data-testid="watermark"]')?.remove());
    await expect(page.getByTestId("tamper-alert")).toBeVisible();
    await expect(page.getByTestId("viewer-shell")).toHaveAttribute("data-blurred", "true");
  });

  test("designer sees the approval, the reviewer's checks and the evidence PDF", async ({ page }) => {
    expect(reviewPath).not.toBe("");
    await login(page, designer);
    await page.goto(reviewPath);
    await waitViewer(page);
    await expect(page.locator("[data-testid=link-item][data-status=APPROVED]")).toHaveCount(1);
    await expect(page.locator("[data-testid=change-item][data-checked=true]")).toHaveCount(2);
    await expect(page.getByTestId("change-item").first()).toContainText(shopContact);
    await expect(page.getByTestId("pin-item")).toHaveCount(2);

    await page.evaluate(() => {
      window.open = ((url?: string | URL) => {
        document.body.dataset.opened = String(url);
        return null;
      }) as typeof window.open;
    });
    await page.getByTestId("link-pdf").click();
    await expect(page.locator("body")).toHaveAttribute("data-opened", /^http:\/\/localhost:9000\//);
    const url = await page.locator("body").getAttribute("data-opened");
    const pdf = await page.request.get(url!);
    expect((await pdf.body()).subarray(0, 5).toString("latin1")).toBe("%PDF-");
  });
});

test("05_symmetric: NEEDS_ALIGN → 3-2-1 datum align in the browser → realign → READY with 4 VOLUME_DELTA", async ({ page }) => {
  await login(page, email("align"));
  await createPart(page, unique("E2E-CUBE"), "대칭 큐브");
  await uploadRevision(page, "A", golden("05_symmetric_needs_align", "A"));
  await uploadRevision(page, "B", golden("05_symmetric_needs_align", "B"));
  const run = await runDiff(page, "A", "B");
  expect(run.status).toBe("NEEDS_ALIGN");

  await page.getByTestId("open-review").click();
  await expect(page.getByTestId("diff-status")).toHaveAttribute("data-status", "NEEDS_ALIGN");
  await expect(page.getByTestId("datum-panel")).toContainText("자동 정합 신뢰도가 낮습니다");
  await waitViewer(page);

  // the same fixture setup on both revisions: bottom (primary) -> front (secondary) -> left (tertiary)
  const normals: [number, number, number][] = [[0, 0, -1], [0, -1, 0], [-1, 0, 0]];
  for (const side of ["base", "target"] as const) {
    for (let i = 0; i < 3; i++) {
      const select = page.getByTestId(`datum-${side}-${i}`);
      await expect(select.locator("option")).not.toHaveCount(1);
      await select.selectOption(await planeOption(select, normals[i]));
    }
  }
  const preview = page.getByTestId("datum-preview");
  await expect(preview).toBeVisible();
  const rows = (await preview.textContent())!.trim().split("\n").map((r) => r.trim().split(/\s+/).map(Number));
  expect(rows).toHaveLength(4);
  rows.forEach((row, r) => row.forEach((v, c) => expect(Math.abs(v - (r === c ? 1 : 0))).toBeLessThan(1e-3)));

  await page.getByTestId("datum-apply").click();
  await expect(page.getByTestId("computing-card")).toBeVisible();
  await expect(page.getByTestId("diff-status")).toHaveAttribute("data-status", "READY", { timeout: 150_000 });
  await expect(page.getByText("MANUAL_321")).toBeVisible();
  const items = page.getByTestId("change-item");
  await expect(items).toHaveCount(4);
  for (const item of await items.all()) await expect(item).toHaveAttribute("data-category", "VOLUME_DELTA");
  await waitViewer(page);
});

test("ZERO_RETENTION: paired drop → diff → raw purged → re-diff blocked → hash-verified re-drop in the browser", async ({ page }) => {
  await login(page, email("zero"));
  await page.getByTestId("mode-zero").check();
  await page.getByTestId("settings-save").click();
  await expect(page.getByTestId("settings-msg")).toContainText("ZERO_RETENTION");

  await createPart(page, unique("E2E-POCKET"), "포켓 블록");
  await expect(page.getByTestId("zero-retention-note")).toBeVisible();
  // Base + Target dropped together: staged, completed and diffed before the worker may destroy them
  await page.getByTestId("upload-dropzone-input").setInputFiles([golden("02_pocket_offset", "B"), golden("02_pocket_offset", "A")]);
  await expect(page.getByTestId("diff-progress")).toHaveAttribute("data-diff-id", /^[0-9a-f-]{36}$/, { timeout: 60_000 });
  await expect(page.getByTestId("open-review")).toHaveAttribute("data-status", "READY", { timeout: 150_000 });
  await expect(revisionRow(page, "A")).toContainText("rev_A.step"); // file-name order -> Base = A
  await expect(revisionRow(page, "B")).toContainText("rev_B.step");

  // q-purge destroys both raw STEP files right after processing (derived viewer data stays)
  await expect
    .poll(
      async () => {
        await page.reload();
        await revisionRow(page, "B").waitFor();
        return page.locator("[data-testid=revision-row][data-raw=purged]").count();
      },
      { timeout: 90_000, intervals: [1000, 2000, 3000] },
    )
    .toBe(2);
  await expect(page.getByTestId("redrop-panel")).toBeVisible();

  // a new comparison needs the originals back
  await page.getByTestId("diff-base").selectOption({ label: "Rev A" });
  await page.getByTestId("diff-target").selectOption({ label: "Rev B" });
  await page.getByTestId("diff-create").click();
  await expect(page.getByTestId("part-panel").getByTestId("error")).toContainText("RAW_PURGED_REDROP_REQUIRED");

  // a different file: the browser-side SHA-256 check rejects it before anything is uploaded
  await page.getByTestId("redrop-A-input").setInputFiles(golden("01_hole_diam", "A"));
  await expect(page.getByTestId("redrop-msg-A")).toHaveAttribute("data-ok", "false");
  await expect(page.getByTestId("redrop-msg-A")).toContainText("SHA-256 불일치");

  // the genuine originals: hash-verified re-drop (the API re-hashes the stored object)
  for (const side of ["A", "B"] as const) {
    await page.getByTestId(`redrop-${side}-input`).setInputFiles(golden("02_pocket_offset", side));
    await expect(page.getByTestId(`redrop-msg-${side}`)).toHaveAttribute("data-ok", "true");
    await expect(revisionRow(page, side)).toHaveAttribute("data-raw", "kept");
  }
  const again = await runDiff(page, "A", "B");
  expect(again.status).toBe("READY");
  await page.getByTestId("open-review").click();
  await waitViewer(page);
  await expect(page.locator("[data-testid=change-item][data-category=PLANAR_OFFSET]")).toHaveCount(1);
});

test("shop inbox: anonymous v1/v2 drop → hash-verified submission → diff lands in the shop's workspace", async ({ page, browser }) => {
  const owner = email("owner");
  await login(page, owner);
  const slug = unique("e2e-shop").toLowerCase();
  await page.getByTestId("inbox-slug").fill(slug);
  await page.getByTestId("inbox-name").fill("OO정밀");
  await page.getByTestId("inbox-contact").fill(owner);
  await page.getByTestId("inbox-create").click();
  await expect(page.getByTestId("inbox-link")).toHaveAttribute("href", `/shop/${slug}`);

  const client = await newPage(browser);
  await client.goto(`/shop/${slug}`);
  await expect(client.getByTestId("shop-title")).toHaveText("OO정밀 전용 도면 접수함");
  const partNumber = unique("SHOP-BRK");
  await client.getByTestId("shop-sender").fill("로봇스타트업 김설계");
  await client.getByTestId("shop-contact").fill(email("client"));
  await client.getByTestId("shop-part-number").fill(partNumber);
  await client.getByTestId("shop-v1-input").setInputFiles(golden("01_hole_diam", "A"));
  await client.getByTestId("shop-v2-input").setInputFiles(golden("01_hole_diam", "B"));
  await client.getByTestId("shop-submit").click();
  const receipt = client.getByTestId("shop-receipt");
  await expect(receipt).toBeVisible({ timeout: 60_000 });
  const diffId = await receipt.getAttribute("data-diff-run-id");
  expect(diffId).toMatch(/^[0-9a-f-]{36}$/);
  await client.context().close();

  await page.goto("/workspace");
  const item = page.locator(`[data-testid=part-item][data-part-number="${partNumber}"]`);
  await expect(item).toContainText("접수함");
  await item.click();
  const row = page.locator(`[data-testid=diff-run-row][data-diff-id="${diffId}"]`);
  await expect(row).toHaveAttribute("data-status", "READY", { timeout: 150_000 });
  await row.getByRole("link", { name: "열기" }).click();
  await waitViewer(page);
  await expect(page.locator("[data-testid=change-item][data-category=HOLE_DIAMETER]")).toHaveCount(2);
});
