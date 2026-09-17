import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

/** Open a fresh sample with the same production login used by workspace tests. */
async function openSample(page: Page, name: string): Promise<void> {
  const {token} = JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN ?? "/tmp/realisr-e2e-token", "utf8"));
  await page.goto("/#token=" + token);
  await page.getByLabel("显示昵称").fill("交互回归测试");
  await page.getByRole("button", {name:"进入工作台"}).click();
  await page.getByLabel("数据集", {exact:true}).selectOption("text");
  await page.getByRole("button", {name}).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
}

/** Read actual rendered layers to detect panning independently of annotation saves. */
async function layers(canvas: Locator): Promise<string[]> {
  const images = await canvas.locator("canvas").evaluateAll(async nodes => {
    // Wait for the production canvas batch draw before comparing pixels.
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
    return nodes.map(node => (node as HTMLCanvasElement).toDataURL());
  });
  return images.map(image => createHash("sha256").update(image).digest("hex"));
}

/** Move the mouse and assert the CSS cursor on the visible canvas itself. */
async function hover(page: Page, canvas: Locator, x: number, y: number, cursor: string): Promise<void> {
  await page.mouse.move(x,y);
  await expect(canvas.locator("canvas").last()).toHaveCSS("cursor",cursor);
}

test("rectangle selection, edge resizing, live corners, view mode and forced pan", async ({page,browserName}, testInfo) => {
  const sample=browserName === "chromium" ? "000007.png" : "000001.png";
  await openSample(page,sample);
  await expect(page.getByRole("button", {name:"选择",exact:true})).toHaveCount(0);
  await expect(page.getByRole("button", {name:"平移",exact:true})).toHaveCount(0);
  await expect(page.getByRole("button", {name:"开始编辑"})).toHaveCSS("cursor","default");
  const canvas = page.getByTestId("canvas-HR");
  const background = async (): Promise<string> => (await layers(canvas))[0];
  // View mode pans empty canvas, and releasing outside ends the captured gesture.
  let box = (await canvas.boundingBox())!;
  await hover(page,canvas,box.x+20,box.y+20,"grab");
  const initial = await background();
  await page.mouse.down();
  await expect(canvas).toHaveCSS("cursor","grabbing");
  await page.mouse.move(box.x+50,box.y+30,{steps:3});
  await page.mouse.up();
  await expect.poll(background).not.toEqual(initial);
  await canvas.locator("..").getByRole("button", {name:"适应",exact:true}).click();
  await page.getByRole("button", {name:"开始编辑"}).click();
  const rectangle = page.getByRole("button", {name:"矩形 R",exact:true});
  await rectangle.click();
  await expect(rectangle).toHaveAttribute("aria-pressed","true");
  box = (await canvas.boundingBox())!;
  const left=box.x+box.width*.3, right=box.x+box.width*.65;
  const top=box.y+box.height*.3, bottom=box.y+box.height*.6;
  const cx=(left+right)/2, cy=(top+bottom)/2;
  await hover(page,canvas,left,top,"default");
  const beforeDraw = await background();
  await page.mouse.down(); await page.mouse.move(right,bottom,{steps:5}); await page.mouse.up();
  await expect(page.locator(".region-list button")).toHaveCount(1);
  expect(await background()).toEqual(beforeDraw);
  await rectangle.click();
  await expect(rectangle).toHaveAttribute("aria-pressed","false");
  await page.keyboard.press("Escape");
  await hover(page,canvas,cx,cy,"pointer");
  const beforeSelect = await background();
  await page.mouse.down(); await page.mouse.move(cx+15,cy+10,{steps:3}); await page.mouse.up();
  await expect(page.locator(".region-list button.selected")).toHaveCount(1);
  expect(await background()).toEqual(beforeSelect);
  await expect(page.getByRole("button",{name:"撤销",exact:true})).toBeEnabled();
  await hover(page,canvas,cx,cy,"move");
  for (const [x,y,cursor] of [
    [cx,top,"ns-resize"],[cx,bottom,"ns-resize"],
    [left,cy,"ew-resize"],[right,cy,"ew-resize"],
    [left,top,"nwse-resize"],[right,bottom,"nwse-resize"],
    [right,top,"nesw-resize"],[left,bottom,"nesw-resize"],
  ] as const) await hover(page,canvas,x,y,cursor);
  // Resize the top edge, then inspect the new top-left handle BEFORE mouseup.
  await hover(page,canvas,cx,top,"ns-resize");
  await page.mouse.down(); await page.mouse.move(cx,top+20,{steps:5});
  await expect.poll(async () => canvas.locator("canvas").last().evaluate((node, p) => {
    const c=node as HTMLCanvasElement, ratio=c.width/c.getBoundingClientRect().width;
    return Array.from(c.getContext("2d")!.getImageData(Math.round(p[0]*ratio),Math.round(p[1]*ratio),1,1).data);
  }, [left-box.x-2,top-box.y+18])).toEqual([255,255,255,255]);
  await page.screenshot({path:testInfo.outputPath("rectangle-live-resize.png")});
  await page.mouse.up();
  await hover(page,canvas,left,top+20,"nwse-resize");
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const current = async () => (await (await page.request.get("/api/v1/datasets/text/samples/"+sample)).json()).draft;
  const resized = await current();
  const scale=Math.min(box.width/800,box.height/600)*.94;
  // Firefox rounds synthetic pointer coordinates to whole CSS pixels.
  const expectedTop=(top+20-box.y-box.height/2)/scale+300;
  expect(Math.abs(resized.HR[0].points[0][1]-expectedTop)*scale).toBeLessThanOrEqual(1);
  await page.getByRole("button",{name:"撤销",exact:true}).click();
  await hover(page,canvas,left,top,"nwse-resize");
  await page.getByRole("button",{name:"重做",exact:true}).click();
  await hover(page,canvas,left,top+20,"nwse-resize");
  // The new key must still behave like text editing inside the OCR field.
  await page.getByLabel("OCR 真值").fill("ab");
  await page.getByLabel("OCR 真值").press("Backspace");
  await expect(page.getByLabel("OCR 真值")).toHaveValue("a");
  await page.getByLabel("OCR 真值").press("Space");
  await expect(page.getByLabel("OCR 真值")).toHaveValue("a ");
  await page.mouse.click(cx,cy);
  const beforePan = await background();
  await page.keyboard.down("Space");
  await expect(canvas).toHaveCSS("cursor","grab");
  await page.mouse.down();
  await expect(canvas).toHaveCSS("cursor","grabbing");
  await page.mouse.move(cx+25,cy+15,{steps:4}); await page.mouse.up();
  await page.keyboard.up("Space");
  await expect.poll(background).not.toEqual(beforePan);
  await expect(page.locator(".save-state")).toHaveText("已保存");
  expect((await current()).HR[0].points).toEqual(resized.HR[0].points);
  await canvas.locator("..").getByRole("button", {name:"适应",exact:true}).click();
  // Viewing an existing shape only selects, even when dragged or right-clicked.
  await page.getByRole("button",{name:"结束编辑"}).click();
  box = (await canvas.boundingBox())!;
  const viewX=box.x+box.width*.475, viewY=box.y+box.height*.45;
  await hover(page,canvas,viewX,viewY,"pointer");
  const view = await layers(canvas);
  await page.mouse.down(); await page.mouse.move(viewX+15,viewY+10,{steps:3}); await page.mouse.up();
  expect(await layers(canvas)).toEqual(view);
  await page.mouse.down({button:"right"}); await page.mouse.move(viewX+30,viewY+20); await page.mouse.up({button:"right"});
  expect(await layers(canvas)).toEqual(view);
  await page.keyboard.press("Escape");
  // Right-drag on empty canvas must also leave the viewport untouched.
  await page.mouse.move(box.x+10,box.y+10);
  const noRightPan = await background();
  await page.mouse.down({button:"right"}); await page.mouse.move(box.x+40,box.y+25); await page.mouse.up({button:"right"});
  expect(await background()).toEqual(noRightPan);
  await page.keyboard.press("Escape");
  await page.getByRole("button",{name:"开始编辑"}).click();
  await expect(rectangle).toBeEnabled();
  box = (await canvas.boundingBox())!;
  await page.mouse.click(box.x+box.width*.475,box.y+box.height*.45);
  await expect(page.locator(".region-list button.selected")).toHaveCount(1);
  await page.keyboard.press("Backspace");
  await expect(page.locator(".region-list button")).toHaveCount(0);
  await page.getByRole("button",{name:"撤销",exact:true}).click();
  await page.mouse.click(box.x+box.width*.475,box.y+box.height*.45);
  await expect(page.locator(".region-list button.selected")).toHaveCount(1);
  await page.keyboard.press("Delete");
  await expect(page.locator(".region-list button")).toHaveCount(0);
  await page.getByRole("button",{name:"结束编辑"}).click();
});

