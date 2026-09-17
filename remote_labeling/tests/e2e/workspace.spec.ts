import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

async function login(page:Page) {
  const {token}=JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN??"/tmp/realisr-e2e-token","utf8"));
  await page.goto("/#token="+token);
  await page.getByLabel("显示昵称").fill("浏览器测试");
  await page.getByRole("button",{name:"进入工作台"}).click();
  await expect(page.getByLabel("数据集",{exact:true})).toBeVisible();
}

test("text four-view edit, OCR keyboard isolation, save and reload",async({page,browserName})=>{
  const sample=browserName==="chromium"?"000000.png":"000004.png";
  const next=browserName==="chromium"?"000001.png":"000005.png";
  const errors:string[]=[];
  page.on("pageerror",e=>errors.push(e.message));
  await login(page);
  await page.getByLabel("数据集",{exact:true}).selectOption("text");
  await page.getByRole("button",{name:sample}).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await page.getByRole("button",{name:"开始编辑"}).click();
  await page.getByRole("button",{name:"矩形 R",exact:true}).click();
  const box=(await page.getByTestId("canvas-HR").boundingBox())!;
  await page.mouse.move(box.x+box.width*.3,box.y+box.height*.3);
  await page.mouse.down();
  await page.mouse.move(box.x+box.width*.65,box.y+box.height*.6,{steps:8});
  await page.mouse.up();
  await expect(page.locator(".region-list button")).toHaveCount(1);
  await page.getByLabel("OCR 真值").fill("012 text");
  await expect(page.getByLabel("OCR 真值")).toHaveValue("012 text");
  for(const variant of ["LR2","LR3","LR4"]){
    await page.locator(".pane header strong").getByText(variant,{exact:true}).click();
    await page.locator(".evidence-buttons").getByRole("button",{name:"1",exact:true}).click();
  }
  await expect(page.locator(".save-state")).toHaveText("已保存");
  await page.getByRole("button",{name:"确认整组",exact:true}).click();
  await expect(page.locator(".sample-toolbar strong")).toHaveText(next);
  await page.getByRole("button",{name:sample}).click();
  await expect(page.getByLabel("OCR 真值")).not.toBeVisible();
  await page.locator(".region-list button").first().click();
  await expect(page.getByLabel("OCR 真值")).toHaveValue("012 text");
  await page.reload();
  await page.getByRole("button",{name:sample}).click();
  await expect(page.locator(".region-list button")).toHaveCount(1);
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await page.screenshot({path:"test-results/workspace.png",fullPage:true});
  expect(errors).toEqual([]);
});

test("two tabs cannot hold the same sample lease",async({browser})=>{
  const context=await browser.newContext();
  const first=await context.newPage();
  await login(first);
  await first.getByLabel("数据集",{exact:true}).selectOption("face");
  await first.getByRole("button",{name:"000002.png"}).click();
  await first.getByRole("button",{name:"开始编辑"}).click();
  const second=await context.newPage();
  await second.goto("/");
  await second.getByLabel("数据集",{exact:true}).selectOption("face");
  await second.getByRole("button",{name:"000002.png"}).click();
  await second.getByRole("button",{name:"开始编辑"}).click();
  await expect(second.getByRole("alert")).toContainText("占用");
  await expect(first.getByRole("button",{name:"确认整组",exact:true})).toBeEnabled();
  await first.getByRole("button",{name:"结束编辑"}).click();
  await context.close();
});

test("dense sample pan and zoom stays local and retains image resources",async({page,browserName},testInfo)=>{
  await login(page);
  await page.getByLabel("数据集",{exact:true}).selectOption("text");
  await page.getByRole("button",{name:"000010.png"}).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await expect(page.locator(".region-list button")).toHaveCount(357);
  let writes=0;
  page.on("request",request=>{if(request.method()!=="GET" && request.url().includes("/api/"))writes++;});
  const canvas=page.getByTestId("canvas-HR");
  const box=(await canvas.boundingBox())!;
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  const frames=await page.evaluate(async()=>{
    const intervals:number[]=[];let last=performance.now();
    const element=document.querySelector('[data-testid="canvas-HR"] canvas')!;
    for(let i=0;i<220;i++){
      element.dispatchEvent(new WheelEvent("wheel",{deltaY:i%2?-1:1,bubbles:true,cancelable:true,clientX:400,clientY:400}));
      await new Promise<void>(resolve=>requestAnimationFrame(now=>{intervals.push(now-last);last=now;resolve();}));
    }
    return intervals.slice(20).sort((a,b)=>a-b);
  });
  await testInfo.attach("dense-frame-intervals",{body:JSON.stringify({browser:browserName,samples:frames.length,p50:frames[100],p95:frames[190],max:frames.at(-1),measurement:"headless synthetic wheel+requestAnimationFrame; not physical display latency"}),contentType:"application/json"});
  expect(writes).toBe(0);
  await canvas.locator("..").getByRole("button",{name:"放大视图"}).click();
  await expect(page.locator(".pane")).toHaveCount(1);
  await page.getByRole("button",{name:"四视图",exact:true}).click();
  await expect(page.locator(".pane")).toHaveCount(4);
  await page.getByRole("button",{name:"000010.png"}).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
});

