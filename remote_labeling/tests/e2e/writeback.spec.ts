import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

function fixture():{token:string;root:string}{
  return JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN??"/tmp/realisr-e2e-token","utf8"));
}
async function login(page:Page,sample:string){
  await page.goto("/?dataset=writeback&sample="+sample+"#token="+fixture().token);
  await page.getByRole("button",{name:"进入工作台"}).click();
  await expect(page.locator(".sample-toolbar strong")).toHaveText(sample);
  await expect(page.locator(".loading-image")).toHaveCount(0);
}
async function draw(page:Page){
  await page.getByRole("button",{name:"开始编辑",exact:true}).click();
  await page.getByRole("button",{name:"矩形 R",exact:true}).click();
  const box=(await page.getByTestId("canvas-HR").boundingBox())!;
  await page.mouse.move(box.x+box.width*.3,box.y+box.height*.3);
  await page.mouse.down();
  await page.mouse.move(box.x+box.width*.6,box.y+box.height*.6,{steps:4});
  await page.mouse.up();
  await expect(page.locator(".region-list button")).toHaveCount(1);
}

test("view-mode saves unfinished drafts to disk with a temporary lease",async({page,browserName})=>{
  const name=browserName==="chromium"?"000000.png":"000006.png";
  const next=browserName==="chromium"?"000001.png":"000007.png";
  await login(page,name);
  const save=page.getByRole("button",{name:"保存标注",exact:true});
  await expect(save).toBeVisible();
  await expect(page.getByRole("button",{name:"确认整组",exact:true})).toHaveCount(0);
  await draw(page);
  await page.getByLabel("OCR 真值").fill("view-mode partial");
  await page.getByRole("button",{name:"结束编辑"}).click();
  await expect(save).toBeEnabled();
  await page.keyboard.press("Control+s");
  await expect(page.locator(".sample-toolbar strong")).toHaveText(next);
  const root=join(fixture().root,"writeback","annotations");
  for(const variant of ["HR","LR2","LR3","LR4"]){
    const doc=JSON.parse(readFileSync(join(root,variant,name.replace(".png",".json")),"utf8"));
    expect(doc.shapes[0].description).toBe("view-mode partial");
    if(variant!=="HR")expect(doc.shapes[0].recoverable).toBeNull();
  }
  await page.getByRole("button",{name,exact:false}).click();
  await expect(save).toBeDisabled();
  await expect(page.locator(".sample-toolbar")).toContainText("标注已写回");
  await expect(page.getByRole("button",{name:"开始编辑",exact:true})).toBeVisible();
  const state=await (await page.request.get("/api/v1/datasets/writeback/samples/"+name)).json();
  expect(state.occupancy).toBeNull();
  expect(state.complete).toBe(false);
  await page.getByRole("button",{name:"开始编辑",exact:true}).click();
  await page.locator(".region-list button").click();
  await page.getByLabel("OCR 真值").fill("changed");
  await expect(save).toBeEnabled();
  await page.getByRole("button",{name:"撤销",exact:true}).click();
  await expect(save).toBeDisabled();
  await page.getByRole("button",{name:"结束编辑"}).click();
  await page.screenshot({path:"test-results/save-annotations-"+browserName+".png",fullPage:true});
});

test("external JSON changes require an explicit overwrite",async({page,browserName})=>{
  const name=browserName==="chromium"?"000002.png":"000008.png";
  await login(page,name);
  await draw(page);
  await page.getByRole("button",{name:"保存标注",exact:true}).click();
  await expect(page.locator(".sample-toolbar strong")).not.toHaveText(name);
  await page.getByRole("button",{name,exact:false}).click();
  await page.getByRole("button",{name:"开始编辑",exact:true}).click();
  await page.locator(".region-list button").click();
  await page.getByLabel("OCR 真值").fill("web edit");
  const path=join(fixture().root,"writeback","annotations","HR",name.replace(".png",".json"));
  const external=JSON.parse(readFileSync(path,"utf8"));
  external.shapes[0].description="outside edit";
  writeFileSync(path,JSON.stringify(external));
  await page.getByRole("button",{name:"保存标注",exact:true}).click();
  await expect(page.getByRole("button",{name:"用当前草稿覆盖"})).toBeVisible();
  expect(JSON.parse(readFileSync(path,"utf8")).shapes[0].description).toBe("outside edit");
  await page.getByRole("button",{name:"用当前草稿覆盖"}).click();
  await expect(page.locator(".sample-toolbar strong")).not.toHaveText(name);
  expect(JSON.parse(readFileSync(path,"utf8")).shapes[0].description).toBe("web edit");
});

test("lost save responses replay the same operation and preserve the last group",async({page,browserName})=>{
  const name=browserName==="chromium"?"000005.png":"000011.png";
  await login(page,name);
  await draw(page);
  const requests:unknown[]=[];
  await page.route("**/save-annotations",async route=>{
    requests.push(route.request().postDataJSON());
    if(requests.length===1){await route.fetch();await route.abort("failed");}
    else await route.continue();
  });
  await page.getByRole("button",{name:"保存标注",exact:true}).click();
  await expect(page.locator(".sample-toolbar strong")).toHaveText(name);
  await expect(page.locator(".sample-toolbar")).toContainText("写回结果待确认");
  await page.getByRole("button",{name:"重试保存",exact:true}).click();
  if(browserName==="firefox"){
    await expect(page.getByRole("button",{name:"保存标注",exact:true})).toBeDisabled();
    await expect(page.locator(".sample-toolbar strong")).toHaveText(name);
    await page.getByRole("button",{name:"结束编辑"}).click();
  }else await expect(page.locator(".sample-toolbar strong")).not.toHaveText(name);
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);
});
