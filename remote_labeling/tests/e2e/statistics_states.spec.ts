import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

test("loading, large numbers, scan failure and empty statistics remain readable", async ({ page }, info) => {
  const distribution = { sufficient: 3333333, ambiguous: 0, insufficient: 0, unset: 6666666, assigned: 3333333, total: 9999999 };
  const statistics = {
    dataset: "text", attribute: "text", scope: "dataset", status: "ready", import_version: 1,
    generated_at: Date.now() / 1000, sample_groups: 1234567, image_files: 4938268,
    instances: 9999999, completed_instances: 3333333, recoverability_assigned: 13333332,
    recoverability_total: 39999996, complete_samples: 123456, pending_samples: 999999,
    by_variant: { HR: distribution, LR2: distribution, LR3: distribution, LR4: distribution },
  };
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/datasets/text/statistics", async route => {
    await gate;
    await route.fulfill({ json: statistics });
  });
  const { token } = JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN ?? "/tmp/realisr-e2e-token", "utf8"));
  await page.goto(`/?dataset=text#token=${token}`);
  await page.getByRole("button", { name: "进入工作台" }).click();
  const panel = page.getByRole("region", { name: "数据集统计", exact: true });
  await expect(panel.getByText("正在加载统计…")).toBeVisible();
  release();
  await expect(page.getByTestId("statistics-regions")).toHaveText("9,999,999");
  await page.getByRole("separator", { name: "调整右侧栏宽度" }).press("Home");
  await panel.locator("summary").click();
  expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("statistics-large-numbers.png") });
  statistics.status = "scanning";
  await page.reload();
  await expect(panel.getByText("扫描中 · 统计暂未更新")).toBeVisible();
  statistics.status = "invalid";
  await page.reload();
  await expect(panel.getByText("校验失败 · 仅显示上次成功导入的统计")).toBeVisible();
  statistics.import_version = 0;
  await page.reload();
  await expect(panel.getByText("校验失败 · 暂无成功导入数据")).toBeVisible();
  Object.assign(statistics, { status: "ready", sample_groups: 0, image_files: 0, instances: 0,
    completed_instances: 0, recoverability_assigned: 0, recoverability_total: 0, complete_samples: 0, pending_samples: 0 });
  Object.assign(distribution, { sufficient: 0, unset: 0, assigned: 0, total: 0 });
  await page.reload();
  await expect(panel.getByText("暂无区域")).toBeVisible();
  await expect(page.getByTestId("statistics-complete")).toHaveText("0 / 0");
  await expect(panel.getByRole("progressbar", { name: "证据赋值进度" })).toHaveAttribute("value", "0");
});
