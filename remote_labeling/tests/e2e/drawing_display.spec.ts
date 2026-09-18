import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/** Use isolated samples so display tests cannot affect existing editing tests. */
async function openSample(page: Page, sample: string): Promise<void> {
  const { token } = JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN ?? "/tmp/realisr-e2e-token", "utf8"));
  await page.goto("/#token=" + token);
  await page.getByLabel("显示昵称").fill("绘图显示测试");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByLabel("数据集", { exact: true }).selectOption("z-display");
  await page.getByRole("button", { name: sample }).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
}

/** Inspect the rendered overlay, not component state or a visibility attribute. */
async function overlay(canvas: Locator): Promise<{ pixels: number; colors: number[][] }> {
  return canvas.locator("canvas").nth(2).evaluate(async node => {
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    const c = node as HTMLCanvasElement;
    const data = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
    let pixels = 0;
    const colors = new Map<string, number[]>();
    for (let i = 0; i < data.length; i += 4) if (data[i + 3]) {
      pixels++;
      if (data[i + 3] > 240) colors.set(`${data[i]},${data[i + 1]},${data[i + 2]}`, Array.from(data.slice(i, i + 3)));
    }
    return { pixels, colors: [...colors.values()] };
  });
}

async function crosshairVisible(canvas: Locator, visible: boolean): Promise<void> {
  if (visible) await expect.poll(async () => (await overlay(canvas)).pixels).toBeGreaterThan(100);
  else await expect.poll(async () => (await overlay(canvas)).pixels).toBe(0);
}

/** Set sliders through keyboard input to also cover shortcut isolation. */
async function width(page: Page, id: string, value: number): Promise<void> {
  const slider = page.locator(`#${id}`);
  await slider.press("Home");
  for (let step = .5; step < value; step += .5) await slider.press("ArrowRight");
  await expect(slider).toHaveValue(String(value));
  await expect(slider.locator("..").locator("output")).toHaveText(`${value} px`);
}

