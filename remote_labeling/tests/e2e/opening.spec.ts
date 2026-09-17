import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const openingURL = "**/api/v1/datasets/opening/opening-selection*";

async function login(page: Page, query = "?dataset=opening") {
  const { token } = JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN ?? "/tmp/realisr-e2e-token", "utf8"));
  await page.goto("/" + query + "#token=" + token);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByLabel("数据集", { exact: true })).toBeVisible();
}

async function opened(page: Page, sample: string) {
  await expect(page.locator(".sample-toolbar strong")).toHaveText(sample);
  await expect(page.locator(".pane")).toHaveCount(4);
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await expect(page.getByText("正在打开样本…", { exact: true })).toHaveCount(0);
}

function gate() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

async function painted(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

test("opening finds a late-page draft, previews it without a lease, and survives refresh", async ({ page }) => {
  let selections = 0, leases = 0;
  page.on("request", request => {
    if (request.url().includes("/opening-selection")) selections++;
    if (request.url().endsWith("/lease") && request.method() === "POST") leases++;
  });
  await login(page);
  await opened(page, "000052.png");
  await expect(page.locator(".pagination")).toContainText("2 / 2");
  await expect(page.locator(".sample-list .selected")).toContainText("000052.png");
  await expect(page.getByLabel("查看已保存草稿")).toBeChecked();
  await page.locator(".region-list button").click();
  await expect(page.getByLabel("OCR 真值")).toHaveValue("待恢复草稿");
  await expect(page.getByLabel("OCR 真值")).not.toBeEditable();
  expect(leases).toBe(0);
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await page.getByLabel("搜索文件名").fill("00000");
  await expect(page.locator(".sample-list button")).toHaveCount(10);
  // The periodic dataset update must not run opening selection again.
  await page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/datasets");
  await opened(page, "000052.png");
  expect(selections).toBe(1);
  await page.reload();
  await opened(page, "000052.png");
  await expect(page.getByLabel("查看已保存草稿")).toBeChecked();
  await page.getByLabel("数据集", { exact: true }).selectOption("face");
  await opened(page, "000000.png");
  await page.getByLabel("数据集", { exact: true }).selectOption("opening");
  await opened(page, "000052.png");
  expect(leases).toBe(0);
  await expect(page.locator(".sample-list .selected")).toContainText("000052.png");
  await expect(page.locator(".pagination")).toContainText("2 / 2");
  await page.screenshot({ path: "test-results/opening-" + test.info().project.name + ".png", fullPage: true });
});

test("explicit links and refresh keep their target instead of choosing a draft", async ({ page }) => {
  await login(page, "?dataset=opening&sample=000010.png");
  await opened(page, "000010.png");
  await expect(page.locator(".pagination")).toContainText("1 / 2");
  await page.reload();
  await opened(page, "000010.png");
  await page.goto("/?dataset=opening&sample=missing.png");
  await expect(page.getByRole("alert")).toContainText("样本不存在");
  await expect(page.locator(".sample-toolbar")).toHaveCount(0);
  await page.getByRole("button", { name: "重试打开" }).click();
  await expect(page.getByRole("alert")).toContainText("样本不存在");
  await page.locator(".sample-list button").filter({ hasText: "000001.png" }).click();
  await opened(page, "000001.png");
});

test("opening selection and original image failures can both be retried", async ({ page }) => {
  await page.route(openingURL, route => route.fulfill({
    status: 503, json: { error: { code: "unavailable", message: "首图读取暂时失败" } },
  }), { times: 1 });
  await login(page);
  await expect(page.getByRole("alert")).toContainText("首图读取暂时失败");
  await page.route("**/datasets/opening/samples/000052.png/images/LR4", route => route.fulfill({ status: 503, body: "unavailable" }), { times: 1 });
  await page.getByRole("button", { name: "重试打开" }).click();
  await expect(page.getByRole("alert")).toContainText("原图加载失败");
  await page.getByRole("button", { name: "重试打开" }).click();
  await opened(page, "000052.png");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("manual selection supersedes a delayed automatic selection", async ({ page }) => {
  const started = gate(), resume = gate();
  await page.route(openingURL, async route => {
    const response = await route.fetch();
    started.resolve();
    await resume.promise;
    await route.fulfill({ response });
  }, { times: 1 });
  await login(page);
  await started.promise;
  await page.locator(".sample-list button").filter({ hasText: "000001.png" }).click();
  await opened(page, "000001.png");
  const response = page.waitForResponse(r => r.url().includes("/opening-selection"));
  resume.resolve();
  await response;
  await painted(page);
  await opened(page, "000001.png");
  await expect(page).toHaveURL(/sample=000001.png/);
  await expect(page.locator(".pagination")).toContainText("1 / 2");
});

for (const action of ["switch", "logout"] as const) {
  test("delayed details cannot undo " + action, async ({ page }) => {
    const started = gate(), resume = gate();
    const path = "/api/v1/datasets/opening/samples/000052.png";
    await page.route("**" + path, async route => {
      const response = await route.fetch();
      started.resolve();
      await resume.promise;
      await route.fulfill({ response });
    }, { times: 1 });
    await login(page);
    await started.promise;
    if (action === "switch") {
      await page.getByLabel("数据集", { exact: true }).selectOption("face");
      await opened(page, "000000.png");
    } else {
      await page.getByRole("button", { name: "退出", exact: true }).click();
      await expect(page.getByRole("button", { name: "进入工作台" })).toBeVisible();
    }
    const response = page.waitForResponse(r => new URL(r.url()).pathname === path);
    resume.resolve();
    await response;
    await painted(page);
    if (action === "switch") {
      await opened(page, "000000.png");
      await expect(page).toHaveURL(/dataset=face&sample=000000.png/);
    } else {
      await expect(page.locator(".sample-toolbar")).toHaveCount(0);
      await expect(page).toHaveURL(/\?dataset=opening$/);
    }
  });
}

test("late local recovery cannot replace a newer dataset", async ({ page }) => {
  await page.addInitScript(() => {
    const original = IDBDatabase.prototype.transaction;
    let held = false;
    IDBDatabase.prototype.transaction = function (...args: Parameters<typeof original>) {
      const transaction = original.apply(this, args);
      if (args[1] === "readonly" && !held) {
        held = true;
        Object.defineProperty(transaction, "oncomplete", { set(callback) {
          transaction.addEventListener("complete", event => {
            const target = window as unknown as { recoveryHeld: boolean; releaseRecovery: () => void };
            target.recoveryHeld = true;
            target.releaseRecovery = () => callback.call(transaction, event);
          });
        } });
      }
      return transaction;
    };
  });
  await login(page);
  await page.waitForFunction(() => (window as unknown as { recoveryHeld: boolean }).recoveryHeld);
  await page.getByLabel("数据集", { exact: true }).selectOption("face");
  await opened(page, "000000.png");
  await page.evaluate(() => (window as unknown as { releaseRecovery: () => void }).releaseRecovery());
  await painted(page);
  await opened(page, "000000.png");
  await expect(page.locator(".recovery-banner")).toHaveCount(0);
  await expect(page).toHaveURL(/dataset=face&sample=000000.png/);
});

test("empty datasets show an explicit empty state", async ({ page }) => {
  await page.route(openingURL, route => route.fulfill({ json: { sample: null, index: null, pending_draft: false } }));
  await login(page);
  await expect(page.getByRole("heading", { name: "暂无可用样本" })).toBeVisible();
  await expect(page.locator(".sample-toolbar")).toHaveCount(0);
});

test("initial opening waits for scanning to finish", async ({ page }) => {
  let scanning = true, selections = 0;
  await page.route("**/api/v1/datasets", async route => {
    const response = await route.fetch();
    const list = await response.json();
    await route.fulfill({ json: list.map((item: { id: string; status: string }) => item.id === "opening" && scanning ? { ...item, status: "scanning" } : item) });
  });
  page.on("request", r => { if (r.url().includes("/opening-selection")) selections++; });
  await login(page);
  await expect(page.getByRole("heading", { name: "正在扫描数据集…" })).toBeVisible();
  expect(selections).toBe(0);
  scanning = false;
  // Polling runs every five seconds; wait for readiness before timing image load.
  await page.waitForResponse(async response => {
    if (new URL(response.url()).pathname !== "/api/v1/datasets") return false;
    return (await response.json()).some((item: { id: string; status: string }) => item.id === "opening" && item.status === "ready");
  });
  await opened(page, "000052.png");
  expect(selections).toBe(1);
});
