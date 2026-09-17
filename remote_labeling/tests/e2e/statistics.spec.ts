import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const headers = { Origin: "http://127.0.0.1:8877" };

async function login(page: Page, dataset = "text"): Promise<void> {
  const { token } = JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN ?? "/tmp/realisr-e2e-token", "utf8"));
  await page.goto(`/?dataset=${dataset}#token=${token}`);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByTestId("statistics-groups")).toBeVisible();
}

const panel = (page: Page) => page.getByRole("region", { name: "数据集统计", exact: true });

test("compact overview, details, search-independent scope and narrow sidebar", async ({ page }, info) => {
  await login(page);
  const dashboard = panel(page);
  await expect(page.getByTestId("statistics-groups")).toHaveText("12");
  await expect(page.getByTestId("statistics-regions")).toHaveText("357");
  await expect(dashboard.locator("details")).not.toHaveAttribute("open");
  expect((await dashboard.boundingBox())!.height).toBeLessThanOrEqual(300);
  const properties = page.getByRole("heading", { name: "区域属性" });
  expect((await properties.boundingBox())!.y).toBeGreaterThan((await dashboard.boundingBox())!.y);
  await page.screenshot({ path: info.outputPath("statistics-default.png") });
  await page.getByLabel("搜索文件名").fill("not-found");
  await expect(page.locator(".sample-list button")).toHaveCount(0);
  await expect(page.getByTestId("statistics-groups")).toHaveText("12");
  await dashboard.locator("summary").click();
  await expect(dashboard.locator(".statistics-variant")).toHaveCount(4);
  await expect(dashboard.locator(".statistics-variant").first()).toContainText("已赋值 357 / 357");
  await page.screenshot({ path: info.outputPath("statistics-expanded.png") });
  await page.getByRole("separator", { name: "调整右侧栏宽度" }).press("Home");
  await page.setViewportSize({ width: 1100, height: 720 });
  expect(await dashboard.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("statistics-narrow.png") });
  await page.getByRole("button", { name: "隐藏右侧栏" }).click();
  await page.mouse.move(800, 30);
  await expect(dashboard).not.toBeVisible();
  await page.locator(".sidebar-right .sidebar-rail").hover();
  await expect(dashboard).toBeVisible();
  await expect(dashboard.locator("details")).toHaveAttribute("open");
});

test("remote writes update counts, pending local edits are explicit, and publication matches navigation", async ({ page, browserName }) => {
  await login(page, "sidebar");
  const sid = browserName === "chromium" ? "000004.png" : "000005.png";
  const base = `/api/v1/datasets/sidebar/samples/${sid}`;
  const before = await (await page.request.get("/api/v1/datasets/sidebar/statistics")).json();
  const tab = crypto.randomUUID();
  const lease = await (await page.request.post(base + "/lease", { headers, data: { tab_id: tab } })).json();
  const sample = await (await page.request.get(base)).json();
  const write = { tab_id: tab, lease_id: lease.id, lease_generation: lease.generation,
    base_revision: sample.revision, operation_id: crypto.randomUUID() };
  const record = { region_id: "statistics-region", label: "text", description: "stats", shape_type: "rectangle", points: [[10, 10], [100, 100]], recoverable: 0 };
  const response = await page.request.put(base + "/draft", { headers, data: { ...write, hr: [record],
    recoverability: { LR2: { "statistics-region": 1 }, LR3: { "statistics-region": 1 }, LR4: { "statistics-region": 1 } } } });
  expect(response.ok(), await response.text()).toBeTruthy();
  const draft = await response.json();
  await expect(page.getByTestId("statistics-regions")).toHaveText(String(before.instances + 1), { timeout: 7000 });
  const published = await page.request.post(base + "/save-annotations", { headers, data: { ...write,
    base_revision: draft.revision, operation_id: crypto.randomUUID(), source_token: draft.source_token, image_version: draft.image_version } });
  expect(published.ok(), await published.text()).toBeTruthy();
  await expect(page.getByTestId("statistics-complete")).toHaveText(`${before.complete_samples + 1} / 12`, { timeout: 7000 });
  await expect(page.locator(".navigation .progress strong")).toHaveText(String(before.complete_samples + 1));
  await page.request.delete(base + "/lease", { headers, data: { tab_id: tab, lease_id: lease.id } });
  await page.getByRole("button", { name: sid }).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await page.getByRole("button", { name: "开始编辑", exact: true }).click();
  await page.locator(".region-list button").first().click();
  let unblock!: () => void;
  const gate = new Promise<void>(resolve => { unblock = resolve; });
  await page.route("**/draft", async route => { await gate; await route.continue(); });
  await page.getByLabel("OCR 真值").fill("changed locally");
  await expect(panel(page).getByText("本地修改尚未计入")).toBeVisible();
  unblock();
  await expect(page.locator(".save-state")).toHaveText("草稿已自动保存");
  await expect(panel(page).getByText("本地修改尚未计入")).toHaveCount(0);
  await expect(page.getByTestId("statistics-complete")).toHaveText(`${before.complete_samples} / 12`, { timeout: 7000 });
  await page.getByRole("button", { name: "结束编辑", exact: true }).click();
});

