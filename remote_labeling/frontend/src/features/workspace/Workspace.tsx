import { uuid } from "../../state/id";
import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, Image as CanvasImage, Layer, Line, Stage } from "react-konva";
import type Konva from "konva";
import { variants, type Group, type Point, type Region, type Sample, type Variant } from "../../api/client";
import { corners, rectangle, syncHR } from "../../state/domain";

export type Mode = "select" | "pan" | "rectangle" | "quadrilateral";
interface Viewport { cx: number; cy: number; zoom: number }
interface Props {
  sample: Sample; group: Group; images: Partial<Record<Variant, ImageBitmap>>;
  editable: boolean; selected: string[]; mode: Mode; active: Variant; focus: number;
  onSelect: (ids: string[]) => void; onActive: (v: Variant) => void;
  onChange: (group: Group) => void;
}
const colors = ["#38d9a9", "#ffd166", "#ff7b86"];

export function Workspace(props: Props) {
  const [single, setSingle] = useState<Variant | null>(null);
  const [viewport, setViewport] = useState<Viewport>({cx: .5, cy: .5, zoom: 1});
  const [preview, setPreview] = useState<Region[] | null>(null);
  const frame = useRef<number>(0);
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
      key={variant} {...props} group={group} baseGroup={props.group} variant={variant}
      image={props.images[variant]} viewport={viewport} setViewport={setViewport}
      single={single === variant} onSingle={() => setSingle(single ? null : variant)}
      update={update}
    />)}
  </div>;
}

interface PaneProps extends Props {
  variant: Variant; image?: ImageBitmap; viewport: Viewport; baseGroup: Group;
  setViewport: (v: Viewport) => void; single: boolean; onSingle: () => void;
  update: (r: Region, p: Point[], commit: boolean) => void;
}

