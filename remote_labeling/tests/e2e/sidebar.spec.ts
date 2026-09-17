import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const storageKey = "realisr.sidebar-layout.v1";

/** Open existing dense annotations without modifying the shared fixture. */
async function openWorkspace(page: Page, dataset = "text", sample = "000010.png"): Promise<void> {
  const { token } = JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN ?? "/tmp/realisr-e2e-token", "utf8"));
  await page.goto("/#token=" + token);
  await page.getByLabel("显示昵称").fill("侧栏测试");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByLabel("数据集", { exact: true }).selectOption(dataset);
  await page.getByRole("button", { name: sample }).click();
  await expect(page.locator(".sample-toolbar strong")).toHaveText(sample);
  await expect(page.locator(".loading-image")).toHaveCount(0);
}

async function width(locator: Locator): Promise<number> {
  return (await locator.boundingBox())!.width;
}

async function drag(page: Page, side: "left" | "right", distance: number): Promise<void> {
  const handle = page.locator(`.sidebar-${side} .sidebar-handle`);
  const box = (await handle.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + 100);
  await expect(handle).toHaveCSS("cursor", "col-resize");
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + distance, box.y + 130, { steps: 8 });
  await page.mouse.up();
  await expect(page.locator("body")).not.toHaveClass(/sidebar-resizing/);
}

async function leaveSidebar(page: Page): Promise<void> {
  await page.mouse.move(800, 30);
}

test("independent drag widths, limits, responsive restoration and persistence", async ({ page }, testInfo) => {
  await openWorkspace(page);
  const left = page.locator("#sidebar-left-panel"), right = page.locator("#sidebar-right-panel");
  let annotationWrites = 0;
  page.on("request", request => {
    if (request.method() !== "GET" && /\/samples\/.*\/(draft|save-annotations)/.test(request.url())) annotationWrites++;
  });
  await drag(page, "left", 80);
  expect(await width(left)).toBeCloseTo(276, 0);
  expect(await width(right)).toBeCloseTo(270, 0);
  await drag(page, "right", -90);
  expect(await width(right)).toBeCloseTo(360, 0);
  const leftHandle = page.getByRole("separator", { name: "调整左侧栏宽度" });
  const rightHandle = page.getByRole("separator", { name: "调整右侧栏宽度" });
  await leftHandle.press("End");
  await rightHandle.press("End");
  expect(await width(left)).toBe(420);
  expect(await width(right)).toBe(480);
  await page.setViewportSize({ width: 1100, height: 720 });
  expect(await width(page.locator(".editor"))).toBeGreaterThanOrEqual(399);
  expect(await width(left)).toBeLessThan(420);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1100);
  await page.screenshot({ path: testInfo.outputPath("sidebars-narrow.png") });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await expect.poll(() => width(left)).toBe(420);
  await expect.poll(() => width(right)).toBe(480);
  await page.setViewportSize({ width: 1100, height: 720 });
  await expect.poll(() => width(left)).toBeLessThan(420);
  const constrainedLeft = await width(left), constrainedRight = await width(right);
  await drag(page, "left", -20);
  expect(await width(left)).toBeCloseTo(constrainedLeft - 20, 0);
  expect(await width(right)).toBeCloseTo(constrainedRight, 0);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await expect.poll(() => width(right)).toBe(480);
  expect(await width(left)).toBeCloseTo(constrainedLeft - 20, 0);
  await leftHandle.press("Home");
  await rightHandle.press("Home");
  expect(await width(left)).toBe(170);
  expect(await width(right)).toBe(235);
  await page.reload();
  await expect(left).toBeVisible();
  expect(await width(left)).toBe(170);
  expect(await width(right)).toBe(235);
  expect(annotationWrites).toBe(0);
  await expect(page.getByTestId("canvas-HR")).toBeVisible();
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("sidebars-docked.png") });
  await page.getByLabel("数据集", { exact: true }).selectOption("face");
  expect(await width(left)).toBe(170);
  expect(await width(right)).toBe(235);
});

