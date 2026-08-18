import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { DuneMapLocation, DuneMapMarker } from "../api";
import {
  DUNE_MAPS,
  type DuneMapCfg,
  type DuneMapKey,
  pctToWorld,
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
  const innerRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef({ active: false, x: 0, y: 0, moved: false });

  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [mapKey]);

  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = vp.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setZoom((z) => {
        const next = Math.min(8, Math.max(0.4, z * factor));
        const k = next / z;
        setPan((p) => ({ x: cx - k * (cx - p.x), y: cy - k * (cy - p.y) }));
        return next;
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
    setPan((p) => ({ x: p.x + dx, y: p.y + dy }));
  }
  function onPointerUp(e: ReactPointerEvent) {
    const wasDrag = drag.current.moved;
    drag.current.active = false;
    if (!wasDrag && pickMode && innerRef.current) {
      const rect = innerRef.current.getBoundingClientRect();
      onPick(pctToWorld(e.clientX, e.clientY, rect, cfg));
    }
  }

  const mapLocations = locations.filter((l) => l.map === mapKey);

  return (
    <div className="pw-map dune-map">
      <div className="pw-map-toolbar">
        <div className="pw-map-tools">
          <button type="button" className="btn small ghost" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>
            Reset view
          </button>
          <span className="pw-map-zoom-label">{Math.round(zoom * 100)}%</span>
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
          ref={innerRef}
          className="pw-map-stage"
          style={{
            width: 760,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "top left",
          }}
        >
          <img className="pw-map-image" src={cfg.image} alt={cfg.label} draggable={false} />
          <div className="pw-map-markers">
            {markers.map((m) => (
              <MarkerDot
                key={m.id}
                cfg={cfg}
                x={m.x}
                y={m.y}
                label={m.name}
                online={m.online}
                selected={!!m.fls && m.fls === selectedFls}
                disabled={!m.fls}
                zoom={zoom}
                onClick={() => m.fls && onSelectPlayer(m.fls)}
              />
            ))}
            {mapLocations.map((l) => {
              const p = worldToPct(l.x, l.y, cfg);
              return (
                <div
                  key={l.name}
                  className="dune-map-pin"
                  style={{ left: `${p.left}%`, top: `${p.top}%`, fontSize: 14 / zoom }}
                  title={l.name}
                >
                  ⌖
                </div>
              );
            })}
            {pending && (
              <div
                className="dune-map-pin pending"
                style={{
                  left: `${worldToPct(pending.x, pending.y, cfg).left}%`,
                  top: `${worldToPct(pending.x, pending.y, cfg).top}%`,
                  fontSize: 16 / zoom,
                }}
              >
                ✛
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function MarkerDot({
  cfg,
  x,
  y,
  label,
  online,
  selected,
  disabled,
  zoom,
  onClick,
}: {
  cfg: DuneMapCfg;
  x: number;
  y: number;
  label: string;
  online: boolean;
  selected: boolean;
  disabled: boolean;
  zoom: number;
  onClick: () => void;
}) {
  const p = worldToPct(x, y, cfg);
  const size = Math.max(8, 10 / zoom);
  return (
    <button
      type="button"
      className={`pw-map-marker player${selected ? " selected" : ""}${online ? "" : " dune-map-offline"}`}
      style={{ left: `${p.left}%`, top: `${p.top}%` }}
      title={`${label} (${online ? "online" : "offline"})`}
      disabled={disabled}
      onPointerDown={(e) => e.stopPropagation()}
      onPointerUp={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <span className="pw-map-marker-dot" style={{ width: size, height: size }} />
      <span className="pw-map-marker-label">{label}</span>
    </button>
  );
}