test("dense instance list keeps rows readable and scrolls to both ends",async({page})=>{
  await login(page);
  await page.getByLabel("数据集",{exact:true}).selectOption("text");
  await page.getByRole("button",{name:"000010.png"}).click();
  const list=page.locator(".region-list");
  const rows=list.locator("button");
  await expect(rows).toHaveCount(357);
  for(const viewport of [{width:1600,height:1000},{width:1100,height:720}]){
    await page.setViewportSize(viewport);
    const layout=await list.evaluate(element=>{
      const rows=Array.from(element.querySelectorAll("button"));
      return {
        height:element.clientHeight,
        scrollHeight:element.scrollHeight,
        readable:rows.every((row,index)=>{
          const bounds=row.getBoundingClientRect();
          const title=row.querySelector("span")!.getBoundingClientRect();
          const evidence=row.querySelector("small")!.getBoundingClientRect();
          const previous=rows[index-1]?.getBoundingClientRect();
          return title.height>0 && evidence.height>0 &&
            title.top>=bounds.top && title.bottom<=evidence.top &&
            evidence.bottom<=bounds.bottom &&
            (!previous || previous.bottom<bounds.top);
        }),
      };
    });
    expect(layout.readable).toBe(true);
    expect(layout.height).toBeLessThanOrEqual(210);
    expect(layout.scrollHeight).toBeGreaterThan(layout.height);
    await rows.last().scrollIntoViewIfNeeded();
    await rows.last().click();
    await expect(rows.last()).toHaveClass("selected");
    await expect(page.getByLabel("OCR 真值")).toHaveValue("356");
    expect(await list.evaluate(element=>element.scrollTop)).toBeGreaterThan(0);
    await rows.first().scrollIntoViewIfNeeded();
    await rows.first().click();
    await expect(rows.first()).toHaveClass("selected");
    await expect(page.getByLabel("OCR 真值")).toHaveValue("0");
  }
  await page.screenshot({path:"test-results/dense-instance-list.png",fullPage:true});
});

