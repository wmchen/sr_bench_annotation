import { uuid } from "../../state/id";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Circle, Image as CanvasImage, Layer, Line, Stage } from "react-konva";
import type Konva from "konva";
import { variants, type Group, type Point, type Region, type Sample, type Variant } from "../../api/client";
import { corners, rectangle, syncHR, typing } from "../../state/domain";

import { crosshairDash, regionStrokeWidth, type DisplayPreferences } from "./displayPreferences";

export type Mode = "select" | "rectangle" | "quadrilateral";
interface Viewport { cx: number; cy: number; zoom: number }
interface Props {
  displayPreferences: DisplayPreferences;
  sample: Sample; group: Group; images: Partial<Record<Variant, ImageBitmap>>;
  editable: boolean; selected: string[]; mode: Mode; active: Variant; focus: number;
  onSelect: (ids: string[], anchor?: string) => void; onActive: (v: Variant) => void;
  onChange: (group: Group) => void;
}
const colors = ["#38d9a9", "#ffd166", "#ff7b86"];

export function Workspace(props: Props) {
  const [single, setSingle] = useState<Variant | null>(null);
  const [viewport, setViewport] = useState<Viewport>({cx: .5, cy: .5, zoom: 1});
  const [preview, setPreview] = useState<Region[] | null>(null);
  const frame = useRef<number>(0);
  const [space, setSpace] = useState(false);
  useEffect(() => {
    const down = (event: KeyboardEvent): void => {
      if (event.code !== "Space" || typing(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
      event.preventDefault();
      setSpace(true);
    };
    const up = (event: KeyboardEvent): void => { if (event.code === "Space") setSpace(false); };
    const blur = (): void => setSpace(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);

  /** Discard an interrupted geometry gesture without adding an undo entry. */
  function cancelPreview(): void {
    cancelAnimationFrame(frame.current);
    setPreview(null);
  }
  const group = preview ? syncHR(preview, props.group, props.sample.dimensions) : props.group;
  useEffect(() => {
    setViewport({cx: .5, cy: .5, zoom: 1});
    setPreview(null);
  }, [props.sample.id, props.sample.dataset]);
  useEffect(() => {
    if (!props.focus || !props.selected.length) return;
    const points = props.group.HR.filter(r => props.selected.includes(r.region_id)).flatMap(r => r.points);
    if (!points.length) return;
    const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
    const [w, h] = props.sample.dimensions.HR;
    setViewport({
      cx: (Math.min(...xs) + Math.max(...xs)) / (2 * w),
      cy: (Math.min(...ys) + Math.max(...ys)) / (2 * h),
      zoom: Math.min(40, .7 / Math.max((Math.max(...xs)-Math.min(...xs))/w, (Math.max(...ys)-Math.min(...ys))/h, .01)),
    });
  }, [props.focus]);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  function update(region: Region, points: Point[], commit: boolean) {
    const hr = props.group.HR.map(r => r.region_id === region.region_id ? {...r, points} : r);
    cancelAnimationFrame(frame.current);
    if (commit) {
      setPreview(null);
      props.onChange(syncHR(hr, props.group, props.sample.dimensions));
    } else {
      frame.current = requestAnimationFrame(() => setPreview(hr));
    }
  }

  return <div className={"workspace " + (single ? "single" : "")}>
    {(single ? [single] : variants).map(variant => <Pane
      key={variant} {...props} group={group} variant={variant}
      image={props.images[variant]} viewport={viewport} setViewport={setViewport}
      single={single === variant} onSingle={() => setSingle(single ? null : variant)}
      update={update} space={space} cancelPreview={cancelPreview}
    />)}
  </div>;
}

interface PaneProps extends Props {
  variant: Variant; image?: ImageBitmap; viewport: Viewport;
  setViewport: (v: Viewport) => void; single: boolean; onSingle: () => void;
  update: (r: Region, p: Point[], commit: boolean) => void;
  space: boolean; cancelPreview: () => void;
}
interface Hit { region: Region; kind: "region" | "vertex" | "edge"; index: number }
type Gesture =
  | { kind: "pan"; pointer: Point; viewport: Viewport }
  | { kind: "rectangle"; start: Point }
  | { kind: "move" | "vertex" | "edge"; start: Point; region: Region; index: number; moved: boolean };

function Pane(props: PaneProps) {
  const holder = useRef<HTMLDivElement>(null);
  const stage = useRef<Konva.Stage>(null);
  const [size, setSize] = useState<Point>([400, 300]);
  const [drawing, setDrawing] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [screenPointer, setScreenPointer] = useState<Point | null>(null);
  const [hover, setHover] = useState<Hit | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const lastPointer = useRef<Point | null>(null);
  const [dragCursor, setDragCursor] = useState<string | null>(null);
  const [iw, ih] = props.sample.dimensions[props.variant];
  const [hw, hh] = props.sample.dimensions.HR;
  const base = Math.min(size[0] / hw, size[1] / hh) * hw / iw * .94;
  const scale = base * props.viewport.zoom;
  const x = size[0] / 2 - props.viewport.cx * iw * scale;
  const y = size[1] / 2 - props.viewport.cy * ih * scale;
  const editable = props.editable && props.variant === "HR";
  const drawingMode = props.editable && props.mode !== "select";
  const canEdit = editable && !drawingMode;
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const rect = entries[0].contentRect;
      setSize([Math.max(1, rect.width), Math.max(1, rect.height)]);
    });
    if (holder.current) observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    cancel(); setHover(null);
  }, [props.mode, props.sample.id, props.sample.dataset, props.editable]);
  useEffect(() => {
    lastPointer.current = null;
    setScreenPointer(null);
  }, [props.sample.id, props.sample.dataset, props.editable]);
  useEffect(() => {
    const blur = (): void => {
      lastPointer.current = null;
      setScreenPointer(null);
      cancel();
    };
    const escape = (event: KeyboardEvent): void => { if (event.key === "Escape" && !typing(event.target)) cancel(); };
    window.addEventListener("keydown", escape);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", escape);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // Konva paints its hit graph on the next frame. Refresh stationary hover
  // after tool changes, undo, and viewport updates using that new graph.
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (lastPointer.current && !gesture.current) setHover(hit(lastPointer.current));
    });
    return () => cancelAnimationFrame(frame);
  }, [props.group, props.selected, props.viewport, drawingMode, canEdit, size]);

  /** Cancel captured gestures when focus or editing authority is lost. */
  function cancel(): void {
    gesture.current = null;
    setDragCursor(null);
    setDrawing([]);
    setCursor(null);
    props.cancelPreview();
  }

  /** Resolve browser coordinates independently of Konva's drag machinery. */
  function pointer(event: ReactPointerEvent<HTMLDivElement>): Point {
    const bounds = holder.current!.getBoundingClientRect();
    const p: Point = [event.clientX - bounds.left, event.clientY - bounds.top];
    lastPointer.current = p[0] >= 0 && p[1] >= 0 && p[0] < bounds.width && p[1] < bounds.height ? p : null;
    if (props.variant === "HR") setScreenPointer(lastPointer.current);
    return p;
  }

  /** Clamp edits and drawing to the source image bounds. */
  function point(p: Point): Point {
    return [Math.max(0, Math.min(iw, (p[0]-x)/scale)), Math.max(0, Math.min(ih, (p[1]-y)/scale))];
  }

  /** Use the rendered hit graph so overlapping regions follow visual order. */
  function hit(p: Point): Hit | null {
    return stage.current?.getIntersection({x:p[0], y:p[1]})?.getAttr("interaction") ?? null;
  }

  /** Choose a cursor from the same target used to start an interaction. */
  function targetCursor(target: Hit | null): string {
    if (props.space) return "grab";
    if (drawingMode) return "default";
    if (!target) return "grab";
    if (!canEdit || target.region.locked || !props.selected.includes(target.region.region_id)) return "pointer";
    if (target.kind === "vertex") return target.region.shape_type === "rectangle"
      ? (target.index % 2 === 0 ? "nwse-resize" : "nesw-resize") : "pointer";
    if (target.kind === "edge") return target.index % 2 === 0 ? "ns-resize" : "ew-resize";
    return "move";
  }

  /** Commit a valid newly drawn region and keep its drawing tool active. */
  function finish(points: Point[]): void {
    if (Math.max(...points.map(p=>p[0]))-Math.min(...points.map(p=>p[0])) < 1 ||
        Math.max(...points.map(p=>p[1]))-Math.min(...points.map(p=>p[1])) < 1) {
      setDrawing([]); return;
    }
    const region: Region = {
      region_id: uuid(), shape_type: props.mode === "quadrilateral" ? "quadrilateral" : "rectangle",
      points, label: props.sample.attribute, description: "",
      recoverable: props.sample.attribute === "text" ? 0 : null,
    };
    props.onChange(syncHR([...props.group.HR, region], props.group, props.sample.dimensions));
    props.onSelect([region.region_id]);
    setDrawing([]);
  }

  /** Start left-button selection, editing, drawing, or forced panning. */
  function down(event: ReactPointerEvent<HTMLDivElement>): void {
    if (event.button !== 0 || gesture.current) return;
    event.preventDefault();
    holder.current!.focus({preventScroll:true});
    props.onActive(props.variant);
    // A rapid click after undo/tool switching can precede Konva's next frame.
    stage.current?.draw();
    const p = pointer(event), target = hit(p);
    setHover(target);
    holder.current!.setPointerCapture(event.pointerId);
    if (props.space || (!drawingMode && !target)) {
      gesture.current = {kind:"pan", pointer:p, viewport:props.viewport};
      setDragCursor("grabbing");
      if (!props.space) props.onSelect([]);
    } else if (drawingMode) {
      if (!editable) return;
      if (props.mode === "rectangle") {
        gesture.current = {kind:"rectangle", start:point(p)};
        setDrawing([point(p)]); setCursor(point(p));
      } else {
        const next = [...drawing, point(p)];
        if (next.length === 4) finish(next); else { setDrawing(next); setCursor(point(p)); }
      }
    } else if (target) {
      const selected = props.selected.includes(target.region.region_id);
      if (event.ctrlKey || event.metaKey) {
        props.onSelect(selected ? props.selected.filter(id=>id!==target.region.region_id) : [...props.selected,target.region.region_id], target.region.region_id);
      } else {
        props.onSelect([target.region.region_id]);
        // A first click only selects; dragging requires an existing selection.
        if (selected && canEdit && !target.region.locked) {
          gesture.current = {kind:target.kind === "region" ? "move" : target.kind,
            start:point(p), region:structuredClone(target.region), index:target.index, moved:false};
          setDragCursor(target.kind === "vertex" && target.region.shape_type === "quadrilateral" ? "grabbing" : targetCursor(target));
        }
      }
    }
  }

  /** Preview all geometry from the original gesture, committing once on release. */
  function edit(g: Extract<Gesture, {region: Region}>, p: Point, commit: boolean): void {
    const points = corners(g.region).map(v=>[...v] as Point);
    let next = points;
    if (g.kind === "move") {
      const xs = points.map(v=>v[0]), ys = points.map(v=>v[1]);
      const dx = Math.max(-Math.min(...xs), Math.min(iw-Math.max(...xs), p[0]-g.start[0]));
      const dy = Math.max(-Math.min(...ys), Math.min(ih-Math.max(...ys), p[1]-g.start[1]));
      next = points.map(v=>[v[0]+dx,v[1]+dy]);
    } else if (g.kind === "vertex") {
      if (g.region.shape_type === "rectangle") next = rectangle(p,points[(g.index+2)%4]);
      else next[g.index] = p;
    } else {
      const axis = g.index % 2 === 0 ? 1 : 0;
      points[g.index][axis] = p[axis];
      points[(g.index+1)%4][axis] = p[axis];
      next = rectangle(points[0],points[2]);
    }
    props.update(g.region,next,commit);
  }

  /** Keep captured drags working even when the pointer leaves the canvas. */
  function move(event: ReactPointerEvent<HTMLDivElement>): void {
    const p = pointer(event), g = gesture.current;
    if (g?.kind === "pan") {
      props.setViewport({...g.viewport,
        cx:g.viewport.cx-(p[0]-g.pointer[0])/(scale*iw),
        cy:g.viewport.cy-(p[1]-g.pointer[1])/(scale*ih)});
    } else if (g && "region" in g) {
      if (Math.hypot(point(p)[0]-g.start[0], point(p)[1]-g.start[1])*scale >= 3) g.moved = true;
      if (g.moved) edit(g,point(p),false);
    } else if (drawing.length) setCursor(point(p));
    if (!g) setHover(hit(p));
  }

  /** End a gesture without turning a forced pan into a drawing click. */
  function up(event: ReactPointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) return;
    const p = pointer(event), g = gesture.current;
    gesture.current = null;
    setDragCursor(null);
    if (g?.kind === "rectangle") finish(rectangle(g.start,point(p)));
    else if (g && "region" in g && g.moved) edit(g,point(p),true);
    if (holder.current!.hasPointerCapture(event.pointerId)) holder.current!.releasePointerCapture(event.pointerId);
    setHover(hit(p));
  }

  const crosshairPoint = screenPointer && editable && drawingMode && !props.space && dragCursor !== "grabbing"
    ? point(screenPointer) : null;
  const crosshairX = crosshairPoint ? x + crosshairPoint[0] * scale : 0;
  const crosshairY = crosshairPoint ? y + crosshairPoint[1] * scale : 0;
  const previewCursor = screenPointer ? point(screenPointer) : cursor;
  const appearance = props.displayPreferences;

  return <section className={"pane " + (props.active === props.variant ? "active" : "")} onPointerDown={() => props.onActive(props.variant)}>
    <header>
      <strong>{props.variant}</strong><span>{iw} × {ih} · {(scale*100).toFixed(0)}%</span>
      <button onClick={() => props.setViewport({cx:.5, cy:.5, zoom:1})}>适应</button>
      <button onClick={() => props.setViewport({...props.viewport, zoom:1/base})}>1:1</button>
      <button onClick={props.onSingle}>{props.single ? "四视图" : "放大视图"}</button>
    </header>
    <div className="canvas-holder" ref={holder} data-testid={"canvas-" + props.variant} tabIndex={-1}
      style={{cursor:dragCursor ?? targetCursor(hover), touchAction:"none"}}
      onPointerEnter={event => { pointer(event); }} onPointerDown={down} onPointerMove={move} onPointerUp={up}
      onPointerCancel={()=>{lastPointer.current=null;setScreenPointer(null);cancel();}} onLostPointerCapture={()=>{if (gesture.current) cancel();}}
      onPointerLeave={()=>{lastPointer.current=null;setScreenPointer(null);setHover(null);}}>
      {!props.image && <div className="loading-image">加载原图…</div>}
      <Stage ref={stage} width={size[0]} height={size[1]}
        onWheel={e=>{
          e.evt.preventDefault();
          if (gesture.current) return;
          const p = stage.current!.getPointerPosition()!;
          const zoom = Math.max(.05, Math.min(100, props.viewport.zoom * (e.evt.deltaY < 0 ? 1.12 : 1/1.12)));
          const nextScale = base*zoom;
          props.setViewport({
            zoom, cx: ((p.x-x)/scale-(p.x-size[0]/2)/nextScale)/iw,
            cy: ((p.y-y)/scale-(p.y-size[1]/2)/nextScale)/ih,
          });
        }}>
        <Layer listening={false} x={x} y={y} scaleX={scale} scaleY={scale} imageSmoothingEnabled={false}>
          {props.image && <CanvasImage image={props.image} width={iw} height={ih}/>}
        </Layer>
        <Layer x={x} y={y} scaleX={scale} scaleY={scale}>
          {props.group[props.variant].map((region, index)=>{
            const selected = props.selected.includes(region.region_id);
            const color = region.recoverable == null ? "#abb5c8" : colors[region.recoverable];
            return <Line key={region.region_id}
              points={corners(region).flat()} closed stroke={selected ? "#ffffff" : color}
              fill={selected ? "#ffffff18" : "#00000001"} strokeWidth={regionStrokeWidth(appearance, selected)} strokeScaleEnabled={false}
              hitStrokeWidth={8/scale} listening={!drawingMode}
              name={"region-" + index} interaction={{region,kind:"region",index:0}}/>;
          })}
          {canEdit && props.selected.length === 1 && props.group.HR.filter(r=>props.selected.includes(r.region_id) && !r.locked).flatMap(region => {
            const points = corners(region);
            return [
              ...(region.shape_type === "rectangle" ? points.map((p,i)=><Line key={region.region_id+"-edge-"+i}
                points={[...p,...points[(i+1)%4]]} stroke="#ffffff" strokeWidth={regionStrokeWidth(appearance, true)} strokeScaleEnabled={false}
                hitStrokeWidth={10/scale} interaction={{region,kind:"edge",index:i}}/>) : []),
              ...points.map((p,i)=><Circle key={region.region_id+"-vertex-"+i}
                x={p[0]} y={p[1]} radius={4.5/scale} stroke="#152238" strokeWidth={1/scale}
                hitStrokeWidth={5/scale} fill="#ffffff" interaction={{region,kind:"vertex",index:i}}/>),
            ];
          })}
          {drawing.length > 0 && <Line listening={false}
            points={(props.mode === "rectangle" && previewCursor ? rectangle(drawing[0],previewCursor) : [...drawing,...(previewCursor?[previewCursor]:[])]).flat()}
            stroke="#7eb6ff" closed={props.mode === "rectangle"} strokeWidth={appearance.regionWidth} strokeScaleEnabled={false} dash={[5/scale,4/scale]}/>
          }
        </Layer>
        {props.variant === "HR" && <Layer listening={false}>
          {crosshairPoint && <>
            <Line points={[0, crosshairY, size[0], crosshairY]} stroke={appearance.crosshairColor}
              strokeWidth={appearance.crosshairWidth} dash={crosshairDash(appearance.crosshairStyle, appearance.crosshairWidth)}/>
            <Line points={[crosshairX, 0, crosshairX, size[1]]} stroke={appearance.crosshairColor}
              strokeWidth={appearance.crosshairWidth} dash={crosshairDash(appearance.crosshairStyle, appearance.crosshairWidth)}/>
          </>}
        </Layer>}
      </Stage>
    </div>
  </section>;
}
