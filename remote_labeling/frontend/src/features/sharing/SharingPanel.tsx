import { useEffect, useState } from "react";
import { api } from "../../api/client";

interface Share { id: string; role: string; dataset: string; sample: string | null; revoked: boolean; expires: number | null }
interface ExportInfo { id: string; dataset: string; state: string; error: string | null }
interface Props { dataset: string; sample?: string; refresh: number; onError: (error: unknown)=>void }

export function SharingPanel(props: Props) {
  const [shares,setShares] = useState<Share[]>([]);
  const [exports,setExports] = useState<ExportInfo[]>([]);
  const [role,setRole] = useState("view");
  const [scope,setScope] = useState("dataset");
  const [expires,setExpires] = useState("");
  const [link,setLink] = useState("");
  async function refresh() {
    const [s,e] = await Promise.all([api<Share[]>("/shares"),api<ExportInfo[]>("/exports")]);
    setShares(s); setExports(e);
  }
  useEffect(()=>{ void refresh().catch(props.onError); },[props.refresh]);
  async function create() {
    try {
      const result = await api<{url:string}>("/shares","POST", {
        dataset:props.dataset, sample:scope==="sample"?props.sample:null, role,
        expires:expires?new Date(expires).getTime()/1000:null,
      });
      setLink(result.url); await refresh();
    } catch(error) { props.onError(error); }
  }
  return <section className="panel">
    <h3>分享与导出</h3>
    <label>链接权限<select value={role} onChange={e=>setRole(e.target.value)}>
      <option value="view">可查看</option><option value="edit">可编辑</option>
    </select></label>
    <label>授权范围<select value={scope} onChange={e=>setScope(e.target.value)}>
      <option value="dataset">整个当前数据集</option><option value="sample" disabled={!props.sample}>仅当前样本</option>
    </select></label>
    <label>到期时间（可选）<input type="datetime-local" value={expires} onChange={e=>setExpires(e.target.value)}/></label>
    <button disabled={!props.dataset} onClick={()=>void create()}>创建分享链接</button>
    {link && <div className="share-link"><input aria-label="分享链接" value={link} readOnly onFocus={e=>e.target.select()}/>
      <button onClick={()=>{if(navigator.clipboard)void navigator.clipboard.writeText(link).catch(props.onError);else props.onError(new Error("请选中链接后复制"));}}>复制</button>
    </div>}
    {shares.filter(s=>s.dataset===props.dataset && !s.revoked).map(s=><div className="job" key={s.id}>
      <span>{s.role==="view"?"查看":"编辑"} · {s.sample??"整个数据集"}</span>
      <button onClick={()=>{if(window.confirm("撤销此分享并使已打开的会话失效？"))void api("/shares/"+s.id,"DELETE").then(refresh).catch(props.onError);}}>撤销</button>
    </div>)}
    <button className="export-button" disabled={!props.dataset} onClick={()=>void api("/exports","POST",{dataset:props.dataset}).then(refresh).catch(props.onError)}>导出已确认结果</button>
    {exports.filter(e=>e.dataset===props.dataset).slice(0,5).map(e=><div className="job" key={e.id}>
      <span>导出 · {e.state}</span>{e.error && <small>{e.error}</small>}
      {e.state==="ready" && <a href={"/api/v1/exports/"+e.id+"/download"}>下载 JSON</a>}
    </div>)}
  </section>;
}