test("quadrilateral handles, drawing toggle, forced pan during drawing and cancellation", async ({page,browserName}, testInfo) => {
  const sample=browserName === "chromium" ? "000011.png" : "000002.png";
  await openSample(page,sample);
  await page.getByRole("button",{name:"开始编辑"}).click();
  const canvas=page.getByTestId("canvas-HR");
  const box=(await canvas.boundingBox())!;
  const q=page.getByRole("button",{name:"四边形 Q",exact:true});
  const left=box.x+box.width*.3, right=box.x+box.width*.65;
  const top=box.y+box.height*.3, bottom=box.y+box.height*.6;
  const cx=(left+right)/2, cy=(top+bottom)/2;
  await q.click();
  await hover(page,canvas,left,top,"default");
  await page.mouse.click(left,top);
  await q.click(); // Cancel an unfinished polygon, not just the active button style.
  await expect(q).toHaveAttribute("aria-pressed","false");
  await expect(page.locator(".region-list button")).toHaveCount(0);
  await q.click();
  for (const [x,y] of [[left,top],[right,top],[right,bottom],[left,bottom]]) await page.mouse.click(x,y);
  await expect(page.locator(".region-list button")).toHaveCount(1);
  await q.click();
  await hover(page,canvas,cx,top,"move"); // Quadrilateral edges cannot resize.
  await hover(page,canvas,left,top,"pointer");
  await page.mouse.down();
  await expect(canvas).toHaveCSS("cursor","grabbing");
  await page.mouse.move(left+20,top+15,{steps:5});
  await page.screenshot({path:testInfo.outputPath("quadrilateral-live-resize.png")});
  await page.mouse.up();
  await hover(page,canvas,left+20,top+15,"pointer");
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const current=async () => (await (await page.request.get("/api/v1/datasets/text/samples/"+sample)).json()).draft.HR[0];
  const before = await current();
  await hover(page,canvas,cx,bottom,"move");
  await page.mouse.down(); await page.mouse.move(cx+10,bottom+10,{steps:3}); await page.mouse.up();
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const moved = await current();
  for (let i=0;i<4;i++) {
    expect(moved.points[i][0]-before.points[i][0]).toBeCloseTo(moved.points[0][0]-before.points[0][0],5);
    expect(moved.points[i][1]-before.points[i][1]).toBeCloseTo(moved.points[0][1]-before.points[0][1],5);
  }
  // A forced pan in rectangle drawing mode must never create a rectangle.
  const r=page.getByRole("button",{name:"矩形 R",exact:true});
  await r.click();
  await hover(page,canvas,cx,cy,"default");
  const initial=(await layers(canvas))[0];
  await page.keyboard.down("Space"); await page.mouse.down();
  await expect(canvas).toHaveCSS("cursor","grabbing");
  await page.mouse.move(cx+25,cy+20,{steps:3});
  await page.keyboard.up("Space"); // Releasing Space first still ends as a pan.
  await page.mouse.up();
  await expect(canvas).toHaveCSS("cursor","default");
  await expect.poll(async () => (await layers(canvas))[0]).not.toEqual(initial);
  await expect(page.locator(".region-list button")).toHaveCount(1);
  await r.click();
  await page.mouse.move(box.x+10,box.y+10); await page.mouse.down();
  await page.mouse.move(box.x-10,box.y-10); await page.mouse.up();
  const ended=await layers(canvas);
  await page.mouse.move(box.x+50,box.y+50);
  expect(await layers(canvas)).toEqual(ended);
  await page.keyboard.down("Space");
  await page.evaluate(()=>window.dispatchEvent(new Event("blur")));
  await page.keyboard.up("Space");
  await expect(canvas).toHaveCSS("cursor","grab");
  await page.locator(".region-list button").last().click();
  await page.getByRole("button",{name:"删除",exact:true}).click();
  await expect(page.locator(".region-list button")).toHaveCount(0);
  await page.getByRole("button",{name:"结束编辑"}).click();
});
