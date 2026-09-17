import { uuid } from "./state/id";
import { useEffect, useRef, useState } from "react";
import {
  api, ApiError, samplePath, variants, type DraftRequest, type CommitRequest, type InferenceRequest, type Dataset, type Group, type Job,
  type Lease, type ModelInfo, type Sample, type SampleItem, type Session,
  type Slot, type Variant,
} from "./api/client";
import { convert, evidence, recoverability, syncHR, typing } from "./state/domain";
import { recovery, type Recovery } from "./state/recovery";
import { ImageCache } from "./state/imageCache";
import { SaveQueue, type SaveStatus } from "./state/saveQueue";
import { Workspace, type Mode } from "./features/workspace/Workspace";
import { InferencePanel } from "./features/inference/InferencePanel";
import { SharingPanel } from "./features/sharing/SharingPanel";

const emptyGroup = (): Group => ({HR:[],LR2:[],LR3:[],LR4:[]});
const statusText = {saved:"已保存",pending:"待保存",saving:"保存中…",failed:"保存失败"};

export function App() {
  const [user,setUser] = useState<Session | null>(null);
  const [checking,setChecking] = useState(true);
  const [restoreFailed,setRestoreFailed] = useState(false);
  const [token,setToken] = useState("");
  const [nickname,setNickname] = useState("");
  const [error,setError] = useState("");
  const [datasets,setDatasets] = useState<Dataset[]>([]);
  const [dataset,setDataset] = useState(new URLSearchParams(location.search).get("dataset") ?? "");
  const initialSample = useRef(new URLSearchParams(location.search).get("sample"));
  const [items,setItems] = useState<SampleItem[]>([]);
  const [search,setSearch] = useState("");
  const [page,setPage] = useState(0);
  const [total,setTotal] = useState(0);
  const [sample,setSampleState] = useState<Sample | null>(null);
  const sampleRef = useRef<Sample | null>(null);
  const [group,setGroupState] = useState<Group>(emptyGroup());
  const groupRef = useRef<Group>(emptyGroup());
  const [selected,setSelected] = useState<string[]>([]);
  const [active,setActive] = useState<Variant>("HR");
  const [mode,setMode] = useState<Mode>("select");
  const [focus,setFocus] = useState(0);
  const [lease,setLeaseState] = useState<Lease | null>(null);
  const leaseRef = useRef<Lease | null>(null);
  const [leaseHealthy,setLeaseHealthy] = useState(true);
  const [status,setStatus] = useState<SaveStatus>("saved");
  const [savedDraft,setSavedDraft] = useState(false);
  const [localRecovery,setLocalRecovery] = useState<Recovery | null>(null);
  const [images,setImages] = useState<Partial<Record<Variant,ImageBitmap>>>({});
  const [slot,setSlot] = useState<Slot | null>(null);
  const [jobs,setJobs] = useState<Job[]>([]);
  const [refreshCount,setRefreshCount] = useState(0);
  const [connection,setConnection] = useState("连接中");
  const [working,setWorking] = useState(false);
  const [conversion,setConversion] = useState(false);
  const [corner,setCorner] = useState(0);
  const [clockwise,setClockwise] = useState(true);
  const tab = useRef(uuid());
  const queue = useRef<SaveQueue | null>(null);
  const cache = useRef(new ImageCache());
  const history = useRef<{past:Group[];future:Group[]}>({past:[],future:[]});
  const navigation = useRef(0);
  const renewalDeadline = useRef(0);

  useEffect(()=>()=>{queue.current?.dispose();cache.current.clear();},[]);

  function setSample(value: Sample | null) {
    const old=sampleRef.current;
    if(!value || !old || old.dataset!==value.dataset || old.id!==value.id || old.image_version!==value.image_version)setImages({});
    sampleRef.current=value;setSampleState(value);
  }
  function setGroup(value: Group) { groupRef.current=value;setGroupState(value); }
  function setLease(value: Lease | null) { leaseRef.current=value;setLeaseState(value); }
  function onError(value: unknown) {
    setError(value instanceof Error?value.message:String(value));
    if(value instanceof ApiError && ["unauthorized","lease_lost"].includes(value.code)) {
      setLeaseHealthy(false);
      if(value.code==="unauthorized"){setImages({});cache.current.clear();}
    }
  }
  async function run(action:()=>Promise<void>) {
    setError("");setWorking(true);
    try { await action(); } catch(value) { onError(value); } finally { setWorking(false); }
  }
  function recoveryKey(s:Sample) { return [user?.session_id,s.dataset,s.id].join("/"); }
  async function refreshLists() {
    const list = await api<Dataset[]>("/datasets");
    setDatasets(list);
    if(!dataset && list.length)setDataset(list[0].id);
    setRefreshCount(c=>c+1);
  }
  async function refreshModels() {
    if(!user || user.role==="view")return;
    const [s,j] = await Promise.all([api<Slot>("/model-slot"),api<Job[]>("/inference-jobs")]);
    setSlot(s);setJobs(j);
  }
  async function restoreAccess() {
    setChecking(true);setRestoreFailed(false);setError("");
    try {
      setUser(await api<Session>("/session/restore","POST"));
    } catch(value) {
      if(!(value instanceof ApiError && value.status===401)) {
        setRestoreFailed(true);
        setError(value instanceof Error?value.message:"无法恢复访问会话，请重试");
      }
    } finally { setChecking(false); }
  }
  useEffect(()=>{
    // Same-document share navigation must also take priority over IP login.
    const shareNavigation=()=>{
      if(new URLSearchParams(location.hash.slice(1)).has("token")) {
        void run(async()=>{await release();window.location.reload();});
      }
    };
    window.addEventListener("hashchange",shareNavigation);
    const fragment = new URLSearchParams(location.hash.slice(1));
    if(fragment.has("token")) {
      setToken(fragment.get("token") ?? "");
      historyReplace();
      setChecking(false);
    } else {
      void restoreAccess();
    }
    return()=>window.removeEventListener("hashchange",shareNavigation);
  },[]);
  function historyReplace() { window.history.replaceState(null,"",location.pathname+location.search); }
  useEffect(()=>{
    if(user) {
      void refreshLists().catch(onError);
      void refreshModels().catch(onError);
    }
  },[user]);
  useEffect(()=>{
    if(!dataset || !user)return;
    let cancelled=false;
    const timer=setTimeout(()=>{
      api<{items:SampleItem[];total:number}>("/datasets/"+encodeURIComponent(dataset)+"/samples?limit=50&offset="+page*50+"&search="+encodeURIComponent(search))
        .then(result=>{if(!cancelled){setItems(result.items);setTotal(result.total);}})
        .catch(onError);
    },150);
    return()=>{cancelled=true;clearTimeout(timer);};
  },[dataset,search,page,refreshCount,user]);

  useEffect(()=>{
    if(user && dataset && datasets.some(d=>d.id===dataset) && initialSample.current){
      const initial=initialSample.current;initialSample.current=null;
      void run(()=>openSample(initial));
    }
  },[user,dataset,datasets]);

  function installQueue(current:Sample, currentLease:Lease) {
    queue.current?.dispose();
    const path=samplePath(current.dataset,current.id);
    queue.current = new SaveQueue(current,
      (edited,revision,operation)=>api<Sample>(path+"/draft","PUT",{
        tab_id:tab.current,lease_id:currentLease.id,lease_generation:currentLease.generation,
        base_revision:revision,operation_id:operation,hr:edited.HR,recoverability:recoverability(edited),
      } satisfies DraftRequest),
      (next,err)=>{setStatus(next);if(err)onError(err);},
      (result,matches)=>{
        if(sampleRef.current?.id!==current.id || sampleRef.current?.dataset!==current.dataset)return;
        setSample(result);
        if(matches)setGroup(result.draft);
        setRefreshCount(c=>c+1);
      },
      (edited,base)=>{void recovery(recoveryKey(current),{group:edited,base,updated:Date.now()}).catch(()=>setError("浏览器本地恢复存储不可用；请保持页面打开直至保存成功"));},
    );
  }
  async function release() {
    const current=sampleRef.current,currentLease=leaseRef.current;
    if(!currentLease || !current)return;
    await queue.current?.flush();
    await api(samplePath(current.dataset,current.id)+"/lease","DELETE",{tab_id:tab.current,lease_id:currentLease.id});
    queue.current?.dispose();queue.current=null;
    setLease(null);setLeaseHealthy(true);
  }
  async function openSample(id:string) {
    await release();
    const request=++navigation.current;
    const result=await api<Sample>(samplePath(dataset,id));
    if(request!==navigation.current)return;
    window.history.replaceState(null,"",location.pathname+"?dataset="+encodeURIComponent(result.dataset)+"&sample="+encodeURIComponent(result.id));
    setSample(result);setGroup(result.formal ?? result.draft);
    setSavedDraft(result.formal===null);setSelected([]);setMode("select");setActive("HR");
    setStatus("saved");setLocalRecovery(null);
    history.current={past:[],future:[]};
    const local=await recovery(recoveryKey(result)).catch(()=>undefined);
    if(local && JSON.stringify(local.group)!==JSON.stringify(result.draft))setLocalRecovery(local);
  }
  useEffect(()=>{
    if(!sample)return;
    let cancelled=false;
    const current=sample;
    cache.current.pin(current);
    void Promise.all(variants.map(async v=>{
      const bitmap=await cache.current.load(current,v);
      if(!cancelled && bitmap)setImages(previous=>({...previous,[v]:bitmap}));
    })).then(async()=>{
      if(cancelled)return;
      const next=items[items.findIndex(i=>i.id===current.id)+1];
      if(next){
        const metadata=await api<Sample>(samplePath(current.dataset,next.id));
        if(!cancelled)await Promise.all(variants.map(v=>cache.current.load(metadata,v,true)));
      }
    }).catch(value=>{if(!cancelled)onError(value);});
    return()=>{cancelled=true;};
  },[sample?.dataset,sample?.id,sample?.image_version]);

  async function beginEdit() {
    if(!sample)return;
    const path=samplePath(sample.dataset,sample.id);
    const requestedAt=performance.now();
    const acquired=await api<Lease>(path+"/lease","POST",{tab_id:tab.current,lease_id:leaseRef.current?.id});
    setLease(acquired);setLeaseHealthy(true);
    renewalDeadline.current=requestedAt+(user!.lease_seconds-5)*1000;
    setLeaseHealthy(performance.now()<renewalDeadline.current);
    const result=await api<Sample>(path);
    if(queue.current && queue.current.sequence!==queue.current.acknowledged){
      const local={group:groupRef.current,base:queue.current.revision,updated:Date.now()};
      setLocalRecovery(local);
    }
    setSample(result);setGroup(result.draft);setSavedDraft(true);
    installQueue(result,acquired);setStatus("saved");
    history.current={past:[],future:[]};
  }
  useEffect(()=>{
    if(!lease || !sample || !user)return;
    let cancelled=false;
    const current=sample, currentLease=lease;
    const renew=async()=>{
      const requestedAt=performance.now();
      if(performance.now()>=renewalDeadline.current)setLeaseHealthy(false);
      try{
        const updated=await api<Lease>(samplePath(current.dataset,current.id)+"/lease","PUT",{tab_id:tab.current,lease_id:currentLease.id});
        if(!cancelled){
          renewalDeadline.current=requestedAt+(user.lease_seconds-5)*1000;
          setLeaseHealthy(performance.now()<renewalDeadline.current);
          leaseRef.current=updated;
        }
      }catch(value){if(!cancelled){setLeaseHealthy(false);onError(value);}}
    };
    const timer=setInterval(()=>void renew(),user.heartbeat_seconds*1000);
    const visibility=()=>{if(!document.hidden)void renew();};
    document.addEventListener("visibilitychange",visibility);
    window.addEventListener("online",visibility);
    return()=>{cancelled=true;clearInterval(timer);document.removeEventListener("visibilitychange",visibility);window.removeEventListener("online",visibility);};
  },[lease?.id,sample?.id,user]);
  useEffect(()=>{
    const prevent=(event:BeforeUnloadEvent)=>{
      if(queue.current && queue.current.sequence!==queue.current.acknowledged){event.preventDefault();event.returnValue="";}
    };
    window.addEventListener("beforeunload",prevent);
    return()=>window.removeEventListener("beforeunload",prevent);
  },[]);

  useEffect(()=>{
    if(!user)return;
    let cancelled=false,busy=false,queuedSync=false;
    async function sync(){
      if(cancelled)return;
      if(busy){queuedSync=true;return;}
      busy=true;
      try{
        await refreshModels();
        setDatasets(await api<Dataset[]>("/datasets"));
        setRefreshCount(c=>c+1);
        const current=sampleRef.current;
        if(current){
          const latest=await api<Sample>(samplePath(current.dataset,current.id));
          if(cancelled || sampleRef.current?.id!==current.id || sampleRef.current?.dataset!==current.dataset)return;
          if(latest.revision!==current.revision || latest.image_version!==current.image_version){
            const q=queue.current;
            if(q && q.sequence!==q.acknowledged){
              if(q.status === "saving") return;
              setError("服务器有新版本；已保留本地未保存内容，请核对后恢复。");
            }else{
              setSample(latest);
              setGroup(leaseRef.current || savedDraft ? latest.draft : latest.formal ?? latest.draft);
              if(leaseRef.current)installQueue(latest,leaseRef.current);
            }
          }else if(!leaseRef.current)setSample(latest);
        }
      }catch(value){onError(value);}finally{busy=false;if(queuedSync){queuedSync=false;void sync();}}
    }
    const stream=new EventSource("/api/v1/events");
    stream.onopen=()=>{setConnection("已连接");void sync();};
    stream.addEventListener("update",()=>void sync());
    stream.addEventListener("shutdown",()=>{cancelled=true;stream.close();clearInterval(poll);});
    stream.addEventListener("revoked",()=>{setConnection("权限已失效");setLeaseHealthy(false);setError("分享已撤销或到期，请保留本地内容。");stream.close();cache.current.clear();setImages({});});
    stream.onerror=()=>setConnection("连接中断，正在轮询");
    const poll=setInterval(()=>void sync(),5000);
    return()=>{cancelled=true;stream.close();clearInterval(poll);};
  },[user,savedDraft]);

  const editable=!!lease && leaseHealthy && !working && variants.every(v=>!!images[v]);
  function change(next:Group,recordHistory=true) {
    if(!leaseRef.current || !leaseHealthy)return;
    if(performance.now()>=renewalDeadline.current){setLeaseHealthy(false);setError("编辑租约需重新确认，当前操作未应用");return;}
    if(recordHistory){
      history.current.past.push(structuredClone(groupRef.current));
      if(history.current.past.length>100)history.current.past.shift();
      history.current.future=[];
    }
    setGroup(next);queue.current?.edit(next);
  }
  function undo(redo=false) {
    if(!editable)return;
    const from=redo?history.current.future:history.current.past;
    const to=redo?history.current.past:history.current.future;
    const next=from.pop();
    if(next){to.push(structuredClone(groupRef.current));change(next,false);}
  }
  function remove() {
    if(!sample || active!=="HR")return;
    change(syncHR(group.HR.filter(r=>!selected.includes(r.region_id) || r.locked),group,sample.dimensions));
    setSelected([]);
  }
  function setEvidence(value:number){if(editable && selected.length)change(evidence(group,active,selected,value));}
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{
      if(typing(event.target))return;
      if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="z"){event.preventDefault();undo(event.shiftKey);}
      else if((event.ctrlKey||event.metaKey)&&event.key==="s"){event.preventDefault();void queue.current?.flush().catch(onError);}
      else if(["0","1","2"].includes(event.key)){event.preventDefault();setEvidence(Number(event.key));}
      else if(["Delete","Backspace"].includes(event.key) && editable){event.preventDefault();remove();}
      else if(event.key.toLowerCase()==="f")setFocus(f=>f+1);
      else if(event.key==="Escape"){setMode("select");setSelected([]);}
      else if(event.key.toLowerCase()==="r" && editable)setMode("rectangle");
      else if(event.key.toLowerCase()==="q" && editable && sample?.attribute==="text")setMode("quadrilateral");
    };
    window.addEventListener("keydown",key);
    return()=>window.removeEventListener("keydown",key);
  },[group,selected,active,editable,sample]);
  async function commit() {
    if(!sample || !leaseRef.current)return;
    await queue.current?.flush();
    const current=sampleRef.current!;
    const values=variants.map(v=>groupRef.current[v]);
    const missing=values.flat().some(r=>r.recoverable==null);
    if(missing)throw new Error("仍有未设置的可恢复度，请补齐四倍率后确认。");
    const empty=!groupRef.current.HR.length;
    if(empty && !window.confirm("确认此组没有需要标注的文本或人脸？"))return;
    const violations=groupRef.current.HR.some((_,i)=>{
      const v=values.map(rows=>rows[i].recoverable!);
      return v.some((n,j)=>j>0 && n<v[j-1]);
    });
    if(violations && !window.confirm("可恢复度不满足 HR ≤ LR2 ≤ LR3 ≤ LR4。确认继续提交？"))return;
    const operation=uuid();
    const result=await api<Sample>(samplePath(current.dataset,current.id)+"/commit","POST",{
      tab_id:tab.current,lease_id:leaseRef.current.id,lease_generation:leaseRef.current.generation,
      base_revision:queue.current!.revision,operation_id:operation,confirm_empty:empty,confirm_monotonic:violations,
    } satisfies CommitRequest);
    setSample(result);setGroup(result.draft);installQueue(result,leaseRef.current);
    await recovery(recoveryKey(result),null);
    await refreshLists();
    await navigate(1,true);
  }
  async function navigate(direction:number,incomplete=false) {
    if(!sample)return;
    const all:SampleItem[]=[];
    for(let offset=0;;offset+=500){
      const part=await api<{items:SampleItem[];total:number}>("/datasets/"+encodeURIComponent(dataset)+"/samples?limit=500&offset="+offset);
      all.push(...part.items);
      if(all.length>=part.total)break;
    }
    const index=all.findIndex(i=>i.id===sample.id);
    for(let next=index+direction;next>=0&&next<all.length;next+=direction){
      if(!incomplete || !all[next].complete){await openSample(all[next].id);return;}
    }
  }
  async function infer(recognize:boolean) {
    if(!sample || !leaseRef.current || !slot?.model_id || !slot.model_version)return;
    await queue.current?.flush();
    await api("/inference-jobs","POST",{
      dataset:sample.dataset,sample:sample.id,image_version:sample.image_version,
      tab_id:tab.current,lease_id:leaseRef.current.id,lease_generation:leaseRef.current.generation,
      base_revision:queue.current!.revision,operation_id:uuid(),
      model_id:slot.model_id,model_version:slot.model_version,slot_generation:slot.generation,
      region_ids:recognize?selected:[],
    } satisfies InferenceRequest);
    await refreshModels();
  }
  function downloadLocal() {
    const blob=new Blob([JSON.stringify({sample:sample?.id,base_revision:queue.current?.revision,group:localRecovery?.group??groupRef.current},null,2)],{type:"application/json"});
    const url=URL.createObjectURL(blob),link=document.createElement("a");
    link.href=url;link.download="local-draft.json";link.click();URL.revokeObjectURL(url);
  }
  if(checking)return <main className="login"><p>正在恢复访问会话…</p></main>;
  if(!user)return <main className="login">
    <div className="login-card"><span className="eyebrow">REAL-ISR / ANNOTATION</span><h1>远程标注工作台</h1>
      <p>在原始像素中判断细节，在四个倍率间保持一致。</p>
      <form onSubmit={e=>{e.preventDefault();void run(async()=>{setUser(await api<Session>("/session","POST",{token,nickname}));setToken("");setRestoreFailed(false);});}}>
        <label>访问凭据<input type="password" required value={token} onChange={e=>setToken(e.target.value)} autoComplete="off"/></label>
        <label>显示昵称<input value={nickname} onChange={e=>setNickname(e.target.value)} placeholder="用于显示样本占用者" maxLength={80}/></label>
        <button className="primary" disabled={working}>进入工作台</button>
      </form>
      <p className="subtle">使用 owner token 验证后将记住当前 IP，同 IP 的浏览器可自动进入。点击“退出”会取消绑定。</p>
      {restoreFailed && <button disabled={working} onClick={()=>void restoreAccess()}>重试恢复会话</button>}
      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
  </main>;
  const currentDataset=datasets.find(d=>d.id===dataset);
  const selectedRegion=group.HR.find(r=>r.region_id===selected[0]);
  return <div className="app">
    <header className="topbar"><div><span className="brand">Real-ISR</span><span className="subtle">远程标注工作台</span></div>
      <div><span className="connection">{connection}</span><span>{user.nickname} · {user.role==="owner"?"所有者":user.role==="edit"?"可编辑":"可查看"}</span>
      <button title={user.role==="owner"?"退出并取消当前 IP 的免登录绑定":undefined} onClick={()=>void run(async()=>{await release();await api("/session","DELETE");setUser(null);setSample(null);cache.current.clear();})}>退出</button></div>
    </header>
    {error && <div className="error-banner" role="alert"><span>{error}</span>
      <button onClick={()=>void run(async()=>{await queue.current?.flush();})}>重试保存</button>
      <button onClick={downloadLocal}>下载本地内容</button>
      {sample && <button onClick={()=>{if(window.confirm("重新读取服务器版本？当前本地内容会保留为恢复副本，建议先下载核对。"))void run(async()=>{
        const current=sampleRef.current!;
        const pending={group:groupRef.current,base:queue.current?.revision??current.revision,updated:Date.now()};
        await recovery(recoveryKey(current),pending);
        const latest=await api<Sample>(samplePath(current.dataset,current.id));
        setSample(latest);setGroup(latest.draft);setSavedDraft(true);setLocalRecovery(pending);
        queue.current?.dispose();queue.current=null;
        if(leaseRef.current&&leaseHealthy)installQueue(latest,leaseRef.current);
        setStatus("saved");
      });}}>重新读取服务器版本</button>}
      <button onClick={()=>setError("")} aria-label="关闭提示">×</button>
    </div>}
    <div className="body">
      <aside className="navigation">
        <span className="eyebrow">数据集</span>
        <select aria-label="数据集" value={dataset} disabled={working} onChange={e=>{const next=e.target.value;void run(async()=>{await release();setSample(null);setDataset(next);setPage(0);setSearch("");window.history.replaceState(null,"",location.pathname+"?dataset="+encodeURIComponent(next));});}}>
          {datasets.map(d=><option key={d.id} value={d.id}>{d.attribute==="text"?"文本":"人脸"} · {d.id}</option>)}
        </select>
        <div className="progress"><strong>{currentDataset?.complete??0}</strong><span> / {currentDataset?.total??0} 组已完成</span></div>
        <progress value={currentDataset?.complete??0} max={currentDataset?.total??1}/>
        {currentDataset?.status!=="ready" && <p className="error-text">数据集尚未通过校验</p>}
        {currentDataset?.errors.map((e,i)=><small className="error-text" key={i}>{e.sample} {e.message}</small>)}
        {user.role==="owner" && <button disabled={working || currentDataset?.status==="scanning"} onClick={()=>void run(async()=>{await release();await api("/datasets/"+encodeURIComponent(dataset)+"/scan","POST");await refreshLists();})}>重新扫描数据集</button>}
        <input aria-label="搜索文件名" placeholder="搜索文件名…" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}}/>
        <div className="sample-list">{items.map(item=><button key={item.id} className={sample?.id===item.id?"selected":""} disabled={working} onClick={()=>void run(()=>openSample(item.id))}>
          <span>{item.complete?"●":"○"}</span><span>{item.id}</span>
        </button>)}</div>
        <div className="button-row pagination"><button disabled={page===0} onClick={()=>setPage(p=>p-1)}>上一页</button><span>{page+1} / {Math.max(1,Math.ceil(total/50))}</span><button disabled={(page+1)*50>=total} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
      </aside>
      <main className="editor">
        {!sample?<div className="empty-state"><h2>选择一组样本开始</h2><p>先查看图像，再申请编辑权。HR 标注会同步到三个 LR 视图。</p></div>:<>
          <div className="sample-toolbar">
            <div><strong>{sample.id}</strong><span className={"save-state "+status}>{lease?statusText[status]:savedDraft?"已保存草稿":"正式结果"}</span>
              {lease && !leaseHealthy && <span className="error-text">编辑已暂停</span>}
              {!lease && sample.occupancy && <span>{sample.occupancy.nickname} 正在编辑</span>}
            </div>
            <div className="button-row"><button disabled={working} onClick={()=>void run(()=>navigate(-1))}>上一组</button>
              <button disabled={working} onClick={()=>void run(()=>navigate(1))}>下一组</button>
              <button disabled={working} onClick={()=>void run(()=>navigate(1,true))}>下一未完成</button>
              {user.role!=="view" && (!lease || !leaseHealthy)?<button className="primary" disabled={working || currentDataset?.status!=="ready"} onClick={()=>void run(beginEdit)}>开始编辑</button>:null}
              {lease && <><button disabled={working} onClick={()=>void run(release)}>结束编辑</button><button className="primary" disabled={!editable} onClick={()=>void run(commit)}>确认整组</button></>}
            </div>
          </div>
          {localRecovery && <div className="recovery-banner">发现本地恢复副本（基础版本 {localRecovery.base}）。
            <button disabled={!editable || localRecovery.base!==sample.revision} onClick={()=>{change(localRecovery.group);setLocalRecovery(null);}}>恢复到当前草稿</button>
            <button onClick={downloadLocal}>下载副本</button>
            <button onClick={()=>{if(window.confirm("放弃本地恢复副本？"))void recovery(recoveryKey(sample),null).then(()=>setLocalRecovery(null));}}>放弃副本</button>
          </div>}
          <div className="drawing-toolbar">
            {(["rectangle","quadrilateral"] as Mode[]).map((m,i)=><button key={m} className={mode===m?"active":""} disabled={!editable || (m==="quadrilateral"&&sample.attribute==="face")} aria-pressed={mode===m} onClick={()=>setMode(current=>current===m?"select":m)}>{["矩形 R","四边形 Q"][i]}</button>)}
            <span className="separator"/><button disabled={!editable||!history.current.past.length} onClick={()=>undo()}>撤销</button><button disabled={!editable||!history.current.future.length} onClick={()=>undo(true)}>重做</button>
            <button disabled={!selected.length} onClick={()=>setFocus(f=>f+1)}>聚焦 F</button>
            <button disabled={!editable||active!=="HR"||!selected.length} onClick={remove}>删除</button>
            {!lease && <label className="inline-label"><input type="checkbox" checked={savedDraft} onChange={e=>{setSavedDraft(e.target.checked);setGroup(e.target.checked?sample.draft:sample.formal??sample.draft);}}/>查看已保存草稿</label>}
          </div>
          {!sample.formal && !lease && <div className="draft-notice">尚无正式结果，当前展示已保存草稿。</div>}
          <Workspace sample={sample} group={group} images={images} editable={editable} selected={selected} mode={mode} active={active} focus={focus}
            onSelect={setSelected} onActive={setActive} onChange={change}/>
          <footer className="editor-footer"><span>滚轮缩放 · 框外左键平移 · 空格 + 左键强制平移 · Shift 多选 · 原始 PNG · 放大无平滑插值</span><span>{group.HR.length} 个区域 · {active}</span></footer>
        </>}
      </main>
      <aside className="inspector">
        <section className="panel"><h3>区域属性 <small>{selected.length ? "已选 "+selected.length : "未选择"}</small></h3>
          <p className="subtle">0 证据充分 · 1 证据模糊 · 2 证据不足</p>
          <div className="evidence-buttons">{[0,1,2].map(value=><button key={value} data-value={value} disabled={!editable||!selected.length} onClick={()=>setEvidence(value)}>{value}</button>)}</div>
          {selectedRegion && <><span className="subtle">当前倍率 {active} · 可恢复度 {group[active].find(r=>r.region_id===selectedRegion.region_id)?.recoverable??"未设置"}</span>
            {sample?.attribute==="text" && <label>OCR 真值<textarea aria-label="OCR 真值" value={selectedRegion.description} readOnly={!editable||active!=="HR"||selected.length!==1}
              onChange={e=>change(syncHR(group.HR.map(r=>r.region_id===selectedRegion.region_id?{...r,description:e.target.value}:r),group,sample.dimensions))}/></label>}
            {sample?.attribute==="text" && <button disabled={!editable||active!=="HR"||selected.length!==1||!!selectedRegion.locked} onClick={()=>{
              if(selectedRegion.shape_type==="rectangle")setConversion(true);
              else change(syncHR(group.HR.map(r=>r===selectedRegion?convert(r,"rectangle"):r),group,sample.dimensions));
            }}>转换为{selectedRegion.shape_type==="rectangle"?"四边形":"矩形"}</button>}
          </>}
          <div className="region-list">{group.HR.map((r,i)=><button key={r.region_id} className={selected.includes(r.region_id)?"selected":""} onClick={e=>setSelected(e.shiftKey?selected.includes(r.region_id)?selected.filter(id=>id!==r.region_id):[...selected,r.region_id]:[r.region_id])}>
            <span>{i+1}. {r.description||r.label}</span><small>{variants.map(v=>group[v].find(other=>other.region_id===r.region_id)?.recoverable??"—").join(" / ")}</small>
          </button>)}</div>
        </section>
        {user.role!=="view" && <InferencePanel slot={slot} jobs={jobs} editable={editable} attribute={sample?.attribute} selected={selected} empty={!group.HR.length}
          onRefresh={refreshModels} onInfer={infer} onError={onError}/>}
        {user.role==="owner" && <SharingPanel dataset={dataset} sample={sample?.id} refresh={refreshCount} onError={onError}/>}
      </aside>
    </div>
    {conversion && <div className="modal-backdrop"><section role="dialog" aria-modal="true" className="modal">
      <h2>矩形转四边形</h2><label>起始顶点<select value={corner} onChange={e=>setCorner(Number(e.target.value))}>
        {["左上","右上","右下","左下"].map((name,i)=><option value={i} key={name}>{name}</option>)}
      </select></label><label>方向<select value={clockwise?"cw":"ccw"} onChange={e=>setClockwise(e.target.value==="cw")}><option value="cw">顺时针</option><option value="ccw">逆时针</option></select></label>
      <div className="button-row"><button onClick={()=>setConversion(false)}>取消</button><button className="primary" disabled={!editable||!selectedRegion} onClick={()=>{
        if(sample&&selectedRegion)change(syncHR(group.HR.map(r=>r===selectedRegion?convert(r,"quadrilateral",corner,clockwise):r),group,sample.dimensions));
        setConversion(false);
      }}>确认转换</button></div>
    </section></div>}
  </div>;
}
