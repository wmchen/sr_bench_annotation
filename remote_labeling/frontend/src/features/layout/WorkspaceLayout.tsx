import {
  useEffect, useLayoutEffect, useRef, useState,
  type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode,
} from "react";
import {
  clampWidth, EDITOR_MIN, fitWidths, HANDLE, limits, RAIL,
  readPreferences, writePreferences, type Side,
} from "./sidebarState";

interface LayoutProps { navigation: ReactNode; inspector: ReactNode; children: ReactNode }
interface SidebarProps {
  side: Side;
  hidden: boolean;
  width: number;
  maximum: number;
  children: ReactNode;
  onHidden: (hidden: boolean) => void;
  onResize: (width: number, commit: boolean) => void;
}

/** Own layout updates without rerendering the application's annotation state. */
export function WorkspaceLayout({ navigation, inspector, children }: LayoutProps) {
  const root = useRef<HTMLDivElement>(null);
  const [preferences, setPreferences] = useState(readPreferences);
  const current = useRef(preferences);
  const [adjustedWidths, setAdjustedWidths] = useState<Record<Side, number> | null>(null);
  const [container, setContainer] = useState(() => Math.max(1050, window.innerWidth));
  const measuredWidth = useRef(container);
  useLayoutEffect(() => {
    const observer = new ResizeObserver(entries => {
      const width = entries[0].contentRect.width;
      if (width === measuredWidth.current) return;
      measuredWidth.current = width;
      setContainer(width);
      setAdjustedWidths(null);
    });
    observer.observe(root.current!);
    return () => observer.disconnect();
  }, []);

  // Explicit drags keep the opposite panel still, even when both preferred
  // widths were constrained. Refit preferences on window resize or docking.
  const widths = adjustedWidths ?? fitWidths(preferences, container);
  function update(side: Side, change: { width?: number; hidden?: boolean }, commit: boolean): void {
    const next = { ...current.current, [side]: { ...current.current[side], ...change } };
    current.current = next;
    setPreferences(next);
    if (commit) writePreferences(next);
  }
  function sidebar(side: Side, content: ReactNode): ReactNode {
    const other = side === "left" ? "right" : "left";
    const occupied = preferences[other].hidden ? RAIL : widths[other] + HANDLE;
    const maximum = preferences[side].hidden ? limits[side].max :
      Math.min(limits[side].max, container - EDITOR_MIN - occupied - HANDLE);
    return <Sidebar side={side} hidden={preferences[side].hidden} width={widths[side]} maximum={maximum}
      onHidden={hidden => { setAdjustedWidths(null); update(side, { hidden }, true); }}
      onResize={(width, commit) => {
        setAdjustedWidths({ ...widths, [side]: width });
        update(side, { width }, commit);
      }}>{content}</Sidebar>;
  }
  return <div className="body" ref={root}>
    {sidebar("left", navigation)}
    {children}
    {sidebar("right", inspector)}
  </div>;
}