function Pane(props: PaneProps) {
  const holder = useRef<HTMLDivElement>(null);
  const stage = useRef<Konva.Stage>(null);
  const [size, setSize] = useState<Point>([400, 300]);
  const [drawing, setDrawing] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null);
  const dragStart = useRef<Region | null>(null);
  const [draggingVertex,setDraggingVertex] = useState(false);
  const pan = useRef<{pointer: Point; viewport: Viewport} | null>(null);
  const [iw, ih] = props.sample.dimensions[props.variant];
  const [hw, hh] = props.sample.dimensions.HR;
  const base = Math.min(size[0] / hw, size[1] / hh) * hw / iw * .94;
  const scale = base * props.viewport.zoom;
  const x = size[0] / 2 - props.viewport.cx * iw * scale;
  const y = size[1] / 2 - props.viewport.cy * ih * scale;
  const editable = props.editable && props.variant === "HR";
  const canDrag = editable && props.mode === "select";
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const rect = entries[0].contentRect;
      setSize([Math.max(1, rect.width), Math.max(1, rect.height)]);
    });
    if (holder.current) observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => { setDrawing([]); setCursor(null); }, [props.mode, props.sample.id, props.editable]);
  useEffect(() => {
    const cancel = (event: KeyboardEvent) => { if (event.key === "Escape") setDrawing([]); };
    window.addEventListener("keydown", cancel);
    return () => window.removeEventListener("keydown", cancel);
  }, []);

  function point(): Point {
    const p = stage.current?.getPointerPosition() ?? {x: 0, y: 0};
    return [Math.max(0, Math.min(iw, (p.x-x)/scale)), Math.max(0, Math.min(ih, (p.y-y)/scale))];
  }
  function finish(points: Point[]) {
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
  function moveRegion(region: Region, node: Konva.Node, commit: boolean) {
    const original = dragStart.current ?? region;
    const points = corners(original);
    const xs = points.map(p=>p[0]), ys = points.map(p=>p[1]);
    const dx = Math.max(-Math.min(...xs), Math.min(iw-Math.max(...xs), node.x()));
    const dy = Math.max(-Math.min(...ys), Math.min(ih-Math.max(...ys), node.y()));
    const shifted = points.map(p=>[p[0]+dx, p[1]+dy] as Point);
    if (commit) node.position({x: 0, y: 0});
    props.update(original, shifted, commit);
    if(commit)dragStart.current=null;
  }
  function vertex(region: Region, index: number, node: Konva.Node, commit: boolean) {
    const p: Point = [Math.max(0, Math.min(iw, node.x())), Math.max(0, Math.min(ih, node.y()))];
    const original = dragStart.current ?? region;
    let points = [...corners(original)];
    if (original.shape_type === "rectangle") points = rectangle(p, points[(index+2)%4]);
    else points[index] = p;
    props.update(original, points, commit);
    if(commit){dragStart.current=null;setDraggingVertex(false);}
  }
  const selection = props.selected;
  return <section className={"pane " + (props.active === props.variant ? "active" : "")} onPointerDown={() => props.onActive(props.variant)}>
    <header>
      <strong>{props.variant}</strong><span>{iw} × {ih} · {(scale*100).toFixed(0)}%</span>
      <button onClick={() => props.setViewport({cx:.5, cy:.5, zoom:1})}>适应</button>
      <button onClick={() => props.setViewport({...props.viewport, zoom:1/base})}>1:1</button>
      <button onClick={props.onSingle}>{props.single ? "四视图" : "放大视图"}</button>
    </header>
    <div className="canvas-holder" ref={holder} data-testid={"canvas-" + props.variant}>
      {!props.image && <div className="loading-image">加载原图…</div>}
      <Stage ref={stage} width={size[0]} height={size[1]}
        onContextMenu={e=>e.evt.preventDefault()}
        onWheel={e=>{
          e.evt.preventDefault();
          const p = stage.current!.getPointerPosition()!;
          const zoom = Math.max(.05, Math.min(100, props.viewport.zoom * (e.evt.deltaY < 0 ? 1.12 : 1/1.12)));
          const nextScale = base*zoom;
          props.setViewport({
            zoom, cx: ((p.x-x)/scale-(p.x-size[0]/2)/nextScale)/iw,
            cy: ((p.y-y)/scale-(p.y-size[1]/2)/nextScale)/ih,
          });
        }}
        onMouseDown={e=>{
          props.onActive(props.variant);
          if (props.mode === "pan" || e.evt.button !== 0) {
            const p = stage.current!.getPointerPosition()!;
            pan.current = {pointer:[p.x,p.y], viewport:props.viewport};
            return;
          }
          if (editable && props.mode === "rectangle") setDrawing([point()]);
          else if (editable && props.mode === "quadrilateral") {
            const next = [...drawing, point()];
            if (next.length === 4) finish(next); else setDrawing(next);
          } else if (e.target === e.target.getStage()) props.onSelect([]);
        }}
        onMouseMove={()=>{
          const p = stage.current!.getPointerPosition()!;
          if (pan.current) {
            props.setViewport({
              ...pan.current.viewport,
              cx:pan.current.viewport.cx-(p.x-pan.current.pointer[0])/(scale*iw),
              cy:pan.current.viewport.cy-(p.y-pan.current.pointer[1])/(scale*ih),
            });
          } else if (drawing.length) setCursor(point());
        }}
        onMouseUp={()=>{
          pan.current = null;
          if (drawing.length === 1 && editable && props.mode === "rectangle") finish(rectangle(drawing[0], point()));
        }}
        onMouseLeave={()=>{ pan.current=null; }}
      >
        <Layer listening={false} x={x} y={y} scaleX={scale} scaleY={scale} imageSmoothingEnabled={false}>
          {props.image && <CanvasImage image={props.image} width={iw} height={ih}/>}
        </Layer>
        <Layer x={x} y={y} scaleX={scale} scaleY={scale}>
          {useMemo(()=>(props.variant === "HR" && !draggingVertex ? props.baseGroup.HR : props.group[props.variant]).map((region, index)=>{
            const selected = selection.includes(region.region_id);
            const color = region.recoverable == null ? "#abb5c8" : colors[region.recoverable];
            return <Line key={region.region_id}
              points={corners(region).flat()} closed stroke={selected ? "#ffffff" : color}
              fill={selected ? "#ffffff18" : "#00000001"} strokeWidth={selected ? 2.5 : 1.5} strokeScaleEnabled={false}
              hitStrokeWidth={8} draggable={canDrag && !region.locked}
              listening={props.mode === "select"}
              name={"region-" + index}
              onMouseDown={e=>{ e.cancelBubble=true; props.onActive(props.variant); }}
              onClick={e=>{
                e.cancelBubble=true;
                props.onSelect(e.evt.shiftKey ? selected ? selection.filter(id=>id!==region.region_id) : [...selection,region.region_id] : [region.region_id]);
              }}
              onDragStart={()=>{dragStart.current=structuredClone(region);props.onSelect([region.region_id]);}}
              onDragMove={e=>moveRegion(region,e.target,false)}
              onDragEnd={e=>moveRegion(region,e.target,true)}
            />;
          }),[props.baseGroup.HR,props.group[props.variant],props.selected,props.mode,canDrag,draggingVertex,iw,ih])}
          {canDrag && selection.length === 1 && props.baseGroup.HR.filter(r=>selection.includes(r.region_id) && !r.locked).flatMap(region =>
            corners(region).map((p,i)=><Circle key={region.region_id+"-"+i}
              x={p[0]} y={p[1]} radius={4.5/scale} stroke="#152238" strokeWidth={1/scale}
              fill="#ffffff" draggable onMouseDown={e=>{e.cancelBubble=true;}}
              onDragStart={()=>{dragStart.current=structuredClone(region);setDraggingVertex(true);}}
              onDragMove={e=>{e.cancelBubble=true; vertex(region,i,e.target,false);}}
              onDragEnd={e=>{e.cancelBubble=true; vertex(region,i,e.target,true);}}
            />)
          )}
          {drawing.length > 0 && <Line listening={false}
            points={(props.mode === "rectangle" && cursor ? rectangle(drawing[0],cursor) : [...drawing,...(cursor?[cursor]:[])]).flat()}
            stroke="#7eb6ff" closed={props.mode === "rectangle"} strokeWidth={2/scale} dash={[5/scale,4/scale]}
          />}
        </Layer>
      </Stage>
    </div>
  </section>;
}