test("auto-hide rails, overlay geometry, focus protection and mounted panel state", async ({ page }, testInfo) => {
  await openWorkspace(page);
  await page.getByLabel("设备", { exact: true }).selectOption("cpu");
  await page.locator(".region-list button").first().click();
  await page.getByRole("button", { name: "隐藏右侧栏" }).click();
  const right = page.locator("#sidebar-right-panel");
  await expect(right).not.toBeVisible();
  await page.getByRole("button", { name: "隐藏左侧栏" }).click();
  await leaveSidebar(page);
  const left = page.locator("#sidebar-left-panel");
  await expect(left).not.toBeVisible();
  expect(await width(page.locator(".sidebar-left"))).toBe(16);
  expect(await width(page.locator(".sidebar-right"))).toBe(16);
  await expect(page.locator(".sidebar-left .sidebar-rail")).toHaveText("▶");
  await expect(page.locator(".sidebar-right .sidebar-rail")).toHaveText("◀");
  await expect(left).toHaveAttribute("inert", "");
  await page.screenshot({ path: testInfo.outputPath("sidebars-hidden.png") });
  const before = await page.getByTestId("canvas-HR").boundingBox();
  await page.locator(".sidebar-left .sidebar-rail").hover();
  await expect(left).toBeVisible();
  await page.getByLabel("搜索文件名").hover();
  await page.waitForTimeout(400);
  await expect(left).toBeVisible();
  expect(await page.getByTestId("canvas-HR").boundingBox()).toEqual(before);
  await leaveSidebar(page);
  await expect(left).not.toBeVisible();
  await page.locator(".sidebar-right .sidebar-rail").hover();
  await expect(right).toBeVisible();
  await page.getByLabel("OCR 真值").click();
  await leaveSidebar(page);
  await page.waitForTimeout(450);
  await expect(right).toBeVisible();
  // Blurring the field resumes auto-hide; ordinary mouse button focus must not pin it.
  await page.locator(".topbar .brand").click();
  await expect(right).not.toBeVisible();
  await page.locator(".sidebar-right .sidebar-rail").hover();
  await expect(right).toBeVisible();
  await expect(page.getByLabel("设备", { exact: true })).toHaveValue("cpu");
  await page.getByLabel("设备", { exact: true }).focus();
  await leaveSidebar(page);
  await page.waitForTimeout(450);
  await expect(right).toBeVisible();
  await page.locator(".topbar .brand").click();
  await expect(right).not.toBeVisible();
  await page.locator(".sidebar-right .sidebar-rail").hover();
  await expect(right).toBeVisible();
  await drag(page, "right", -50);
  expect(await page.getByTestId("canvas-HR").boundingBox()).toEqual(before);
  await page.screenshot({ path: testInfo.outputPath("sidebar-overlay.png") });
  await right.getByRole("button", { name: "固定右侧栏" }).click();
  await leaveSidebar(page);
  await expect(page.getByRole("button", { name: "隐藏右侧栏" })).toBeVisible();
  await page.reload();
  await expect(left).not.toBeVisible();
  await expect(right).toBeVisible();
  expect(await width(right)).toBe(320);
  // A rail can be pinned using the keyboard even without hover.
  await page.locator(".sidebar-left .sidebar-rail").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "隐藏左侧栏" })).toBeVisible();
});

