import { useEffect, useState } from "react";
import { api, type Job, type ModelInfo, type Slot } from "../../api/client";

interface Devices {
  gpus: {uuid: string; name: string; free_mb: number; utilization: number}[];
  sampled_at: number; error: string | null;
}
interface Props {
  slot: Slot | null; jobs: Job[]; editable: boolean; attribute?: string;
  selected: string[]; empty: boolean; onRefresh: () => Promise<void>;
  onInfer: (recognize: boolean) => Promise<void>; onError: (error: unknown) => void;
}
export function InferencePanel(props: Props) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState("");
  const [device, setDevice] = useState("auto");
  const [devices, setDevices] = useState<Devices | null>(null);
  useEffect(() => {
    api<ModelInfo[]>("/models").then(setModels).catch(props.onError);
  }, [props.slot?.state, props.slot?.generation]);
  useEffect(() => {
    if (props.slot?.state !== "DOWNLOADING") return;
    let pending = false;
    const timer = setInterval(() => {
      if (pending) return;
      pending = true;
      void props.onRefresh().catch(props.onError).finally(() => { pending = false; });
    }, 1000);
    return () => clearInterval(timer);
  }, [props.slot?.state]);
  async function refreshDevices() {
    try { setDevices(await api<Devices>("/devices")); } catch (error) { props.onError(error); }
  }
  const selectedModel = models.find(m=>m.id===model);
  async function control(unload: boolean) {
    if (!props.slot) return;
    if (!window.confirm(unload ? "卸载共享模型？这会影响其他编辑者。" : (selectedModel?.available ? "确认加载所选模型和设备？切换会先卸载当前共享模型。" : "确认下载并加载所选模型？下载在服务器后台进行，切换会先卸载当前共享模型。"))) return;
    try {
      await api("/model-slot", "PUT", {generation: props.slot.generation, model_id: unload ? null : model, device});
      await props.onRefresh();
    } catch (error) { props.onError(error); }
  }
  const slot = props.slot;
  const busy = !slot || !["READY","UNLOADED"].includes(slot.state) || slot.queued > 0;
  const compatible = models.find(m=>m.id===slot?.model_id)?.attribute === props.attribute;
  return <section className="panel">
    <h3>模型辅助</h3>
    <p className="subtle">{({UNLOADED:"未加载",DOWNLOADING:"下载中",LOADING:"加载中",READY:"已就绪",RUNNING:"推理中",UNLOADING:"卸载中",ERROR:"发生错误"} as Record<string,string>)[slot?.state ?? ""] ?? "读取状态…"} · {slot?.device ?? "未加载"} · 排队 {slot?.queued ?? 0}</p>
    {slot?.error && <p className="error-text">{slot.error}</p>}
    {slot?.state === "DOWNLOADING" && slot.download && <div className="download-progress" role="status" aria-label="模型下载进度">
      <p>{slot.download.filename}（{slot.download.file_index}/{slot.download.files_total}）</p>
      <progress aria-label="文件下载进度" max={slot.download.total_bytes ?? 1} value={slot.download.total_bytes ? slot.download.bytes_received : undefined}/>
      <small>{(slot.download.bytes_received/1048576).toFixed(1)} MiB{slot.download.total_bytes ? " / "+(slot.download.total_bytes/1048576).toFixed(1)+" MiB" : ""}
        {slot.download.stage === "retrying" ? " · 正在重试" : slot.download.stage === "verifying" ? " · 校验中" : slot.download.stage === "cached" ? " · 使用缓存" : ""}
        {slot.download.attempt > 1 ? " · 第 "+slot.download.attempt+" 次尝试" : ""}</small>
    </div>}
    <label>模型<select aria-label="模型" value={model} onChange={e=>setModel(e.target.value)}>
      <option value="">选择模型</option>
      {models.map(m=><option key={m.id} value={m.id} disabled={!m.available && !m.downloadable}>{m.id}{m.available?"":"（"+(m.downloadable?"首次使用自动下载":"需配置本地文件")+"）"}</option>)}
    </select></label>
    {models.filter(m=>!m.available && !m.downloadable).map(m=><p className="subtle" key={m.id}>{m.id}：{m.errors.join("；")}</p>)}
    <label>设备<select aria-label="设备" value={device} onChange={e=>setDevice(e.target.value)}>
      <option value="auto">自动 GPU</option><option value="cpu">CPU</option>
      {devices?.gpus.map(g=><option key={g.uuid} value={g.uuid}>{g.name} · 空闲 {g.free_mb} MiB · {g.utilization}%</option>)}
    </select></label>
    <div className="button-row">
      <button onClick={()=>void refreshDevices()}>刷新设备</button>
      <button disabled={busy || !selectedModel || (!selectedModel.available && !selectedModel.downloadable)} onClick={()=>void control(false)}>{selectedModel?.available ? "确认加载" : slot?.error ? "重试下载并加载" : "下载并加载"}</button>
      <button disabled={busy || !slot?.model_id} onClick={()=>void control(true)}>卸载</button>
    </div>
    {devices && <p className="subtle">采集于 {new Date(devices.sampled_at*1000).toLocaleTimeString()}{devices.error ? " · " + devices.error : ""}</p>}
    <div className="button-row">
      <button disabled={!props.editable || !compatible || !props.empty || !["READY","RUNNING"].includes(slot?.state ?? "")} onClick={()=>void props.onInfer(false).catch(props.onError)}>整图检测</button>
      <button disabled={!props.editable || !compatible || props.attribute!=="text" || !props.selected.length || !["READY","RUNNING"].includes(slot?.state ?? "")} onClick={()=>void props.onInfer(true).catch(props.onError)}>识别选中文本</button>
    </div>
    {props.jobs.slice(0,5).map(job=><div className="job" key={job.id}>
      <span>{job.sample} · {job.state}</span>
      {job.error && <small>{job.error}</small>}
      {job.state==="queued" && <button onClick={()=>api("/inference-jobs/"+job.id,"DELETE").then(props.onRefresh).catch(props.onError)}>取消排队</button>}
    </div>)}
  </section>;
}