test("failed refresh retains counts, retry recovers, and obsolete dataset replies are ignored", async ({ page }) => {
  await login(page);
  const original = await page.getByTestId("statistics-regions").textContent();
  await page.route("**/datasets/text/statistics", route => route.fulfill({ status: 503,
    json: { error: { code: "unavailable", message: "test failure" } } }));
  await expect(panel(page).getByRole("button", { name: "重试统计" })).toBeVisible({ timeout: 7000 });
  await expect(page.getByTestId("statistics-regions")).toHaveText(original!);
  let releaseRetry!: () => void;
  const retryGate = new Promise<void>(resolve => { releaseRetry = resolve; });
  await page.unroute("**/datasets/text/statistics");
  await page.route("**/datasets/text/statistics", async route => { await retryGate; await route.continue(); });
  await panel(page).getByRole("button", { name: "重试统计" }).click();
  releaseRetry();
  await expect(panel(page).getByRole("button", { name: "重试统计" })).toHaveCount(0);
  await page.unroute("**/datasets/text/statistics");
  let unblock!: () => void;
  const gate = new Promise<void>(resolve => { unblock = resolve; });
  let started!: () => void;
  const received = new Promise<void>(resolve => { started = resolve; });
  await page.route("**/datasets/text/statistics", async route => {
    const response = await route.fetch(); started(); await gate; await route.fulfill({ response });
  });
  await received;
  await page.getByLabel("数据集", { exact: true }).selectOption("face");
  await expect(panel(page).getByText("人脸", { exact: true })).toBeVisible();
  unblock();
  await expect(page.getByTestId("statistics-regions")).toHaveText("0");
  await expect(panel(page).getByText("暂无区域")).toBeVisible();
});

test("single-sample share scopes counts and revocation clears the panel", async ({ page, browser }) => {
  await login(page);
  const result = await page.request.post("/api/v1/shares", { headers, data: { role: "view", dataset: "text", sample: "000010.png" } });
  expect(result.ok(), await result.text()).toBeTruthy();
  const share = await result.json();
  const context = await browser.newContext();
  const guest = await context.newPage();
  await guest.goto(share.url);
  await guest.getByRole("button", { name: "进入工作台" }).click();
  await expect(panel(guest).getByText("当前分享范围")).toBeVisible();
  await expect(guest.getByTestId("statistics-groups")).toHaveText("1");
  await expect(guest.getByTestId("statistics-regions")).toHaveText("357");
  expect((await page.request.delete(`/api/v1/shares/${share.id}`, { headers })).ok()).toBeTruthy();
  await expect(panel(guest).getByText("统计访问权限已失效")).toBeVisible({ timeout: 7000 });
  await expect(guest.getByTestId("statistics-groups")).toHaveCount(0);
  await context.close();
});