test("face requires explicit HR evidence and preserves geometry-only workflow",async({page,browserName})=>{
  await login(page);
  await page.getByLabel("数据集",{exact:true}).selectOption("face");
  const sample=browserName==="chromium"?"000006.png":"000008.png";
  await page.getByRole("button",{name:sample}).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await page.getByRole("button",{name:"开始编辑"}).click();
  await expect(page.getByRole("button",{name:"四边形 Q",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"矩形 R",exact:true}).click();
  const box=(await page.getByTestId("canvas-HR").boundingBox())!;
  await page.mouse.move(box.x+box.width*.3,box.y+box.height*.3);
  await page.mouse.down();await page.mouse.move(box.x+box.width*.6,box.y+box.height*.6);await page.mouse.up();
  await expect(page.locator(".region-list button")).toHaveCount(1);
  await expect(page.getByLabel("OCR 真值")).not.toBeVisible();
  await expect(page.locator(".region-list button small")).toContainText("— / — / — / —");
  for(const variant of ["HR","LR2","LR3","LR4"]){
    await page.locator(".pane header strong").getByText(variant,{exact:true}).click();
    await page.locator(".evidence-buttons").getByRole("button",{name:variant==="HR"?"0":"1",exact:true}).click();
  }
  await expect(page.locator(".save-state")).toHaveText("已保存");
  await page.getByRole("button",{name:"确认整组",exact:true}).click();
  await expect(page.locator(".sample-toolbar strong")).not.toHaveText(sample);
  await page.getByRole("button",{name:sample}).click();
  await expect(page.locator(".region-list button small")).toHaveText("0 / 1 / 1 / 1");
});

test("geometry dragging and vertex edits are single undoable domain operations",async({page,browserName})=>{
  await login(page);
  await page.getByLabel("数据集",{exact:true}).selectOption("text");
  const name=browserName==="chromium"?"000003.png":"000009.png";
  await page.getByRole("button",{name}).click();
  await expect(page.locator(".loading-image")).toHaveCount(0);
  await page.getByRole("button",{name:"开始编辑"}).click();
  await page.getByRole("button",{name:"矩形 R",exact:true}).click();
  const box=(await page.getByTestId("canvas-HR").boundingBox())!;
  await page.mouse.move(box.x+box.width*.3,box.y+box.height*.3);
  await page.mouse.down();await page.mouse.move(box.x+box.width*.65,box.y+box.height*.6);await page.mouse.up();
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const current=()=>page.evaluate(async(name)=>{const r=await fetch("/api/v1/datasets/text/samples/"+name);return (await r.json()).draft.HR[0];},name);
  const before=await current();
  await page.getByRole("button",{name:"矩形 R",exact:true}).click();
  const scale=Math.min(box.width/800,box.height/600)*.94;
  await page.mouse.move(box.x+box.width*.475,box.y+box.height*.45);
  await page.mouse.down();await page.mouse.move(box.x+box.width*.475+25,box.y+box.height*.45+15,{steps:8});await page.mouse.up();
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const moved=await current();
  expect(moved.region_id).toBe(before.region_id);
  expect(moved.points[0][0]-before.points[0][0]).toBeCloseTo(25/scale,0);
  expect(moved.points[0][1]-before.points[0][1]).toBeCloseTo(15/scale,0);
  const x=box.x+box.width/2-400*scale+moved.points[0][0]*scale;
  const y=box.y+box.height/2-300*scale+moved.points[0][1]*scale;
  await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+10,y+10,{steps:5});await page.mouse.up();
  await expect(page.locator(".save-state")).toHaveText("已保存");
  const resized=await current();
  expect(resized.points[0][0]).toBeGreaterThan(moved.points[0][0]);
  await page.getByRole("button",{name:"撤销",exact:true}).click();
  await expect(page.locator(".save-state")).toHaveText("已保存");
  expect((await current()).points).toEqual(moved.points);
  await page.getByRole("button",{name:"重做",exact:true}).click();
  await expect(page.locator(".save-state")).toHaveText("已保存");
  expect((await current()).points).toEqual(resized.points);
  await page.getByRole("button",{name:"结束编辑"}).click();
});

test("uncached model offers automatic download and shows progress",async({page})=>{
  let loads=0;
  const slot={state:"UNLOADED",generation:0,model_id:null,model_version:null,device:null,queued:0,error:null,providers:[],running_job:null,download:null} as Record<string,unknown>;
  await page.route("**/api/v1/models",route=>route.fulfill({json:[{id:"scrfd",kind:"scrfd",attribute:"face",version:"pending",available:false,downloadable:true,errors:["缺少 model 文件"],required_memory_mb:2048}]}));
  await page.route("**/api/v1/model-slot",async route=>{
    if(route.request().method()==="PUT"){
      loads++;
      expect(route.request().postDataJSON()).toMatchObject({model_id:"scrfd",device:"cpu",generation:0});
      slot.state="DOWNLOADING";slot.generation=1;
      slot.download={model_id:"scrfd",component:"model",filename:"scrfd_10g_bnkps.onnx",file_index:1,files_total:1,bytes_received:1048576,total_bytes:2097152,attempt:1,stage:"downloading"};
    }
    await route.fulfill({json:slot});
  });
  await login(page);
  await page.getByLabel("模型",{exact:true}).selectOption("scrfd");
  await page.getByLabel("设备",{exact:true}).selectOption("cpu");
  expect(loads).toBe(0);
  await expect(page.getByRole("button",{name:"下载并加载",exact:true})).toBeEnabled();
  page.once("dialog",dialog=>dialog.accept());
  await page.getByRole("button",{name:"下载并加载",exact:true}).click();
  await expect(page.getByRole("status",{name:"模型下载进度"})).toContainText("scrfd_10g_bnkps.onnx");
  await expect(page.getByRole("status",{name:"模型下载进度"})).toContainText("1.0 MiB / 2.0 MiB");
  await expect(page.getByRole("button",{name:"下载并加载",exact:true})).toBeDisabled();
  expect(loads).toBe(1);
});