/** A mounted panel that can dock, auto-hide, or temporarily cover the canvas. */
function Sidebar({ side, hidden, width, maximum, children, onHidden, onResize }: SidebarProps) {
  const root = useRef<HTMLDivElement>(null);
  const handle = useRef<HTMLDivElement>(null);
  const rail = useRef<HTMLButtonElement>(null);
  const pin = useRef<HTMLButtonElement>(null);
  const [peek, setPeek] = useState(false);
  const [dragging, setDragging] = useState(false);
  const hovered = useRef(false);
  const blocked = useRef(false);
  const restoreFocus = useRef(false);
  const pressed = useRef(false);
  const keyboard = useRef(false);
  const opening = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const closing = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const frame = useRef(0);
  const drag = useRef<{ id: number; x: number; width: number; latest: number } | null>(null);
  const label = side === "left" ? "左侧栏" : "右侧栏";
  const visible = !hidden || peek;

  function clearTimers(): void {
    clearTimeout(opening.current);
    clearTimeout(closing.current);
  }
  function protectedFocus(): boolean {
    const active = document.activeElement;
    return !!active && !!root.current?.contains(active) &&
      (keyboard.current || active.matches("input, textarea, select, [contenteditable='true']"));
  }
  function scheduleClose(): void {
    clearTimeout(closing.current);
    if (hovered.current || pressed.current || drag.current || protectedFocus()) return;
    closing.current = setTimeout(() => {
      if (!hovered.current && !pressed.current && !drag.current && !protectedFocus()) setPeek(false);
    }, 300);
  }
  function enter(event: ReactPointerEvent): void {
    hovered.current = true;
    clearTimeout(closing.current);
    if (!hidden || peek || blocked.current || event.buttons !== 0) return;
    clearTimeout(opening.current);
    opening.current = setTimeout(() => setPeek(true), 150);
  }
  function leave(): void {
    hovered.current = false;
    blocked.current = false;
    clearTimeout(opening.current);
    scheduleClose();
  }
  function toggle(): void {
    clearTimers();
    restoreFocus.current = true;
    if (hidden) {
      onHidden(false);
      setPeek(false);
    } else {
      blocked.current = hovered.current;
      setPeek(false);
      onHidden(true);
    }
  }
  useLayoutEffect(() => {
    if (!restoreFocus.current) return;
    (hidden ? rail : pin).current?.focus({ preventScroll: true });
    restoreFocus.current = false;
  }, [hidden]);

  function startDrag(event: ReactPointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    clearTimers();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { id: event.pointerId, x: event.clientX, width, latest: width };
    setDragging(true);
    document.body.classList.add("sidebar-resizing");
  }
  function moveDrag(event: ReactPointerEvent<HTMLDivElement>): void {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    current.latest = clampWidth(side, current.width + (event.clientX - current.x) * (side === "left" ? 1 : -1), maximum);
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => onResize(current.latest, false));
  }
  function finishDrag(): void {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    cancelAnimationFrame(frame.current);
    onResize(current.latest, true);
    if (handle.current?.hasPointerCapture(current.id)) handle.current.releasePointerCapture(current.id);
    document.body.classList.remove("sidebar-resizing");
    setDragging(false);
  }

  const handlers = useRef({ finishDrag, scheduleClose });
  handlers.current = { finishDrag, scheduleClose };
  useEffect(() => {
    const release = (event: PointerEvent): void => {
      if (!pressed.current && !drag.current) return;
      pressed.current = false;
      handlers.current.finishDrag();
      hovered.current = !!root.current?.contains(document.elementFromPoint(event.clientX, event.clientY));
      handlers.current.scheduleClose();
    };
    const blur = (): void => {
      pressed.current = false;
      hovered.current = false;
      clearTimers();
      handlers.current.finishDrag();
      handlers.current.scheduleClose();
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      window.removeEventListener("blur", blur);
      clearTimers();
      cancelAnimationFrame(frame.current);
      if (drag.current) document.body.classList.remove("sidebar-resizing");
    };
  }, []);

  return <div ref={root} className={`sidebar sidebar-${side}${hidden ? " auto-hide" : ""}${peek ? " peeking" : ""}${dragging ? " resizing" : ""}`}
    style={{ "--sidebar-width": `${width}px` } as CSSProperties}
    onPointerEnter={enter} onPointerLeave={leave}
    onPointerDownCapture={() => { keyboard.current = false; pressed.current = true; clearTimers(); }}
    onKeyDownCapture={() => { keyboard.current = true; }}
    onFocusCapture={() => { clearTimeout(closing.current); }}
    onBlurCapture={() => { queueMicrotask(() => handlers.current.scheduleClose()); }}>
    <button ref={rail} type="button" className="sidebar-rail" hidden={!hidden}
      aria-label={`固定${label}`} aria-controls={`sidebar-${side}-panel`} aria-expanded={visible}
      title={`悬停展开，点击固定${label}`} onClick={toggle}
      onFocus={event => {
        if (!restoreFocus.current && !blocked.current && event.currentTarget.matches(":focus-visible")) {
          keyboard.current = true;
          setPeek(true);
        }
      }}>{side === "left" ? "▶" : "◀"}</button>
    <div id={`sidebar-${side}-panel`} className="sidebar-panel" inert={!visible} aria-hidden={!visible}>
      <div className="sidebar-heading"><span>{side === "left" ? "数据导航" : "标注面板"}</span>
        <button ref={pin} type="button" aria-label={`${hidden ? "固定" : "隐藏"}${label}`} onClick={toggle}>
          {hidden ? "固定侧栏" : "隐藏侧栏"}
        </button>
      </div>
      {children}
    </div>
    <div ref={handle} className="sidebar-handle" role="separator" tabIndex={visible ? 0 : -1}
      aria-label={`调整${label}宽度`} aria-controls={`sidebar-${side}-panel`} aria-orientation="vertical"
      aria-valuemin={limits[side].min} aria-valuemax={Math.max(width, maximum)} aria-valuenow={Math.round(width)}
      aria-hidden={!visible} onPointerDown={startDrag} onPointerMove={moveDrag}
      onLostPointerCapture={() => { finishDrag(); pressed.current = false; scheduleClose(); }}
      onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        const delta = (event.key === "ArrowRight" ? 10 : -10) * (side === "left" ? 1 : -1);
        const next = event.key === "Home" ? limits[side].min : event.key === "End" ? maximum : width + delta;
        onResize(clampWidth(side, next, maximum), true);
      }}/>
  </div>;
}