test("HR crosshair follows drawing tools, boundaries, cancellation and forced pan", async ({ page, browserName }, testInfo) => {
  await openSample(page, browserName === "chromium" ? "000000.png" : "000001.png");
  const canvas = page.getByTestId("canvas-HR");
  await page.getByRole("button", { name: "开始编辑" }).click();
  const box = (await canvas.boundingBox())!;
  const x = Math.round(box.x + box.width * .35), y = Math.round(box.y + box.height * .35);
  await page.mouse.move(x, y);
  await canvas.focus();
  await page.keyboard.press("r"); // Stationary pointer: visible before the first click.
  await crosshairVisible(canvas, true);
  for (const variant of ["LR2", "LR3", "LR4"]) await expect(page.getByTestId(`canvas-${variant}`).locator("canvas")).toHaveCount(2);
  await page.mouse.down();
  await page.mouse.move(x + 90, y + 70, { steps: 4 });
  await crosshairVisible(canvas, true);
  await page.mouse.up();
  await expect(page.locator(".region-list button")).toHaveCount(1);
  await crosshairVisible(canvas, true);
  await page.mouse.move(800, 30);
  await crosshairVisible(canvas, false);
  await page.mouse.move(x, y);
  await crosshairVisible(canvas, true);
  await page.keyboard.down("Space");
  await crosshairVisible(canvas, false);
  await page.mouse.down();
  await page.mouse.move(x + 20, y + 15);
  await page.keyboard.up("Space");
  await crosshairVisible(canvas, false); // Releasing Space first must not end a captured pan.
  await page.mouse.up();
  await crosshairVisible(canvas, true);
  await expect(page.locator(".region-list button")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await crosshairVisible(canvas, false);
  await page.keyboard.press("q");
  await crosshairVisible(canvas, true);
  for (const [dx, dy] of [[0, 0], [80, 0], [80, 60], [0, 60]]) {
    await page.mouse.click(x + dx, y + dy);
    await crosshairVisible(canvas, true);
  }
  await expect(page.locator(".region-list button")).toHaveCount(2);
  await page.mouse.click(x + 5, y + 5);
  await page.keyboard.press("Escape");
  await expect(page.locator(".region-list button")).toHaveCount(2);
  await crosshairVisible(canvas, false);
  await page.keyboard.press("r");
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await crosshairVisible(canvas, false);
  await page.mouse.move(x + 10, y + 10);
  await crosshairVisible(canvas, true);
  // Captured drawing outside the canvas keeps its geometry semantics but hides the crosshair.
  await page.mouse.down();
  await page.mouse.move(box.x - 5, box.y - 5);
  await crosshairVisible(canvas, false);
  await page.mouse.up();
  await canvas.locator("..").getByRole("button", { name: "适应", exact: true }).click();
  await page.mouse.move(Math.round(box.x + 1), Math.round(box.y + box.height / 2));
  // The vertical guide clamps to the left image edge, not the blank canvas margin.
  const scale = Math.min(box.width / 800, box.height / 600) * .94;
  const expectedX = box.width / 2 - 400 * scale;
  const actualX = await canvas.locator("canvas").nth(2).evaluate(async node => {
    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
    const c = node as HTMLCanvasElement, ratio = c.width / c.getBoundingClientRect().width;
    const data = c.getContext("2d")!.getImageData(0, 0, c.width, Math.round(30 * ratio)).data;
    const columns = new Array(c.width).fill(0);
    for (let i = 0; i < data.length; i += 4) columns[(i / 4) % c.width] += data[i + 3];
    return columns.indexOf(Math.max(...columns)) / ratio;
  });
  expect(Math.abs(actualX - expectedX)).toBeLessThanOrEqual(1);
  await page.locator(".drawing-display").scrollIntoViewIfNeeded();
  await page.mouse.move(x, y);
  await page.screenshot({ path: testInfo.outputPath("drawing-display-default.png") });
  await page.getByRole("button", { name: "结束编辑" }).click();
  await page.mouse.move(x, y);
  await crosshairVisible(canvas, false);
});

test("live display settings preserve geometry, scale, history and browser preferences", async ({ page, browserName }, testInfo) => {
  test.setTimeout(60000);
  const sample = browserName === "chromium" ? "000002.png" : "000003.png";
  await openSample(page, sample);
  await expect(page.locator("#regionWidth")).toHaveValue("1.5");
  await expect(page.locator("#crosshairWidth")).toHaveValue("1");
  await page.getByRole("button", { name: "开始编辑" }).click();
  await page.getByRole("button", { name: "矩形 R", exact: true }).click();
  const canvas = page.getByTestId("canvas-HR"), box = (await canvas.boundingBox())!;
  const x = Math.round(box.x + box.width * .3), y = Math.round(box.y + box.height * .3);
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + 100, y + 70); await page.mouse.up();
  await expect(page.locator(".save-state")).toHaveText("草稿已自动保存");
  const path = `/api/v1/datasets/z-display/samples/${sample}`;
  const before = (await (await page.request.get(path)).json()).draft;
  let writes = 0;
  page.on("request", request => {
    if (request.method() !== "GET" && /\/samples\/.*\/(draft|save-annotations)/.test(request.url())) writes++;
  });
  await width(page, "regionWidth", 6);
  await width(page, "crosshairWidth", 3);
  await page.getByRole("combobox", { name: "十字线样式", exact: true }).selectOption("solid");
  await page.getByLabel("十字线颜色", { exact: true }).fill("#ff0000");
  await page.mouse.move(x + 40, y + 30);
  await crosshairVisible(canvas, true);
  expect((await overlay(canvas)).colors).toContainEqual([255, 0, 0]);
  // Integrate alpha across a guide away from its intersection: CSS-pixel thickness stays fixed on zoom.
  const guideWidth = async (): Promise<number> => canvas.locator("canvas").nth(2).evaluate(async node => {
    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
    const c = node as HTMLCanvasElement, ratio = c.width / c.getBoundingClientRect().width;
    const data = c.getContext("2d")!.getImageData(0, Math.round(10 * ratio), c.width, 1).data;
    let alpha = 0; for (let i = 3; i < data.length; i += 4) alpha += data[i] / 255;
    return alpha / ratio;
  });
  expect(await guideWidth()).toBeCloseTo(3, 1);
  await page.mouse.wheel(0, -400);
  expect(await guideWidth()).toBeCloseTo(3, 1);
  await page.getByRole("combobox", { name: "十字线样式", exact: true }).selectOption("dotted");
  await page.mouse.move(x + 40, y + 30);
  const dotted = (await overlay(canvas)).pixels;
  await page.getByRole("combobox", { name: "十字线样式", exact: true }).selectOption("solid");
  await page.mouse.move(x + 40, y + 30);
  expect((await overlay(canvas)).pixels).toBeGreaterThan(dotted);
  await page.keyboard.press("Escape");
  // Each pane renders the same selected-border thickness despite differing image scales.
  await page.locator(".region-list button").first().click();
  await canvas.locator("..").getByRole("button", { name: "适应", exact: true }).click();
  for (const variant of ["HR", "LR2", "LR3", "LR4"]) {
    const pane = page.getByTestId(`canvas-${variant}`);
    const top = before[variant][0].points[0][1];
    const [left, right] = [before[variant][0].points[0][0], before[variant][0].points[1][0]];
    const factor = variant === "HR" ? 1 : Number(variant.slice(2));
    const thick = await pane.locator("canvas").nth(1).evaluate(async (node, data) => {
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
      const c = node as HTMLCanvasElement, box = c.getBoundingClientRect(), ratio = c.width / box.width;
      const scale = Math.min(box.width / 800, box.height / 600) * .94 * data.factor;
      const px = box.width / 2 + ((data.left + data.right) / 2 - Math.floor(800 / data.factor) / 2) * scale;
      const py = box.height / 2 + (data.top - Math.floor(600 / data.factor) / 2) * scale;
      const pixels = c.getContext("2d")!.getImageData(Math.round(px * ratio), Math.round((py - 8) * ratio), 1, Math.round(16 * ratio)).data;
      let count = 0; for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 128) count++;
      return count / ratio;
    }, { left, right, top, factor });
    expect(Math.abs(thick - 7)).toBeLessThanOrEqual(1);
  }
  expect((await (await page.request.get(path)).json()).draft).toEqual(before);
  expect(writes).toBe(0);
  await page.getByRole("separator", { name: "调整右侧栏宽度" }).press("Home");
  await page.locator(".drawing-display").scrollIntoViewIfNeeded();
  expect(await page.locator(".inspector").evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("drawing-display-narrow.png") });
  await canvas.locator("..").getByRole("button", { name: "放大视图", exact: true }).click();
  await page.keyboard.press("r");
  const single = (await canvas.boundingBox())!;
  await page.mouse.move(single.x + single.width / 2, single.y + single.height / 2);
  await crosshairVisible(canvas, true);
  await page.screenshot({ path: testInfo.outputPath("drawing-display-single.png") });
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".region-list button")).toHaveCount(0);
  await page.getByRole("button", { name: "结束编辑" }).click();
  await page.getByRole("button", { name: "000004.png" }).click();
  await expect(page.locator("#regionWidth")).toHaveValue("6");
  await page.reload();
  await expect(page.locator("#regionWidth")).toHaveValue("6");
  await expect(page.locator("#crosshairWidth")).toHaveValue("3");
  await expect(page.getByRole("combobox", { name: "十字线样式", exact: true })).toHaveValue("solid");
  await expect(page.getByLabel("十字线颜色", { exact: true })).toHaveValue("#ff0000");
});