test("remembered owner IP survives cookies and new browser, logout forgets it",async({page,browser})=>{
  // Start unbound even when another test has already verified localhost.
  await login(page);
  await page.getByRole("button",{name:"退出",exact:true}).click();
  await page.goto("/");
  await expect(page.getByLabel("访问凭据")).toBeVisible();
  const {token}=JSON.parse(readFileSync(process.env.REALISR_E2E_TOKEN??"/tmp/realisr-e2e-token","utf8"));
  await page.getByLabel("访问凭据").fill(token);
  await page.getByLabel("显示昵称").fill("IP 记忆测试");
  await page.getByRole("button",{name:"进入工作台"}).click();
  await expect(page.locator(".topbar")).toContainText("IP 记忆测试 · 所有者");
  const firstSession=(await (await page.request.get("/api/v1/session")).json()).session_id;
  await page.context().clearCookies();
  await page.reload();
  await expect(page.locator(".topbar")).toContainText("IP 记忆测试 · 所有者");
  const renewedSession=(await (await page.request.get("/api/v1/session")).json()).session_id;
  expect(renewedSession).not.toBe(firstSession);
  const context=await browser.newContext();
  try {
    const second=await context.newPage();
    await second.goto("/");
    await expect(second.locator(".topbar")).toContainText("IP 记忆测试 · 所有者");
    const independentSession=(await (await second.request.get("/api/v1/session")).json()).session_id;
    expect(independentSession).not.toBe(renewedSession);
    await second.getByRole("button",{name:"退出",exact:true}).click();
    await expect(second.getByLabel("访问凭据")).toBeVisible();
    await second.reload();
    await expect(second.getByLabel("访问凭据")).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("访问凭据")).toBeVisible();
    await page.screenshot({path:"test-results/owner-ip-login.png",fullPage:true});
  } finally { await context.close(); }
});

test("share link on a remembered IP retains its role and revocation",async({page,browser})=>{
  await login(page);
  const response=await page.request.post("/api/v1/shares",{
    headers:{Origin:new URL(page.url()).origin},
    data:{dataset:"text",sample:null,role:"view",expires:null},
  });
  expect(response.ok()).toBeTruthy();
  const share=await response.json();
  const context=await browser.newContext();
  try {
    const visitor=await context.newPage();
    await visitor.goto("/");
    await expect(visitor.locator(".topbar")).toContainText("所有者");
    await visitor.goto(share.url);
    await expect(visitor.getByLabel("访问凭据")).toBeVisible();
    await visitor.getByRole("button",{name:"进入工作台"}).click();
    await expect(visitor.locator(".topbar")).toContainText("可查看");
    await expect(visitor.getByRole("button",{name:"重新扫描数据集"})).toHaveCount(0);
    await visitor.reload();
    await expect(visitor.locator(".topbar")).toContainText("可查看");
    const revoke=await page.request.delete("/api/v1/shares/"+share.id,{headers:{Origin:new URL(page.url()).origin}});
    expect(revoke.ok()).toBeTruthy();
    await visitor.reload();
    await expect(visitor.getByLabel("访问凭据")).toBeVisible();
    await expect(visitor.locator(".topbar")).toHaveCount(0);
    // Even an invalid explicit capability must not fall back to IP login.
    await visitor.goto("/#token="+"invalid".repeat(8));
    await expect(visitor.getByLabel("访问凭据")).toHaveValue("invalid".repeat(8));
    await visitor.getByRole("button",{name:"进入工作台"}).click();
    await expect(visitor.getByRole("alert")).toBeVisible();
    await expect(visitor.locator(".topbar")).toHaveCount(0);
  } finally { await context.close(); }
});

test("session restore reports server failure and supports retry",async({page})=>{
  await login(page);
  await page.context().clearCookies();
  let attempts=0;
  await page.route("**/api/v1/session/restore",async route=>{
    attempts++;
    if(attempts===1)await route.fulfill({status:503,contentType:"application/json",body:JSON.stringify({error:{code:"storage_unavailable",message:"存储暂不可用，请重试"}})});
    else await route.continue();
  });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("存储暂不可用");
  await page.getByRole("button",{name:"重试恢复会话"}).click();
  await expect(page.locator(".topbar")).toContainText("所有者");
  expect(attempts).toBe(2);
});
