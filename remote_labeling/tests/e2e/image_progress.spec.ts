import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const headers = { Origin: "http://127.0.0.1:8877" };
const progress = (page: Page) => page.getByRole("region", { name: "当前图像标注进度", exact: true });
async function login(page: Page, query = "dataset=sidebar"): Promise<void> {
  const { token } = JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN ?? "/tmp/realisr-e2e-token", "utf8"));
  await page.goto(`/?${query}#token=${token}`);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByTestId("statistics-groups")).toBeVisible();
}
async function opened(page: Page, sample: string): Promise<void> {
  await expect(page.locator(".sample-toolbar strong")).toHaveText(sample);
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await expect(page.getByText("正在打开样本…", { exact: true })).toHaveCount(0);
}

test("current image follows active variant, unsaved edits, undo and deletion", async ({ page, browserName }, info) => {
  await login(page);
  const id = browserName === "chromium" ? "000008.png" : "000009.png";
  const base = `/api/v1/datasets/sidebar/samples/${id}`;
  const tab = crypto.randomUUID();
  const lease = await (await page.request.post(base + "/lease", { headers, data: { tab_id: tab } })).json();
  const sample = await (await page.request.get(base)).json();
  const hr = ["a", "b"].map((id, index) => ({ region_id: `image-progress-${id}`, label: "text", description: id,
    shape_type: "rectangle", points: [[10 + index * 120, 10], [100 + index * 120, 100]], recoverable: 0 }));
  const response = await page.request.put(base + "/draft", { headers, data: { tab_id: tab, lease_id: lease.id,
    lease_generation: lease.generation, base_revision: sample.revision, operation_id: crypto.randomUUID(), hr,
    recoverability: { LR2: { "image-progress-a": 1 } } } });
  expect(response.ok(), await response.text()).toBeTruthy();
  await page.request.delete(base + "/lease", { headers, data: { tab_id: tab, lease_id: lease.id } });
  await page.getByRole("button", { name: id }).click();
  await opened(page, id);
  await page.getByRole("button", { name: "当前图像", exact: true }).click();
  await expect(progress(page).getByText(id, { exact: true })).toBeVisible();
  await expect(page.getByTestId("image-progress-active")).toHaveText("2 / 2");
  await expect(page.getByTestId("image-progress-percent")).toHaveText("100.0%");
  await expect(page.getByTestId("image-progress-regions")).toHaveText("0 / 2");
  await page.locator(".pane header strong").filter({ hasText: /^LR2$/ }).click();
  await expect(page.getByTestId("image-progress-percent")).toHaveText("50.0%");
  await expect(page.getByTestId("image-progress-LR2")).toHaveAttribute("aria-current", "true");
  await page.getByRole("button", { name: "开始编辑", exact: true }).click();
  await page.locator(".region-list button").nth(1).click();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/draft", async route => { await gate; await route.continue(); });
  await page.locator(".evidence-buttons button").filter({ hasText: /^0$/ }).click();
  await expect(page.getByTestId("image-progress-percent")).toHaveText("100.0%");
  await expect(progress(page).getByText(/包含本地修改/)).toBeVisible();
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.getByTestId("image-progress-percent")).toHaveText("50.0%");
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await expect(page.getByTestId("image-progress-percent")).toHaveText("100.0%");
  release();
  await expect(page.locator(".save-state")).toHaveText("草稿已自动保存");
  await expect(progress(page).getByText("服务器已保存草稿")).toBeVisible();
  await page.locator(".pane header strong").filter({ hasText: /^HR$/ }).click();
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.getByTestId("image-progress-regions")).toHaveText("0 / 1");
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.getByTestId("image-progress-regions")).toHaveText("0 / 2");
  await page.getByRole("separator", { name: "调整右侧栏宽度" }).press("Home");
  expect(await progress(page).evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("current-image-progress.png") });
  await page.getByRole("button", { name: "结束编辑", exact: true }).click();
  await page.getByRole("button", { name: "000011.png" }).click();
  await opened(page, "000011.png");
  await expect(progress(page).getByText("000011.png", { exact: true })).toBeVisible();
  await expect(page.getByTestId("image-progress-active")).toHaveText("0 / 0");
  await expect(page.getByTestId("image-progress-percent")).toHaveText("—");
  await expect(progress(page).getByText("暂无区域")).toBeVisible();
  await page.getByRole("button", { name: "数据集", exact: true }).click();
  await expect(page.getByTestId("statistics-groups")).toHaveText("12");
});

test("progress uses displayed formal or draft content and survives statistics API failure", async ({ page }) => {
  await page.route("**/datasets/opening/samples/000052.png", async route => {
    const response = await route.fetch();
    const sample = await response.json();
    sample.draft.LR4[0].recoverable = null;
    await route.fulfill({ json: sample });
  });
  await login(page, "dataset=opening");
  await opened(page, "000052.png");
  await page.getByRole("button", { name: "当前图像", exact: true }).click();
  await expect(page.getByTestId("image-progress-regions")).toHaveText("0 / 1");
  await page.getByLabel("查看已保存草稿").uncheck();
  await expect(page.getByTestId("image-progress-regions")).toHaveText("1 / 1");
  await expect(progress(page).getByText("按画布中的正式标注统计")).toBeVisible();
  await page.getByLabel("查看已保存草稿").check();
  await expect(page.getByTestId("image-progress-regions")).toHaveText("0 / 1");
  await page.route("**/datasets/opening/statistics", route => route.fulfill({ status: 503,
    json: { error: { code: "unavailable", message: "statistics unavailable" } } }));
  await page.waitForResponse(response => response.url().endsWith("/opening/statistics") && response.status() === 503);
  await expect(page.getByTestId("image-progress-regions")).toHaveText("0 / 1");
  await expect(page.getByRole("button", { name: "重试统计" })).toHaveCount(0);
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/datasets/opening/samples/000011.png", async route => { await gate; await route.continue(); });
  await page.getByRole("button", { name: "000011.png" }).click();
  await expect(page.getByText("正在加载当前图像…")).toBeVisible();
  await expect(page.getByTestId("image-progress-regions")).toHaveCount(0);
  release();
  await opened(page, "000011.png");
  await expect(page.getByTestId("image-progress-regions")).toHaveText("0 / 0");
});