test("right clicks, interrupted drags and canvas gestures do not leave stale interactions", async ({ page }) => {
  await openWorkspace(page);
  const handle = page.locator(".sidebar-left .sidebar-handle");
  const box = (await handle.boundingBox())!;
  await page.mouse.move(box.x + 3, box.y + 100);
  await page.mouse.down({ button: "right" });
  await page.mouse.move(box.x + 50, box.y + 100);
  await page.mouse.up({ button: "right" });
  expect(await width(page.locator("#sidebar-left-panel"))).toBe(196);
  await page.keyboard.press("Escape");
  await page.mouse.move(box.x + 3, box.y + 100);
  await page.mouse.down();
  await page.mouse.move(box.x + 45, box.y + 100);
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await expect(page.locator("body")).not.toHaveClass(/sidebar-resizing/);
  await page.mouse.up();
  const current = (await handle.boundingBox())!;
  await page.mouse.move(current.x + 3, current.y + 100);
  await page.mouse.down();
  await page.mouse.move(current.x + 20, current.y + 100);
  await handle.dispatchEvent("pointercancel", { pointerId: 1, bubbles: true });
  await expect(page.locator("body")).not.toHaveClass(/sidebar-resizing/);
  await page.mouse.up();
  await page.getByRole("button", { name: "隐藏左侧栏" }).click();
  await leaveSidebar(page);
  const canvas = (await page.getByTestId("canvas-HR").boundingBox())!;
  await page.mouse.move(canvas.x + 10, canvas.y + 10);
  await page.mouse.down();
  await page.mouse.move(8, canvas.y + 10, { steps: 5 });
  await page.waitForTimeout(250);
  await expect(page.locator("#sidebar-left-panel")).not.toBeVisible();
  await page.mouse.move(800, 30);
  await page.mouse.up();
  await page.locator(".sidebar-left .sidebar-rail").hover();
  await expect(page.locator("#sidebar-left-panel")).toBeVisible();
  // Mouse focus on a sample button does not protect the popup from auto-hide.
  await page.getByRole("button", { name: "000010.png" }).click();
  await leaveSidebar(page);
  await expect(page.locator("#sidebar-left-panel")).not.toBeVisible();
});

test("broken or disabled browser storage falls back to usable sidebars", async ({ page }) => {
  await page.addInitScript(key => localStorage.setItem(key, "{invalid"), storageKey);
  await openWorkspace(page);
  await expect(page.getByRole("button", { name: "隐藏左侧栏" })).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new Error("storage denied"); } });
  });
  await drag(page, "left", 40);
  expect(await width(page.locator("#sidebar-left-panel"))).toBe(236);
  await page.getByRole("button", { name: "隐藏左侧栏" }).click();
  await expect(page.locator("#sidebar-left-panel")).not.toBeVisible();
});

test("drawing after sidebar layout changes retains image coordinates and undo history", async ({ page, browserName }) => {
  const sample = browserName === "chromium" ? "000000.png" : "000001.png";
  await openWorkspace(page, "sidebar", sample);
  const samplePath = "/api/v1/datasets/sidebar/samples/" + sample;
  const original = (await (await page.request.get(samplePath)).json()).draft;
  await drag(page, "left", 60);
  await drag(page, "right", -60);
  await page.getByRole("button", { name: "隐藏左侧栏" }).click();
  await leaveSidebar(page);
  await page.getByRole("button", { name: "开始编辑" }).click();
  await page.getByRole("button", { name: "矩形 R", exact: true }).click();
  const box = (await page.getByTestId("canvas-HR").boundingBox())!;
  const x1 = box.x + box.width * .3, x2 = box.x + box.width * .6;
  const y1 = box.y + box.height * .3, y2 = box.y + box.height * .6;
  await page.mouse.move(x1, y1);
  await page.mouse.down();
  await page.mouse.move(x2, y2, { steps: 5 });
  await page.mouse.up();
  await expect(page.locator(".region-list button")).toHaveCount(original.HR.length + 1);
  await expect(page.locator(".save-state")).toHaveText("草稿已自动保存");
  const edited = (await (await page.request.get(samplePath)).json()).draft;
  const points = edited.HR.at(-1).points;
  const scale = Math.min(box.width / 800, box.height / 600) * .94;
  const expected = [[(x1 - box.x - box.width / 2) / scale + 400, (y1 - box.y - box.height / 2) / scale + 300],
    [(x2 - box.x - box.width / 2) / scale + 400, (y2 - box.y - box.height / 2) / scale + 300]];
  for (let i = 0; i < 2; i++) for (let axis = 0; axis < 2; axis++) {
    expect(Math.abs(points[i * 2][axis] - expected[i][axis]) * scale).toBeLessThanOrEqual(1);
  }
  // More layout changes must not add undo entries or modify any geometry.
  await page.getByRole("button", { name: "隐藏右侧栏" }).click();
  await leaveSidebar(page);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".save-state")).toHaveText("草稿已自动保存");
  expect((await (await page.request.get(samplePath)).json()).draft).toEqual(original);
  await page.getByRole("button", { name: "结束编辑" }).click();
});