test("view-only visitors can customize display even when browser storage is denied", async ({ page, browser }) => {
  await openSample(page, "000005.png");
  const response = await page.request.post("/api/v1/shares", {
    headers: { Origin: new URL(page.url()).origin },
    data: { dataset: "z-display", sample: "000005.png", role: "view", expires: null },
  });
  expect(response.ok()).toBeTruthy();
  const share = await response.json();
  const context = await browser.newContext();
  try {
    await context.addInitScript(() => {
      Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new Error("storage denied"); } });
    });
    const visitor = await context.newPage();
    await visitor.goto(share.url);
    await visitor.getByRole("button", { name: "进入工作台" }).click();
    await expect(visitor.locator(".topbar")).toContainText("可查看");
    await expect(visitor.getByTestId("canvas-HR")).toBeVisible();
    await expect(visitor.getByRole("button", { name: "矩形 R", exact: true })).toBeDisabled();
    await width(visitor, "regionWidth", 3);
    await width(visitor, "crosshairWidth", 2);
    await visitor.getByRole("combobox", { name: "十字线样式", exact: true }).selectOption("dotted");
    await visitor.getByLabel("十字线颜色", { exact: true }).fill("#112233");
    await visitor.reload();
    await expect(visitor.locator("#regionWidth")).toHaveValue("1.5");
    await expect(visitor.locator("#crosshairWidth")).toHaveValue("1");
  } finally {
    await context.close();
    await page.request.delete(`/api/v1/shares/${share.id}`, { headers: { Origin: new URL(page.url()).origin } });
  }
});
