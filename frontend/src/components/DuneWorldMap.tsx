import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import type { DuneMapLocation, DuneMapMarker } from "../api";
import {
  DD_GRID,
  DD_ROWS,
  DUNE_MAPS,
  type DuneMapCfg,
  type DuneMapKey,
  pctToWorld,
  sectorFor,
  worldToPct,
} from "../lib/duneMapCoords";

type Props = {
  mapKey: DuneMapKey;
  markers: DuneMapMarker[];
  locations: DuneMapLocation[];
  selectedFls: string;
  pickMode: boolean;
  pending: { x: number; y: number } | null;
  onSelectPlayer: (fls: string) => void;
  onPick: (pos: { x: number; y: number }) => void;
};

/** Longest stage edge in CSS px before the fit + user zoom multipliers. */
const BASE_PX = 1024;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 12;

/** zoom + pan move together on a wheel tick, so they are one state value:
    nesting setPan inside setZoom made the pan update run twice under React's
    development double-invoke, which is what pulled the zoom off the cursor. */
type View = { zoom: number; x: number; y: number };
const HOME: View = { zoom: 1, x: 0, y: 0 };

export default function DuneWorldMap({
  mapKey,
  markers,
  locations,
  selectedFls,
  pickMode,
  pending,
  onSelectPlayer,
  onPick,
}: Props) {
  const cfg = DUNE_MAPS.find((m) => m.key === mapKey) ?? DUNE_MAPS[0];
  const viewportRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<View>(HOME);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const drag = useRef({ active: false, x: 0, y: 0, moved: false });

  // Stage keeps the image's own aspect, longest edge BASE_PX, then scales to
  // fit the panel: zoom 1 = the whole region visible, centred.
  const aspect = cfg.imgW / cfg.imgH;
  const stageW = aspect >= 1 ? BASE_PX : BASE_PX * aspect;
  const stageH = aspect >= 1 ? BASE_PX / aspect : BASE_PX;
  const fit = box.w > 0 ? Math.min(box.w / stageW, box.h / stageH) : 1;
  const scale = fit * view.zoom;
  const screenW = stageW * scale;
  const screenH = stageH * scale;

  const resetView = useCallback(() => setView(HOME), []);

  useEffect(() => {
    resetView();
  }, [mapKey, resetView]);

  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    // clientWidth/Height, not the bounding rect: the marker layer is inset
    // inside the 1px border, so measuring the border box would offset every
    // dot by a pixel against the image under it.
    const measure = () => setBox({ w: vp.clientWidth, h: vp.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(vp);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const stage = stageRef.current;
      if (!stage) return;
      // Anchor on the stage's own rect: it already includes pan and scale, and
      // transform-origin is its centre, so a point d px from that centre lands
      // at k*d after zooming. Translating by d*(1-k) puts it back under the
      // cursor exactly, whatever the viewport's borders and padding are.
      const rect = stage.getBoundingClientRect();
      const dx = e.clientX - (rect.left + rect.width / 2);
      const dy = e.clientY - (rect.top + rect.height / 2);
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setView((v) => {
        const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.zoom * factor));
        const k = zoom / v.zoom;
        return { zoom, x: v.x + dx * (1 - k), y: v.y + dy * (1 - k) };
      });
    };
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => vp.removeEventListener("wheel", onWheel);
  }, []);

  function onPointerDown(e: ReactPointerEvent) {
    drag.current = { active: true, x: e.clientX, y: e.clientY, moved: false };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e: ReactPointerEvent) {
    if (!drag.current.active) return;
    const dx = e.clientX - drag.current.x;
    const dy = e.clientY - drag.current.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) drag.current.moved = true;
    drag.current.x = e.clientX;
    drag.current.y = e.clientY;
    setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
  }
  function onPointerUp(e: ReactPointerEvent) {
    const wasDrag = drag.current.moved;
    drag.current.active = false;
    if (!wasDrag && pickMode && stageRef.current) {
      const rect = stageRef.current.getBoundingClientRect();
      onPick(pctToWorld(e.clientX, e.clientY, rect, cfg));
    }
  }

  /** World coords → viewport px. Markers live outside the scaled stage so
      they keep a constant on-screen size at every zoom level. */
  const project = useCallback(
    (x: number, y: number) => {
      const p = worldToPct(x, y, cfg);
      return {
        left: box.w / 2 + view.x + (p.left / 100 - 0.5) * screenW,
        top: box.h / 2 + view.y + (p.top / 100 - 0.5) * screenH,
      };
    },
    [cfg, box.w, box.h, view.x, view.y, screenW, screenH]
  );

  const grid = useMemo(() => {
    if (!cfg.sectorGrid || screenW <= 0) return null;
    const x0 = box.w / 2 + view.x - screenW / 2;
    const y0 = box.h / 2 + view.y - screenH / 2;
    const stepX = screenW / DD_GRID;
    const stepY = screenH / DD_GRID;
    const cells: { key: string; left: number; top: number }[] = [];
    // Row A is the southern edge, so the top row on screen is I.
    for (let row = 0; row < DD_GRID; row += 1) {
      for (let col = 0; col < DD_GRID; col += 1) {
        cells.push({
          key: `${DD_ROWS[row]}${col + 1}`,
          left: x0 + (col + 0.5) * stepX,
          top: y0 + (DD_GRID - row - 0.5) * stepY,
        });
      }
    }
    return {
      x0,
      y0,
      stepX,
      stepY,
      cells,
      ticks: Array.from({ length: DD_GRID + 1 }, (_, i) => i),
      showLabels: Math.min(stepX, stepY) >= 34,
    };
  }, [cfg.sectorGrid, box.w, box.h, view.x, view.y, screenW, screenH]);

  const mapLocations = locations.filter((l) => l.map === mapKey);

  return (
    <div className="pw-map dune-map">
      <div className="pw-map-toolbar">
        <div className="pw-map-tools">
          <button type="button" className="btn small ghost" onClick={resetView}>
            Reset view
          </button>
          <span className="pw-map-zoom-label">{Math.round(view.zoom * 100)}%</span>
          {!cfg.liveData && <span className="muted">Reference map</span>}
        </div>
      </div>
      <div
        ref={viewportRef}
        className={`pw-map-viewport${pickMode ? " dune-map-pick" : ""}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <div
          ref={stageRef}
          className="pw-map-stage"
          style={{
            width: stageW,
            height: stageH,
            marginLeft: -stageW / 2,
            marginTop: -stageH / 2,
            transform: `translate(${view.x}px, ${view.y}px) scale(${scale})`,
          }}
        >
          <img className="pw-map-image" src={cfg.image} alt={cfg.label} draggable={false} />
        </div>

        {grid && (
          <svg className="dune-map-grid" aria-hidden>
            {grid.ticks.map((i) => (
              <line
                key={`v${i}`}
                x1={grid.x0 + i * grid.stepX}
                y1={grid.y0}
                x2={grid.x0 + i * grid.stepX}
                y2={grid.y0 + screenH}
              />
            ))}
            {grid.ticks.map((i) => (
              <line
                key={`h${i}`}
                x1={grid.x0}
                y1={grid.y0 + i * grid.stepY}
                x2={grid.x0 + screenW}
                y2={grid.y0 + i * grid.stepY}
              />
            ))}
            {grid.showLabels &&
              grid.cells.map((c) => (
                <text key={c.key} x={c.left} y={c.top}>
                  {c.key}
                </text>
              ))}
          </svg>
        )}

        <div className="pw-map-markers">
          {markers.map((m) => (
            <MarkerDot
              key={m.id}
              pos={project(m.x, m.y)}
              label={m.name}
              sector={sectorFor(m.x, m.y, cfg)}
              online={m.online}
              selected={!!m.fls && m.fls === selectedFls}
              disabled={!m.fls}
              onClick={() => m.fls && onSelectPlayer(m.fls)}
            />
          ))}
          {mapLocations.map((l) => {
            const p = project(l.x, l.y);
            return (
              <div
                key={l.name}
                className="dune-map-pin"
                style={{ left: p.left, top: p.top }}
                title={l.name}
              >
                ⌖
              </div>
            );
          })}
          {pending && (
            <div
              className="dune-map-pin pending"
              style={{ ...project(pending.x, pending.y) }}
            >
              ✛
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MarkerDot({
  pos,
  label,
  sector,
  online,
  selected,
  disabled,
  onClick,
}: {
  pos: { left: number; top: number };
  label: string;
  sector: string;
  online: boolean;
  selected: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  const where = sector ? ` · sector ${sector}` : "";
  return (
    <button
      type="button"
      className={`pw-map-marker player${selected ? " selected" : ""}${online ? "" : " dune-map-offline"}`}
      style={{ left: pos.left, top: pos.top }}
      title={`${label} (${online ? "online" : "offline"})${where}`}
      disabled={disabled}
      onPointerDown={(e) => e.stopPropagation()}
      onPointerUp={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <span className="pw-map-marker-dot" />
      <span className="pw-map-marker-label">
        {label}
        {sector ? ` (${sector})` : ""}
      </span>
    </button>
  );
}
