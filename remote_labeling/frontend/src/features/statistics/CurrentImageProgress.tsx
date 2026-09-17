import { useMemo } from "react";
import { variants, type Group, type Sample, type Variant } from "../../api/client";
import type { SaveStatus } from "../../state/saveQueue";
import { needsWriteback } from "../workspace/sourceSync";
import { imageProgress } from "./imageProgress";

interface Props {
  sample: Sample | null;
  group: Group;
  active: Variant;
  opening: "idle" | "loading" | "empty" | "failed";
  showDraft: boolean;
  saveStatus: SaveStatus;
}
const number = (value: number): string => value.toLocaleString("zh-CN");
const saveText: Record<SaveStatus, string> = {
  saved: "服务器已保存草稿", pending: "包含本地修改 · 待自动保存",
  saving: "包含本地修改 · 自动保存中", failed: "包含本地修改 · 自动保存失败",
};

/** Show evidence progress for exactly the annotations currently on the canvas. */
export function CurrentImageProgress({ sample, group, active, opening, showDraft, saveStatus }: Props) {
  const progress = useMemo(() => imageProgress(group), [group]);
  if (opening === "loading") return <p className="subtle" role="status">正在加载当前图像…</p>;
  if (opening === "failed") return <p className="subtle" role="status">当前图像加载失败，请重试打开样本。</p>;
  if (!sample || opening === "empty") return <p className="subtle">尚未打开图像</p>;
  const current = progress.byVariant[active];
  const dirty = needsWriteback(sample, group);
  return <div className="current-image-progress" aria-label="当前图像标注进度" role="region">
    <div className="statistics-scope"><span>{sample.attribute === "text" ? "文本" : "人脸"} · 当前 {active}</span><span>{showDraft ? "草稿" : "正式标注"}</span></div>
    <div className="image-progress-filename" title={sample.id}>{sample.id}</div>
    <div className="image-progress-overview">
      <strong data-testid="image-progress-percent">{current.total ? `${(current.assigned / current.total * 100).toFixed(1)}%` : "—"}</strong>
      <span>{active} 证据已赋值 <b data-testid="image-progress-active">{number(current.assigned)} / {number(current.total)}</b></span>
    </div>
    <div className="statistics-progress"><div><progress aria-label="当前图像证据进度" value={current.assigned} max={current.total || 1}/><small>{current.total ? `还差 ${number(current.total - current.assigned)}` : "暂无区域"}</small></div></div>
    <div className="statistics-pending" title="同一区域在 HR、LR2、LR3、LR4 均已赋值"><span>本组证据齐全区域</span><span data-testid="image-progress-regions">{number(progress.completeRegions)} / {number(progress.regions)}</span></div>
    <div className="image-progress-variants" aria-label="当前图像组各倍率进度">
      {variants.map(variant => {
        const value = progress.byVariant[variant];
        return <div key={variant} className={variant === active ? "is-active" : ""} data-testid={`image-progress-${variant}`} aria-current={variant === active ? "true" : undefined}>
          <strong>{variant}</strong><progress aria-label={`${variant} 图像证据进度`} value={value.assigned} max={value.total || 1}/><span>{number(value.assigned)} / {number(value.total)}</span>
        </div>;
      })}
    </div>
    <div className="statistics-status" aria-live="polite">
      <span>{showDraft ? saveText[saveStatus] : "按画布中的正式标注统计"}</span>
      <span className={dirty ? "statistics-notice" : ""}>{dirty ? "标注待写回" : "标注已写回"} · {progress.regions ? progress.assigned === progress.total ? "本组证据已齐全" : `本组还差 ${number(progress.total - progress.assigned)} 项证据` : dirty ? "无区域，需确认后保存空组" : "已保存空组"}</span>
    </div>
  </div>;
}
