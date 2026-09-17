import { useEffect, useRef, useState } from "react";
import { api, variants, type DatasetStatistics } from "../../api/client";
import { initialStatistics, StatisticsResource } from "./statisticsResource";

interface Props { dataset: string; refresh: number; localPending: boolean; enabled: boolean }
const number = (value: number): string => value.toLocaleString("zh-CN");
const percent = (value: number, total: number): string => total ? `${(value / total * 100).toFixed(1)}%` : "—";
const evidence = [
  ["sufficient", "充分"], ["ambiguous", "模糊"], ["insufficient", "不足"], ["unset", "未设置"],
] as const;

/** The parent keys this panel by session and dataset to isolate response state. */
export function DatasetStatisticsPanel({ dataset, refresh, localPending, enabled }: Props) {
  const [state, setState] = useState(initialStatistics);
  const resource = useRef<StatisticsResource | null>(null);
  useEffect(() => {
    if (!enabled || !dataset) { setState(initialStatistics); return; }
    const current = new StatisticsResource(
      () => api<DatasetStatistics>(`/datasets/${encodeURIComponent(dataset)}/statistics`), setState,
    );
    resource.current = current;
    return () => { current.dispose(); resource.current = null; };
  }, [dataset, enabled]);
  useEffect(() => { void resource.current?.refresh(); }, [dataset, enabled, refresh]);

  const stats = enabled ? state.data : null;
  return <section className="panel dataset-statistics" aria-label="数据集统计">
    <h3>数据集统计 <small>{stats ? stats.attribute === "text" ? "文本" : "人脸" : ""}</small></h3>
    {!enabled ? <p className="subtle">统计访问权限已失效</p> : !dataset ? <p className="subtle">请先选择数据集</p> : <>
      {stats ? <>
        <div className="statistics-scope">{stats.scope === "sample" ? "当前分享范围" : "全部可访问样本"}</div>
        {stats.status !== "ready" && <p className="statistics-notice" role="status">{stats.status === "scanning" ? "扫描中 · 统计暂未更新" : stats.import_version > 0 ? "校验失败 · 仅显示上次成功导入的统计" : "校验失败 · 暂无成功导入数据"}</p>}
        <dl className="statistics-counts">
          <div><dt>样本组</dt><dd data-testid="statistics-groups">{number(stats.sample_groups)}</dd></div>
          <div><dt>图片</dt><dd>{number(stats.image_files)}</dd></div>
          <div><dt>{stats.attribute === "text" ? "文本区域" : "人脸区域"}</dt><dd data-testid="statistics-regions">{number(stats.instances)}</dd></div>
          <div><dt title="HR、LR2、LR3、LR4 均已设置证据值的区域">证据齐全区域</dt><dd>{number(stats.completed_instances)}</dd></div>
        </dl>
        <div className="statistics-progress">
          <div><span>证据赋值</span><span>{number(stats.recoverability_assigned)} / {number(stats.recoverability_total)}</span></div>
          <div><progress aria-label="证据赋值进度" value={stats.recoverability_assigned} max={stats.recoverability_total || 1}/><small>{stats.recoverability_total ? percent(stats.recoverability_assigned, stats.recoverability_total) : "暂无区域"}</small></div>
        </div>
        <div className="statistics-progress" title="当前草稿与正式结果一致且证据齐全；再次编辑后需重新保存标注">
          <div><span>已完成组</span><span data-testid="statistics-complete">{number(stats.complete_samples)} / {number(stats.sample_groups)}</span></div>
          <div><progress aria-label="已完成组进度" value={stats.complete_samples} max={stats.sample_groups || 1}/><small>{percent(stats.complete_samples, stats.sample_groups)}</small></div>
        </div>
        <div className="statistics-pending" title="服务器草稿相对正式快照存在待确认内容；源文件的外部变化需重新扫描后反映"><span>待保存草稿</span><span>{number(stats.pending_samples)} 组</span></div>
        <div className="statistics-status" aria-live="polite">
          <span title={new Date(stats.generated_at * 1000).toLocaleString("zh-CN")}>服务器已保存内容 · {state.loading ? "更新中" : state.error ? "更新失败" : "已更新"}</span>
          {localPending && <span className="statistics-notice">本地修改尚未计入</span>}
        </div>
        <details className="statistics-details"><summary>各倍率详情</summary>
          {variants.map(variant => {
            const counts = stats.by_variant[variant];
            if (!counts) return null;
            return <div className="statistics-variant" key={variant}>
              <div><strong>{variant}</strong><span>已赋值 {number(counts.assigned)} / {number(counts.total)}</span></div>
              <div className="statistics-distribution" aria-hidden="true">{evidence.map(([key]) => <span key={key} className={`evidence-${key}`} style={{ width: `${counts.total ? counts[key] / counts.total * 100 : 0}%` }}/>)}</div>
              <dl>{evidence.map(([key, label]) => <div key={key}><dt><i className={`evidence-${key}`}/>{label}</dt><dd>{number(counts[key])}</dd></div>)}</dl>
            </div>;
          })}
        </details>
      </> : state.loading ? <p className="subtle" role="status">正在加载统计…</p> : null}
      {state.error && <div className="statistics-error" role="status"><span>{state.error}{stats ? "，保留上次结果" : ""}</span>
        {!state.denied && <button type="button" onClick={() => void resource.current?.refresh()}>重试统计</button>}
      </div>}
    </>}
  </section>;
}
