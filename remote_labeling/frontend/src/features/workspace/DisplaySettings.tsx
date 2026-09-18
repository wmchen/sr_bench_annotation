import { crosshairDash, widthLimits, type CrosshairStyle, type DisplayPreferences } from "./displayPreferences";

interface Props {
  value: DisplayPreferences;
  onChange: (value: DisplayPreferences) => void;
}

/** Personal drawing appearance controls shared by all four views. */
export function DisplaySettings({ value, onChange }: Props) {
  return <section className="panel drawing-display" aria-labelledby="drawing-display-title">
    <h3 id="drawing-display-title">绘图显示</h3>
    {(["regionWidth", "crosshairWidth"] as const).map(key => <label key={key} htmlFor={key}>
      {key === "regionWidth" ? "实例边线宽度" : "十字线宽度"}
      <span className="display-slider-row">
        <output htmlFor={key}>{value[key]} px</output>
        <input id={key} type="range" {...widthLimits[key]} value={value[key]}
          aria-valuetext={`${value[key]} px`}
          onChange={event => onChange({ ...value, [key]: Number(event.target.value) })}/>
      </span>
    </label>)}
    <p className="display-hint">选中框额外加粗 1 px</p>
    <label>十字线样式
      <select value={value.crosshairStyle} onChange={event => onChange({ ...value, crosshairStyle: event.target.value as CrosshairStyle })}>
        <option value="solid">实线</option><option value="dashed">虚线</option><option value="dotted">点线</option>
      </select>
    </label>
    <label htmlFor="crosshair-color">十字线颜色</label>
    <div className="display-color-row">
      <input id="crosshair-color" type="color" value={value.crosshairColor}
        onChange={event => onChange({ ...value, crosshairColor: event.target.value })}/>
      <span>{value.crosshairColor.toUpperCase()}</span>
      <svg viewBox="0 0 72 24" role="img" aria-label="十字线样式预览">
        <path d="M 4 12 H 68" fill="none" stroke={value.crosshairColor} strokeWidth={value.crosshairWidth}
          strokeDasharray={crosshairDash(value.crosshairStyle, value.crosshairWidth).join(" ")}/>
      </svg>
    </div>
    <p className="display-hint">十字线仅在 HR 绘图时显示 · 设置自动记忆</p>
  </section>;
}
