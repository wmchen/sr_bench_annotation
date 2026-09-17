import { uuid } from "./state/id";
import { useEffect, useRef, useState } from "react";
import {
  api, ApiError, samplePath, variants, type DraftRequest, type SaveAnnotationsRequest, type InferenceRequest, type Dataset, type Group, type Job,
  type Lease, type ModelInfo, type Sample, type SampleItem, type Session,
  type Slot, type Variant, type OpeningSelection,
} from "./api/client";
import { convert, evidence, recoverability, syncHR, typing } from "./state/domain";
import { recovery, type Recovery } from "./state/recovery";
import { ImageCache } from "./state/imageCache";
import { needsWriteback } from "./features/workspace/sourceSync";
import { selectRegion, type RegionSelection } from "./features/workspace/selection";
import { SaveQueue, type SaveStatus } from "./state/saveQueue";
import { WorkspaceLayout } from "./features/layout/WorkspaceLayout";
import { Workspace, type Mode } from "./features/workspace/Workspace";
import { InferencePanel } from "./features/inference/InferencePanel";
import { DatasetStatisticsPanel } from "./features/statistics/DatasetStatisticsPanel";
import { SharingPanel } from "./features/sharing/SharingPanel";

const emptyGroup = (): Group => ({HR:[],LR2:[],LR3:[],LR4:[]});
const statusText = {saved:"草稿已自动保存",pending:"草稿待自动保存",saving:"草稿自动保存中…",failed:"草稿自动保存失败"};

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
  const openingAttempt = useRef<{key:string;target:string|null} | null>(null);
  const [opening,setOpening] = useState<"idle"|"loading"|"empty"|"failed">("idle");
  const [openingError,setOpeningError] = useState("");
  const [items,setItems] = useState<SampleItem[]>([]);
  const [search,setSearch] = useState("");
  const [page,setPage] = useState(0);
  const [total,setTotal] = useState(0);
  const [sample,setSampleState] = useState<Sample | null>(null);
  const sampleRef = useRef<Sample | null>(null);
  const [group,setGroupState] = useState<Group>(emptyGroup());
  const groupRef = useRef<Group>(emptyGroup());
  const [selection,setSelection] = useState<RegionSelection>({ids:[],anchor:null});
  const selected = selection.ids;
  const [active,setActive] = useState<Variant>("HR");
  const [mode,setMode] = useState<Mode>("select");
  const [focus,setFocus] = useState(0);
  const [lease,setLeaseState] = useState<Lease | null>(null);
  const leaseRef = useRef<Lease | null>(null);
  const [leaseHealthy,setLeaseHealthy] = useState(true);
  const [status,setStatus] = useState<SaveStatus>("saved");
  const [savedDraft,setSavedDraft] = useState(false);
  const [localRecovery,setLocalRecovery] = useState<Recovery | null>(null);
  const [imageNavigation,setImageNavigation] = useState(0);
  const [images,setImages] = useState<Partial<Record<Variant,ImageBitmap>>>({});
  const [slot,setSlot] = useState<Slot | null>(null);
  const [jobs,setJobs] = useState<Job[]>([]);
  const [refreshCount,setRefreshCount] = useState(0);
  const [statisticsAllowed,setStatisticsAllowed] = useState(true);
  const [connection,setConnection] = useState("连接中");
  const [working,setWorking] = useState(false);
  const [conversion,setConversion] = useState(false);
  const [corner,setCorner] = useState(0);
  const [clockwise,setClockwise] = useState(true);
  const [sourceConflict,setSourceConflict] = useState<string | null>(null);
  const [publicationUncertain,setPublicationUncertain] = useState(false);
  const publication = useRef<{path:string;body:SaveAnnotationsRequest} | null>(null);
  const publishing = useRef(false);
  const lastSaveFailed = useRef(false);
  const sourceBaseline = useRef("");
  const tab = useRef(uuid());
  const queue = useRef<SaveQueue | null>(null);
  const cache = useRef(new ImageCache());
  const history = useRef<{past:Group[];future:Group[]}>({past:[],future:[]});
  const navigation = useRef(0);
  const renewalDeadline = useRef(0);
  const releasing = useRef<Promise<void> | null>(null);

  useEffect(()=>()=>{navigation.current++;queue.current?.dispose();cache.current.clear();},[]);
  useEffect(()=>()=>{navigation.current++;openingAttempt.current=null;},[user?.session_id,dataset]);

  function setSample(value: Sample | null) {
    const old=sampleRef.current;
    if(!value || !old || old.dataset!==value.dataset || old.id!==value.id || old.image_version!==value.image_version){
      setImages({});sourceBaseline.current=value?.source_token??"";setSourceConflict(null);
    }
    sampleRef.current=value;setSampleState(value);
  }
  function setGroup(value: Group) { groupRef.current=value;setGroupState(value); }
  /** Keep list range selection anchored to the last canvas click or new shape. */
  function setSelected(ids: string[], anchor: string | null = ids.at(-1) ?? null): void {
    setSelection({ids,anchor});
  }
  function setLease(value: Lease | null) { leaseRef.current=value;setLeaseState(value); }
  function onError(value: unknown) {
    setError(value instanceof Error?value.message:String(value));
    if(value instanceof ApiError && ["unauthorized","lease_lost"].includes(value.code)) {
      setLeaseHealthy(false);
      if(value.code==="unauthorized"){setStatisticsAllowed(false);setImages({});cache.current.clear();}
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
      setStatisticsAllowed(true);
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
        .catch(value=>{if(!cancelled)onError(value);});
    },150);
    return()=>{cancelled=true;clearTimeout(timer);};
  },[dataset,search,page,refreshCount,user]);

  const datasetStatus=datasets.find(d=>d.id===dataset)?.status;
  useEffect(()=>{
    if(!user || !dataset || !datasetStatus || datasetStatus==="scanning")return;
    const key=JSON.stringify([user.session_id,dataset]);
    if(openingAttempt.current?.key===key)return;
    openingAttempt.current={key,target:initialSample.current};
    initialSample.current=null;
    void openInitial();
  },[user?.session_id,dataset,datasetStatus]);

  /** Select once per dataset entry; list and event refreshes do not navigate. */
  async function openInitial() {
    const target=openingAttempt.current?.target;
    const request=++navigation.current;
    setOpening("loading");setOpeningError("");
    try {
      const suffix=target==null?"":"?sample="+encodeURIComponent(target);
      const selection=await api<OpeningSelection>("/datasets/"+encodeURIComponent(dataset)+"/opening-selection"+suffix);
      if(request!==navigation.current)return;
      if(selection.sample===null){setOpening("empty");return;}
      await release();
      if(request!==navigation.current)return;
      await loadSample(dataset,selection.sample,request,selection.pending_draft,selection.index);
    } catch(value) { openingFailed(value,request); }
  }

  function openingFailed(value:unknown,request:number) {
    if(request!==navigation.current)return;
    setOpening("failed");
    setOpeningError(value instanceof Error?value.message:String(value));
  }

  function resetOpening() {
    navigation.current++;openingAttempt.current=null;initialSample.current=null;
    setOpening("idle");setOpeningError("");
  }

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
    if(publication.current)throw new Error("请先重试保存标注，确认上次写回结果后再离开。");
    if(releasing.current)return releasing.current;
    const current=sampleRef.current,currentLease=leaseRef.current,currentQueue=queue.current;
    if(!currentLease || !current)return;
    const pending=(async()=>{
      await currentQueue?.flush();
      await api(samplePath(current.dataset,current.id)+"/lease","DELETE",{tab_id:tab.current,lease_id:currentLease.id});
      if(leaseRef.current?.id!==currentLease.id)return;
      currentQueue?.dispose();queue.current=null;
      setLease(null);setLeaseHealthy(true);
    })();
    releasing.current=pending;
    try { await pending; } finally { if(releasing.current===pending)releasing.current=null; }
  }
  /** Open a user-selected sample and supersede any pending automatic choice. */
  async function openSample(id:string) {
    const request=++navigation.current;
    openingAttempt.current={key:JSON.stringify([user?.session_id,dataset]),target:id};
    setOpening("loading");setOpeningError("");
    try {
      await release();
      if(request!==navigation.current)return;
      await loadSample(dataset,id,request);
    } catch(value) { openingFailed(value,request); }
  }

  /** Apply detail and local recovery only while this navigation is current. */
  async function loadSample(datasetId:string,id:string,request:number,preferDraft=false,index:number|null=null) {
    const result=await api<Sample>(samplePath(datasetId,id));
    if(request!==navigation.current)return;
    const local=await recovery(recoveryKey(result)).catch(()=>undefined);
    if(request!==navigation.current)return;
    window.history.replaceState(null,"",location.pathname+"?dataset="+encodeURIComponent(result.dataset)+"&sample="+encodeURIComponent(result.id));
    const showDraft=preferDraft || result.source_dirty || result.formal===null;
    sourceBaseline.current=result.source_token;
    setSample(result);setGroup(showDraft?result.draft:result.formal!);
    setSavedDraft(showDraft);setSelected([]);setMode("select");setActive("HR");
    setStatus("saved");
    setLocalRecovery(local && JSON.stringify(local.group)!==JSON.stringify(result.draft)?local:null);
    history.current={past:[],future:[]};
    if(index!==null){setSearch("");setPage(Math.floor(index/50));}
    if(openingAttempt.current)openingAttempt.current.target=result.id;
    setImageNavigation(request);
  }
  useEffect(()=>{
    if(!sample)return;
    let cancelled=false;
    const current=sample,request=imageNavigation;
    cache.current.pin(current);
    void Promise.all(variants.map(async v=>{
      const bitmap=await cache.current.load(current,v);
      if(!cancelled && request===navigation.current && bitmap)setImages(previous=>({...previous,[v]:bitmap}));
    })).then(()=>{
      if(cancelled || request!==navigation.current)return;
      setOpening("idle");
      const position=items.findIndex(i=>i.id===current.id);
      const next=position>=0?items[position+1]:undefined;
      if(next){
        // Speculative loading must not turn a successful opening into an error.
        void api<Sample>(samplePath(current.dataset,next.id)).then(async metadata=>{
          if(!cancelled && request===navigation.current)await Promise.all(variants.map(v=>cache.current.load(metadata,v,true)));
        }).catch(()=>undefined);
      }
    }).catch(value=>{if(!cancelled)openingFailed(value,request);});
    return()=>{cancelled=true;};
  },[sample?.dataset,sample?.id,sample?.image_version,imageNavigation]);

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
      if(publication.current || (queue.current && queue.current.sequence!==queue.current.acknowledged)){event.preventDefault();event.returnValue="";}
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
        if(cancelled)return;
        const list=await api<Dataset[]>("/datasets");
        if(cancelled)return;
        setDatasets(list);
        setRefreshCount(c=>c+1);
        const current=sampleRef.current,request=navigation.current;
        if(current){
          const latest=await api<Sample>(samplePath(current.dataset,current.id));
          if(cancelled || request!==navigation.current || sampleRef.current?.id!==current.id || sampleRef.current?.dataset!==current.dataset)return;
          if(publishing.current || publication.current)return;
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
          }else setSample(latest);
        }
      }catch(value){if(!cancelled)onError(value);}finally{busy=false;if(queuedSync){queuedSync=false;void sync();}}
    }
    const stream=new EventSource("/api/v1/events");
    stream.onopen=()=>{setConnection("已连接");void sync();};
    stream.addEventListener("update",()=>void sync());
    stream.addEventListener("shutdown",()=>{cancelled=true;stream.close();clearInterval(poll);});
    stream.addEventListener("revoked",()=>{setStatisticsAllowed(false);setConnection("权限已失效");setLeaseHealthy(false);setError("分享已撤销或到期，请保留本地内容。");stream.close();cache.current.clear();setImages({});});
    stream.onerror=()=>setConnection("连接中断，正在轮询");
    const poll=setInterval(()=>void sync(),5000);
    return()=>{cancelled=true;stream.close();clearInterval(poll);};
  },[user,savedDraft]);

  const editable=!!lease && leaseHealthy && !publicationUncertain && !working && opening!=="loading" && variants.every(v=>!!images[v]);
  const sourceDirty=!!sample && needsWriteback(sample,lease?group:sample.draft);
  const canSave=!!sample && user?.role!=="view" && currentDatasetReady() && !working && opening!=="loading" &&
    (!!publication.current || (sourceDirty && !!sample.source_token && (!lease || leaseHealthy) && (!sample.occupancy || !!lease)));
  function currentDatasetReady(){return datasets.find(d=>d.id===dataset)?.status==="ready";}
  function change(next:Group,recordHistory=true) {
    if(!leaseRef.current || !leaseHealthy || publishing.current || publication.current)return;
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
      else if((event.ctrlKey||event.metaKey)&&event.key==="s"){event.preventDefault();if(canSave)void run(()=>saveAnnotations());}
      else if(["0","1","2"].includes(event.key)){event.preventDefault();setEvidence(Number(event.key));}
      else if(["Delete","Backspace"].includes(event.key) && editable){event.preventDefault();remove();}
      else if(event.key.toLowerCase()==="f")setFocus(f=>f+1);
      else if(event.key==="Escape"){setMode("select");setSelected([]);}
      else if(event.key.toLowerCase()==="r" && editable)setMode("rectangle");
      else if(event.key.toLowerCase()==="q" && editable && sample?.attribute==="text")setMode("quadrilateral");
    };
    window.addEventListener("keydown",key);
    return()=>window.removeEventListener("keydown",key);
  },[group,selected,active,editable,sample,canSave]);
  async function reloadSource() {
    if(!sampleRef.current)return;
    const latest=await api<Sample>(samplePath(sampleRef.current.dataset,sampleRef.current.id));
    sourceBaseline.current=latest.source_token;setSample(latest);setSourceConflict(null);
    if(!leaseRef.current){setGroup(latest.draft);setSavedDraft(true);}
  }
  async function saveAnnotations(overrideToken?:string) {
    if(publishing.current || !sampleRef.current || user?.role==="view")return;
    publishing.current=true;lastSaveFailed.current=true;
    let temporary:Lease | null=null;
    const current=sampleRef.current;
    const path=samplePath(current.dataset,current.id);
    let result:Sample | null=null;
    try {
      if(!publication.current){
        await queue.current?.flush();
        const latest=sampleRef.current!;
        if(!needsWriteback(latest,leaseRef.current?groupRef.current:latest.draft))return;
        if(!leaseRef.current){setGroup(latest.draft);setSavedDraft(true);}
        const draft=latest.draft;
        const empty=!draft.HR.length;
        if(empty && !window.confirm("确认此组没有需要标注的文本或人脸？"))return;
        const violations=latest.validation.violations.length>0;
        if(violations && !window.confirm("可恢复度不满足 HR ≤ LR2 ≤ LR3 ≤ LR4。确认继续保存？"))return;
        const activeLease=leaseRef.current ?? (temporary=await api<Lease>(path+"/lease","POST",{tab_id:tab.current}));
        publication.current={path,body:{
          tab_id:tab.current,lease_id:activeLease.id,lease_generation:activeLease.generation,
          base_revision:latest.revision,operation_id:uuid(),image_version:latest.image_version,
          source_token:overrideToken??sourceBaseline.current,
          confirm_empty:empty,confirm_monotonic:violations,
        }};
      }
      const pending=publication.current;
      try {
        result=await api<Sample>(pending.path+"/save-annotations","POST",pending.body);
      } catch(value) {
        // A lost response may already have committed. Replay its exact request.
        if(value instanceof ApiError){publication.current=null;setPublicationUncertain(false);}
        else setPublicationUncertain(true);
        if(value instanceof ApiError && value.code==="annotation_source_changed"){
          const details=value.details as {source_token?:string}|undefined;
          setSourceConflict(details?.source_token??"");
          return;
        }
        throw value;
      }
      publication.current=null;lastSaveFailed.current=false;setPublicationUncertain(false);setSourceConflict(null);
      sourceBaseline.current=result.source_token;
      setSample(result);setGroup(result.draft);setSavedDraft(true);
      if(leaseRef.current)installQueue(result,leaseRef.current);
      await recovery(recoveryKey(result),null);
    } finally {
      try {
        if(temporary)await api(path+"/lease","DELETE",{tab_id:tab.current,lease_id:temporary.id});
      } finally {publishing.current=false;}
    }
    if(result){await refreshLists();await navigate(1,true);}
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
      <button title={user.role==="owner"?"退出并取消当前 IP 的免登录绑定":undefined} onClick={()=>void run(async()=>{resetOpening();await release();await api("/session","DELETE");setUser(null);setSample(null);setGroup(emptyGroup());setSelected([]);setLocalRecovery(null);setDatasets([]);cache.current.clear();})}>退出</button></div>
    </header>
    {error && <div className="error-banner" role="alert"><span>{error}</span>
      <button onClick={()=>void run(()=>publication.current||lastSaveFailed.current?saveAnnotations():queue.current?.flush()??Promise.resolve())}>重试保存</button>
      <button onClick={downloadLocal}>下载本地内容</button>
      {sample && <button disabled={publicationUncertain} onClick={()=>{if(window.confirm("重新读取服务器版本？当前本地内容会保留为恢复副本，建议先下载核对。"))void run(async()=>{
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
    <WorkspaceLayout navigation={
      <aside className="navigation">
        <span className="eyebrow">数据集</span>
        <select aria-label="数据集" value={dataset} disabled={working} onChange={e=>{const next=e.target.value;void run(async()=>{resetOpening();await release();setSample(null);setGroup(emptyGroup());setSelected([]);setLocalRecovery(null);setItems([]);setTotal(0);setDataset(next);setPage(0);setSearch("");window.history.replaceState(null,"",location.pathname+"?dataset="+encodeURIComponent(next));});}}>
          {datasets.map(d=><option key={d.id} value={d.id}>{d.attribute==="text"?"文本":"人脸"} · {d.id}</option>)}
        </select>
        <div className="progress"><strong>{currentDataset?.complete??0}</strong><span> / {currentDataset?.total??0} 组已完成</span></div>
        <progress value={currentDataset?.complete??0} max={currentDataset?.total??1}/>
        {currentDataset?.status!=="ready" && <p className="error-text">数据集尚未通过校验</p>}
        {currentDataset?.errors.map((e,i)=><small className="error-text" key={i}>{e.sample} {e.message}</small>)}
        {user.role==="owner" && <button disabled={working || currentDataset?.status==="scanning"} onClick={()=>void run(async()=>{await release();await api("/datasets/"+encodeURIComponent(dataset)+"/scan","POST");await refreshLists();})}>重新扫描数据集</button>}
        <input aria-label="搜索文件名" placeholder="搜索文件名…" value={search} onChange={e=>{setSearch(e.target.value);setPage(0);}}/>
        <div className="sample-list">{items.map(item=><button key={item.id} className={sample?.id===item.id?"selected":""} disabled={working} onClick={()=>void openSample(item.id)}>
          <span>{item.complete?"●":"○"}</span><span>{item.id}</span>
        </button>)}</div>
        <div className="button-row pagination"><button disabled={page===0} onClick={()=>setPage(p=>p-1)}>上一页</button><span>{page+1} / {Math.max(1,Math.ceil(total/50))}</span><button disabled={(page+1)*50>=total} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
      </aside>
    } inspector={
      <aside className="inspector">
        <DatasetStatisticsPanel key={JSON.stringify([user.session_id,dataset])} dataset={dataset} refresh={refreshCount} localPending={status!=="saved" || !!localRecovery} enabled={statisticsAllowed}/>
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
          <div className="region-list">{group.HR.map((r,i)=><button key={r.region_id} className={selected.includes(r.region_id)?"selected":""} onClick={e=>setSelection(previous=>selectRegion(previous,group.HR.map(region=>region.region_id),r.region_id,e))}>
            <span>{i+1}. {r.description||r.label}</span><small>{variants.map(v=>group[v].find(other=>other.region_id===r.region_id)?.recoverable??"—").join(" / ")}</small>
          </button>)}</div>
        </section>
        {user.role!=="view" && <InferencePanel slot={slot} jobs={jobs} editable={editable} attribute={sample?.attribute} selected={selected} empty={!group.HR.length}
          onRefresh={refreshModels} onInfer={infer} onError={onError}/>}
        {user.role==="owner" && <SharingPanel dataset={dataset} sample={sample?.id} refresh={refreshCount} onError={onError}/>}
      </aside>
    }>
      <main className="editor">
        {opening==="loading" && <p role="status">正在打开样本…</p>}
        {opening==="failed" && <div role="alert" className="error-banner"><span>{openingError}</span><button disabled={working} onClick={()=>void openInitial()}>重试打开</button></div>}
        {!sample?<div className="empty-state"><h2>{datasetStatus==="scanning"?"正在扫描数据集…":opening==="empty"?"暂无可用样本":opening==="loading"?"正在加载四视图…":"选择一组样本开始"}</h2><p>先查看图像，再申请编辑权。HR 标注会同步到三个 LR 视图。</p></div>:<>
          <div className="sample-toolbar">
            <div><strong>{sample.id}</strong><span className={"save-state "+status}>{lease?statusText[status]:savedDraft?"已保存草稿":"正式结果"}</span>
              <span>{publicationUncertain?"写回结果待确认":sourceDirty?"标注待写回":"标注已写回"}</span>
              {lease && !leaseHealthy && <span className="error-text">编辑已暂停</span>}
              {!lease && sample.occupancy && <span>{sample.occupancy.nickname} 正在编辑</span>}
            </div>
            <div className="button-row"><button disabled={working} onClick={()=>void run(()=>navigate(-1))}>上一组</button>
              <button disabled={working} onClick={()=>void run(()=>navigate(1))}>下一组</button>
              <button disabled={working} onClick={()=>void run(()=>navigate(1,true))}>下一未完成</button>
              {user.role!=="view" && (!lease || !leaseHealthy)?<button className="primary" disabled={working || publicationUncertain || opening==="loading" || currentDataset?.status!=="ready"} onClick={()=>void run(beginEdit)}>开始编辑</button>:null}
              {lease && <button disabled={working || publicationUncertain} onClick={()=>void run(release)}>结束编辑</button>}
              <button className="primary" disabled={!canSave} title={user.role==="view"?"只读分享无写入权限":sample.occupancy&&!lease?"样本正在由其他会话编辑":"将当前草稿保存到数据 root"} onClick={()=>void run(()=>saveAnnotations())}>保存标注</button>
            </div>
          </div>
          {sample.source_error && <div role="alert" className="error-banner">{sample.source_error}</div>}
          {sourceConflict!==null && <div role="alert" className="recovery-banner">
            磁盘标注已被修改。重新读取会刷新磁盘状态并保留当前草稿；覆盖会将当前草稿写入磁盘。
            <button disabled={working} onClick={()=>void run(reloadSource)}>重新读取</button>
            <button disabled={working || !sourceConflict} onClick={()=>void run(()=>saveAnnotations(sourceConflict))}>用当前草稿覆盖</button>
          </div>}
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
          <footer className="editor-footer"><span>滚轮缩放 · 框外左键平移 · 空格 + 左键强制平移 · Ctrl/Cmd + 单击多选 · 列表 Shift + 单击范围选择 · 原始 PNG · 放大无平滑插值</span><span>{group.HR.length} 个区域 · {active}</span></footer>
        </>}
      </main>
    </WorkspaceLayout>
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
