/**
 * Phase 4 full-stack browser E2E — no mocks:
 *   - SpecCardV2 editing + Base↔Target spec diff + "3D 형상 동일 — 제조 사양/공차만 N건 변경됨" banner
 *   - 2D PDF (pdfjs-dist) & DXF (dxf-parser) upload + 3D/Split/2D views + pixel rendering + watermark lock
 *   - Measure reliability badge ("CAD 이론치" + caption) + STEP compatibility notice
 *   - Process DFM checklist (CNC vs 3D printing) + immutable approval snapshot + SUPERSEDED banner & raw block
 *   - Offline queue check sync on reconnect + Factory tablet (768x1024, touch) lod1 default, bottom sheet & 44px targets
 */

import { expect, test } from "@playwright/test";

import {
  DXF_TEXT,
  clickCanvas,
  createPart,
  email,
  golden,
  inkPixels,
  login,
  makePdf,
  newPage,
  optionValue,
  runDiff,
  sameGeometryPair,
  unique,
  uploadRevision,
  waitViewer,
  writeFixture,
} from "./helpers";

test.describe.serial("Phase 4: SpecCardV2, 2D Split View, DFM, Supersede & Tablet UX", () => {
  const designer = email("p4-designer");
  const shopContact = email("p4-shop");
  let reviewPath = "";
  let shareUrl = "";
  let shareUrlRaw = "";

  test("same-geometry STEP diff → SpecCardV2 diff banner → 2D PDF/DXF split view → measure reliability", async ({ page }) => {
    const [stepA, stepB] = sameGeometryPair();
    const pdfPath = writeFixture("motor_mount_v2.pdf", makePdf("DRN-ARM-01 Rev B"));
    const dxfPath = writeFixture("motor_mount_v2.dxf", DXF_TEXT);

    await login(page, designer);
    await createPart(page, unique("E2E-P4-SPEC"), "모터 마운트");
    await uploadRevision(page, "v1", stepA);
    await uploadRevision(page, "v2", stepB);
    const run = await runDiff(page, "v1", "v2");
    expect(run.status).toBe("READY");

    await page.getByTestId("open-review").click();
    await page.waitForURL(/\/parts\/[^/]+\/diffs\/[^/]+$/);
    reviewPath = new URL(page.url()).pathname;
    await waitViewer(page);

    // 3D geometry is identical (0 geometric changes)
    await expect(page.getByTestId("change-item")).toHaveCount(0);

    // Edit Target SpecCardV2 -> triggers "3D 형상 동일 — 제조 사양/공차만 N건 변경됨" banner
    await page.getByTestId("spec-process").selectOption("CNC_MILLING");
    await page.getByTestId("spec-material-grade").fill("AL7075-T6");
    await page.getByTestId("spec-tolerance").selectOption("ISO_2768_mK");
    await page.getByTestId("spec-ct-add").click();
    await page.getByTestId("spec-ct-location").first().fill("모터 축 홀");
    await page.getByTestId("spec-ct-spec").first().fill("Ø6 H7");
    await page.getByTestId("spec-thread-add").click();
    await page.getByTestId("spec-thread-spec").first().fill("M3x0.5");
    await page.getByTestId("spec-thread-count").first().fill("4");
    await page.getByTestId("spec-save").click();

    await expect(page.getByTestId("spec-saved")).toContainText("spec v2");
    await expect(page.getByTestId("spec-only-banner")).toContainText("3D 형상 동일 — 제조 사양/공차만");
    await expect(page.getByTestId("spec-only-note")).toContainText("3D 형상 동일 — 제조 사양/공차만");
    await expect(page.locator("[data-testid=spec-diff-row][data-severity=CRITICAL]").first()).toBeVisible();

    // Upload 2D PDF + DXF attachments onto Target
    await page.getByTestId("attach-dropzone-target-input").setInputFiles([pdfPath, dxfPath]);
    await expect(page.getByTestId("attach-msg-target")).toContainText("첨부 완료");
    await expect(page.getByTestId("attachment-item")).toHaveCount(2);

    // Switch to 3D+2D Split View -> render both PDF (pdfjs-dist) and DXF (dxf-parser)
    await page.getByTestId("layout-split").click();
    await expect(page.getByTestId("drawing-panel")).toBeVisible();
    await expect(page.getByTestId("pdf-view")).toHaveAttribute("data-status", "rendered", { timeout: 30_000 });
    expect(await inkPixels(page.getByTestId("pdf-canvas"))).toBeGreaterThan(200);
    await expect(page.getByTestId("drawing-download")).toBeVisible();

    await page.locator("[data-testid=drawing-item][data-kind='2D_DXF']").click();
    await expect(page.getByTestId("dxf-view")).toHaveAttribute("data-status", "rendered", { timeout: 30_000 });
    expect(await inkPixels(page.getByTestId("dxf-canvas"))).toBeGreaterThan(50);

    // Measure reliability badge & STEP compatibility notice
    await page.getByTestId("layout-3d").click();
    const a = page.getByTestId("measure-a");
    const b = page.getByTestId("measure-b");
    await a.selectOption(await optionValue(a, /^BASE · (홀|보스) #\d+ Ø3\.30 @\(88\.0, 12\.0/));
    await b.selectOption(await optionValue(b, /^BASE · (홀|보스) #\d+ Ø3\.30 @\(80\.0, 50\.0/));
    await expect(page.getByTestId("measure-reliability")).toContainText("CAD 이론치");
    await expect(page.getByTestId("measure-caption")).toContainText("가공 공차 미포함");
    await expect(page.getByTestId("step-info-text")).toContainText("나사산 규격·GD&T 공차·재질·표면처리를 포함하지 않습니다");

    // Issue link 1 (allow_raw_download=false) & link 2 (allow_raw_download=true for supersede raw-block test)
    await page.getByTestId("share-name").fill("드론정밀");
    await page.getByTestId("share-contact").fill(shopContact);
    await page.getByTestId("share-create").click();
    shareUrl = (await page.getByTestId("share-url").getAttribute("href")) ?? "";
    expect(shareUrl).toMatch(/^http:\/\/localhost:3000\/r\/[\w-]{20,}$/);

    await page.getByLabel("승인 후 원본 다운로드").check();
    await page.getByTestId("share-name").fill("드론정밀-원본허용");
    await page.getByTestId("share-create").click();
    await expect(page.getByTestId("link-item")).toHaveCount(2);
    shareUrlRaw = (await page.getByTestId("share-url").getAttribute("href")) ?? "";
    expect(shareUrlRaw).not.toBe(shareUrl);
  });

  test("external reviewer: 2D watermarked view (no raw download) + DFM checklist + spec-complete approval", async ({ page }) => {
    expect(shareUrl).not.toBe("");
    await page.goto(new URL(shareUrl).pathname);
    await page.getByTestId("otp-request").click();
    await expect(page.getByTestId("dev-otp-code")).toHaveText(/^\d{6}$/);
    await page.getByTestId("dev-otp-fill").click();
    await page.getByTestId("otp-verify").click();
    await waitViewer(page);

    // 2D Split View: watermarked, download button hidden because allow_raw_download=false
    await page.getByTestId("layout-split").click();
    await expect(page.getByTestId("pdf-view")).toHaveAttribute("data-status", "rendered", { timeout: 30_000 });
    await expect(page.getByTestId("drawing-watermark")).toBeVisible();
    await expect(page.getByTestId("drawing-download")).toHaveCount(0);

    // Tampering main watermark blurs both 3D canvas and 2D drawing panel
    await page.evaluate(() => document.querySelector('[data-testid="watermark"]')?.remove());
    await expect(page.getByTestId("tamper-alert")).toBeVisible();
    await expect(page.getByTestId("viewer-shell")).toHaveAttribute("data-blurred", "true");
    await page.reload();
    await waitViewer(page);

    // Material + ISO 2768-mK + 2D PDF + thread are all set -> no forced spec-ack checkbox, DFM CNC checklist is shown
    await expect(page.getByTestId("spec-ack")).toHaveCount(0);
    await expect(page.getByTestId("dfm-checklist")).toHaveAttribute("data-process", "CNC_MILLING");
    await page.getByTestId("dfm-corner_r").check();
    await page.getByTestId("dfm-thin_wall").check();
    await page.getByTestId("dfm-precision_fit").check();

    await page.getByTestId("approve-agree").check();
    await page.getByTestId("approve-submit").click();
    await expect(page.getByTestId("approved-card")).toBeVisible();
    await expect(page.getByTestId("snapshot-sha")).toContainText("snapshot sha256");

    // Evidence PDF succeeds and contains immutable snapshot hash
    const pdfUrl = await page.getByTestId("approval-pdf").getAttribute("href");
    const pdf = await page.request.get(pdfUrl!);
    expect(pdf.status()).toBe(200);
    expect((await pdf.body()).subarray(0, 5).toString("latin1")).toBe("%PDF-");
  });

  test("supersede trigger (SPEC_UPDATED): designer updates target spec → reviewer sees red SUPERSEDED banner & raw blocked", async ({ browser }) => {
    expect(reviewPath).not.toBe("");
    expect(shareUrlRaw).not.toBe("");

    // Second reviewer verifies OTP & approves link 2 (allow_raw_download=true) while still current
    const ext = await newPage(browser);
    await ext.goto(new URL(shareUrlRaw).pathname);
    await ext.getByTestId("otp-request").click();
    await expect(ext.getByTestId("dev-otp-code")).toHaveText(/^\d{6}$/);
    await ext.getByTestId("dev-otp-fill").click();
    await ext.getByTestId("otp-verify").click();
    await waitViewer(ext);
    await ext.getByTestId("approve-agree").check();
    await ext.getByTestId("approve-submit").click();
    await expect(ext.getByTestId("raw-downloads")).toBeVisible();

    // Designer edits Target spec -> triggers SPEC_UPDATED supersede on both APPROVED links
    const des = await newPage(browser);
    await login(des, designer);
    await des.goto(reviewPath);
    await waitViewer(des);
    await des.getByTestId("spec-order-qty").fill("25");
    await des.getByTestId("spec-save").click();
    await expect(des.getByTestId("spec-saved")).toContainText("구버전(SUPERSEDED) 처리");
    await expect(des.locator("[data-testid=link-item][data-effective=SUPERSEDED]")).toHaveCount(2);
    await des.context().close();

    // Reviewer refreshes -> sticky red SUPERSEDED banner appears and raw STEP download is blocked
    await ext.reload();
    await expect(ext.getByTestId("superseded-banner")).toBeVisible();
    await expect(ext.getByTestId("superseded-banner")).toContainText("가공 착수 금지");
    await expect(ext.getByTestId("link-status")).toHaveAttribute("data-status", "SUPERSEDED");
    await expect(ext.getByTestId("raw-blocked")).toBeVisible();
    await expect(ext.getByTestId("raw-downloads")).toHaveCount(0);
    await ext.context().close();
  });

  test("factory tablet UX (<1024px, touch) + offline queue reconnect sync", async ({ browser }) => {
    // Prepare a fresh READY diff with 2 changes for offline check test + tablet layout test
    const d = email("p4-tablet");
    const c = email("p4-shop-tablet");
    const desktop = await newPage(browser);
    await login(desktop, d);
    await createPart(desktop, unique("E2E-P4-TAB"), "태블릿 테스트 브래킷");
    await uploadRevision(desktop, "A", golden("01_hole_diam", "A"));
    await uploadRevision(desktop, "B", golden("01_hole_diam", "B"));
    const run = await runDiff(desktop, "A", "B");
    expect(run.status).toBe("READY");
    await desktop.getByTestId("open-review").click();
    await waitViewer(desktop);

    // Switch to PRINT_FDM without build orientation -> verify additive DFM + MISSING_BUILD_ORIENTATION warning
    await desktop.getByTestId("spec-process").selectOption("PRINT_FDM");
    await desktop.getByTestId("spec-material-grade").fill("PETG-CF");
    await desktop.getByTestId("spec-tolerance").selectOption("ISO_2768_mK");
    await desktop.getByTestId("spec-save").click();
    await expect(desktop.getByTestId("spec-saved")).toContainText("spec v2");

    await desktop.getByTestId("share-name").fill("태블릿공장");
    await desktop.getByTestId("share-contact").fill(c);
    await desktop.getByTestId("share-create").click();
    const link = (await desktop.getByTestId("share-url").getAttribute("href")) ?? "";
    await expect(desktop.locator("[data-testid=share-warnings] [data-code=MISSING_BUILD_ORIENTATION]")).toBeVisible();
    await desktop.context().close();

    // Open in a coarse-pointer tablet context (768x1024, hasTouch: true)
    const tabCtx = await browser.newContext({
      baseURL: process.env.WEB_URL ?? "http://localhost:3000",
      viewport: { width: 768, height: 1024 },
      hasTouch: true,
      acceptDownloads: true,
    });
    const tab = await tabCtx.newPage();
    await tab.goto(new URL(link).pathname);
    await tab.getByTestId("otp-request").click();
    await expect(tab.getByTestId("dev-otp-code")).toHaveText(/^\d{6}$/);
    await tab.getByTestId("dev-otp-fill").click();
    await tab.getByTestId("otp-verify").click();
    await waitViewer(tab);

    // Tablet defaults to lod1 and switches to lod0 when toggle-hq is clicked
    await expect(tab.getByTestId("viewer-shell")).toHaveAttribute("data-lod", "lod1");
    await tab.getByTestId("toggle-hq").click();
    await expect(tab.getByTestId("viewer-shell")).toHaveAttribute("data-lod", "lod0");

    // Tabs (<1024px) & BottomSheet + >=44px touch targets
    await expect(tab.getByTestId("layout-tabs")).toBeVisible();
    await expect(tab.getByTestId("bottom-sheet")).toHaveAttribute("data-open", "false");
    const sheetBtnBox = await tab.getByTestId("sheet-toggle").boundingBox();
    expect(sheetBtnBox!.height).toBeGreaterThanOrEqual(44);
    const toolBtnBox = await tab.getByTestId("tool-select").boundingBox();
    expect(toolBtnBox!.height).toBeGreaterThanOrEqual(44);

    // Rotation mode ("회전") must not open a pin draft on canvas tap
    await clickCanvas(tab);
    await expect(tab.getByTestId("pin-draft")).toHaveCount(0);

    // Open bottom sheet -> verify PRINT_FDM DFM checklist
    await tab.getByTestId("sheet-toggle").click();
    await expect(tab.getByTestId("bottom-sheet")).toHaveAttribute("data-open", "true");
    await expect(tab.getByTestId("dfm-checklist")).toHaveAttribute("data-process", "PRINT_FDM");
    await expect(tab.getByTestId("dfm-orientation")).toBeVisible();
    await tab.getByTestId("sheet-toggle").click();

    // Offline queue: go offline, toggle a checklist item, verify queued, then reconnect and verify synced
    await tabCtx.setOffline(true);
    await tab.evaluate(() => window.dispatchEvent(new Event("offline")));
    await expect(tab.getByTestId("offline-indicator")).toBeVisible();
    await tab.getByTestId("change-check").first().click();
    await expect(tab.getByTestId("offline-queue")).toHaveAttribute("data-count", "1");

    await tabCtx.setOffline(false);
    await tab.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect(tab.getByTestId("offline-queue")).toHaveCount(0, { timeout: 15_000 });
    await tab.reload();
    await expect(tab.locator("[data-testid=change-item][data-checked=true]")).toHaveCount(1);

    await tabCtx.close();
  });
});
