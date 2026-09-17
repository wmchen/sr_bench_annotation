import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/** Open a temporary fixture through the production login and navigation. */
async function openSample(page: Page, name = "000010.png", count = 357): Promise<void> {
  const {token} = JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN ?? "/tmp/realisr-e2e-token", "utf8"));
  await page.goto("/#token=" + token);
  await page.getByLabel("显示昵称").fill("多选回归测试");
  await page.getByRole("button", {name:"进入工作台"}).click();
  await page.getByLabel("数据集", {exact:true}).selectOption("text");
  await page.getByRole("button", {name}).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await expect(page.locator(".region-list button")).toHaveCount(count);
}

/** Check exact list membership, including unselected rows between two clicks. */
async function expectSelection(page: Page, indices: number[]): Promise<void> {
  await expect.poll(() => page.locator(".region-list button").evaluateAll(nodes =>
    nodes.flatMap((node, index) => node.classList.contains("selected") ? [index] : []),
  )).toEqual(indices);
  await expect(page.getByRole("heading", {name:/区域属性/}).locator("small")).toHaveText(indices.length ? "已选 " + indices.length : "未选择");
}

test("list supports Ctrl/Cmd toggles and stable Shift ranges", async ({page}, testInfo) => {
  await openSample(page);
  const rows = page.locator(".region-list button");
  for (const modifier of ["Control", "Meta"] as const) {
    await rows.nth(1).click();
    await rows.nth(5).click({modifiers:[modifier]});
    await expectSelection(page, [1,5]);
    await rows.nth(5).click({modifiers:[modifier]});
    await expectSelection(page, [1]);
    await rows.nth(2).click();
    await rows.nth(5).click({modifiers:["Shift"]});
    await expectSelection(page, [2,3,4,5]);
    await rows.nth(3).click({modifiers:["Shift"]});
    await expectSelection(page, [2,3]);
    await rows.nth(0).click({modifiers:["Shift"]});
    await expectSelection(page, [0,1,2]);
    await rows.nth(5).click({modifiers:[modifier]});
    await rows.nth(7).click({modifiers:[modifier,"Shift"]});
    await expectSelection(page, [0,1,2,5,6,7]);
    await rows.nth(6).click({modifiers:["Shift"]});
    await expectSelection(page, [5,6]);
  }
  await page.screenshot({path:testInfo.outputPath("list-range-selection.png")});
  await page.keyboard.press("Escape");
  await rows.nth(4).click({modifiers:["Shift"]});
  await expectSelection(page, [4]);
  await page.getByRole("button", {name:"000000.png"}).click();
  await expect(rows).toHaveCount(0);
  await page.getByRole("button", {name:"000010.png"}).click();
  await expect(rows).toHaveCount(357);
  await rows.nth(7).click({modifiers:["Shift"]});
  await expectSelection(page, [7]);
});

test("canvas Ctrl/Cmd selection synchronizes all panes and the list without editing geometry", async ({page,browserName}) => {
  test.setTimeout(60000);
  const name = browserName === "chromium" ? "000008.png" : "000009.png";
  await openSample(page, name, 0);
  await page.getByRole("button", {name:"开始编辑"}).click();
  const hr = page.getByTestId("canvas-HR");
  const box = (await hr.boundingBox())!;
  const scale = Math.min(box.width / 800, box.height / 600) * .94;
  const x = box.x + box.width / 2 - 400 * scale;
  const y = box.y + box.height / 2 - 300 * scale;
  const rectangle = page.getByRole("button", {name:"矩形 R",exact:true});
  await rectangle.click();
  // Keep shapes well separated so expanded hit targets cannot overlap.
  for (const left of [80,350,620]) {
    await page.mouse.move(x + left * scale, y + 150 * scale);
    await page.mouse.down();
    await page.mouse.move(x + (left + 100) * scale, y + 250 * scale, {steps:3});
    await page.mouse.up();
  }
  await rectangle.click();
  await expect(page.locator(".region-list button")).toHaveCount(3);
  await expect(page.locator(".save-state")).toHaveText("草稿已自动保存");
  await page.getByRole("button", {name:"结束编辑"}).click();
  const path = "/api/v1/datasets/text/samples/" + name;
  const sample = await (await page.request.get(path)).json();
  const before = sample.draft;
  const rows = page.locator(".region-list button");
  for (const editing of [false, true]) {
    if (editing) await page.getByRole("button", {name:"开始编辑"}).click();
    for (const variant of ["HR", "LR2", "LR3", "LR4"]) {
      const canvas = page.getByTestId("canvas-" + variant);
      const position = async (index: number): Promise<{x:number;y:number}> => {
        const box = (await canvas.boundingBox())!;
        const [width, height] = sample.dimensions[variant];
        const [hrWidth, hrHeight] = sample.dimensions.HR;
        const scale = Math.min(box.width / hrWidth, box.height / hrHeight) * hrWidth / width * .94;
        const region = before[variant].find((r: {region_id:string}) => r.region_id === before.HR[index].region_id);
        const xs = region.points.map((p: number[]) => p[0]);
        const ys = region.points.map((p: number[]) => p[1]);
        return {
          x:box.width / 2 + ((Math.min(...xs) + Math.max(...xs)) / 2 - width / 2) * scale,
          y:box.height / 2 + ((Math.min(...ys) + Math.max(...ys)) / 2 - height / 2) * scale,
        };
      };
      for (const modifier of ["Control", "Meta"] as const) {
        await rows.nth(0).click();
        await canvas.click({position:await position(2), modifiers:[modifier]});
        await expectSelection(page, [0,2]);
        await canvas.click({position:await position(2), modifiers:[modifier]});
        await expectSelection(page, [0]);
        // A canvas click also establishes the anchor for list range selection.
        await canvas.click({position:await position(0)});
        await rows.nth(2).click({modifiers:["Shift"]});
        await expectSelection(page, [0,1,2]);
        await canvas.click({position:await position(1), modifiers:["Shift"]});
        await expectSelection(page, [1]);
      }
    }
  }
  await page.getByRole("button", {name:"结束编辑"}).click();
  expect((await (await page.request.get(path)).json()).draft).toEqual(before);
});
