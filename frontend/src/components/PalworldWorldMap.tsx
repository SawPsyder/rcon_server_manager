import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type {
  PalworldBaseCamp,
  PalworldMapEntity,
  PalworldWorld,
  PalworldWorldPlayer,
} from "../api";
import {
  PALPAGOS_MAP_URL,
  isOnMap,
  normalizedToWorld,
  worldToNormalized,
} from "../lib/palworldMapCoords";

export type MapSelection =
  | { kind: MarkerKind; id: string }
  | null;

export type MarkerKind =
  | "player"
  | "camp"
  | "worker"
  | "wild"
  | "npc"
  | "otomo";

/** Imperative API for the admin tables (double-click → fly to location). */
export type PalworldWorldMapHandle = {
  focusOn: (
    u: number,
    v: number,
    kind?: MarkerKind,
    id?: string
  ) => void;
};

type Props = {
  world: PalworldWorld;
  selected: MapSelection;
  onSelect: (sel: MapSelection) => void;
  /** Full-viewport layout for share page / dedicated map tab. */
  variant?: "embedded" | "fullscreen";
  /** Extra toolbar controls (share, full screen) rendered by the parent. */
  toolbarExtra?: ReactNode;
};

const FOCUS_ZOOM = 30;
const FOCUS_MS = 700;
const MIN_ZOOM = 1;
const MAX_ZOOM = 30;
const ZOOM_STEP = 1.18;
const DEFAULT_MAP_PX = 8192;
const MARKER_SMOOTH_TAU = 0.35;
/** Show text labels for dense layers at this zoom and above. */
const LABEL_ZOOM = 3.5;

/** Higher draws on top. Players always above bases, party, NPCs, workers, wild. */
const MARKER_Z: Record<MarkerKind, number> = {
  wild: 10,
  worker: 20,
  npc: 30,
  otomo: 40,
  camp: 50,
  player: 60,
};

type LayerKey = "players" | "camps" | "workers" | "wild" | "npcs" | "otomo";

type Marker = {
  id: string;
  kind: MarkerKind;
  label: string;
  detail: string;
  u: number;
  v: number;
  rotationZ: number | null;
  offMap: boolean;
  level: number | null;
  guild: string;
};

type SmoothPos = { u: number; v: number };

const LAYER_META: {
  key: LayerKey;
  kind: MarkerKind;
  label: string;
  defaultOn: boolean;
}[] = [
  { key: "players", kind: "player", label: "Players", defaultOn: true },
  { key: "camps", kind: "camp", label: "Bases", defaultOn: true },
  { key: "workers", kind: "worker", label: "Workers", defaultOn: false },
  { key: "wild", kind: "wild", label: "Wild", defaultOn: false },
  { key: "npcs", kind: "npc", label: "NPCs", defaultOn: true },
  { key: "otomo", kind: "otomo", label: "Party", defaultOn: true },
];

function playerId(p: PalworldWorldPlayer, index: number): string {
  return p.user_id || `player-${index}-${p.name}`;
}

export function mapPlayerKey(p: PalworldWorldPlayer, index: number): string {
  return playerId(p, index);
}

export function mapCampKey(c: PalworldBaseCamp, index: number): string {
  return c.id || (c.guild_id ? `camp-${c.guild_id}-${index}` : `camp-${index}`);
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

function panToCenter(u: number, v: number, zoom: number, side: number) {
  return {
    x: -(u - 0.5) * side * zoom,
    y: -(v - 0.5) * side * zoom,
  };
}

function entityDetail(e: PalworldMapEntity): string {
  return [
    e.level != null ? `Lv ${e.level}` : null,
    e.hp != null ? `HP ${e.hp}/${e.max_hp ?? "?"}` : null,
    e.guild_name || null,
    e.activity || null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function buildMarkers(world: PalworldWorld): Marker[] {
  const out: Marker[] = [];

  (world.players || []).forEach((p, i) => {
    if (p.location_x == null || p.location_y == null) return;
    const { u, v } = worldToNormalized(p.location_x, p.location_y);
    out.push({
      id: playerId(p, i),
      kind: "player",
      label: p.name || "Player",
      detail: [
        p.level != null ? `Lv ${p.level}` : null,
        p.guild_name || null,
        p.hp != null ? `HP ${p.hp}/${p.max_hp ?? "?"}` : null,
        p.pal_count ? `${p.pal_count} pals` : null,
      ]
        .filter(Boolean)
        .join(" · "),
      u,
      v,
      rotationZ: p.rotation_z ?? null,
      offMap: !isOnMap(p.location_x, p.location_y),
      level: p.level,
      guild: p.guild_name || "",
    });
  });

  (world.base_camps || []).forEach((c, i) => {
    if (c.location_x == null || c.location_y == null) return;
    const { u, v } = worldToNormalized(c.location_x, c.location_y);
    const gid = c.guild_id || "";
    out.push({
      id: mapCampKey(c, i),
      kind: "camp",
      label: c.guild_name || "Base camp",
      detail: gid ? `Guild ${gid.slice(0, 8)}…` : "Base camp",
      u,
      v,
      rotationZ: null,
      offMap: !isOnMap(c.location_x, c.location_y),
      level: null,
      guild: c.guild_name || "",
    });
  });

  const pushEntities = (
    list: PalworldMapEntity[] | undefined,
    kind: MarkerKind
  ) => {
    (list || []).forEach((e) => {
      if (e.location_x == null || e.location_y == null) return;
      const { u, v } = worldToNormalized(e.location_x, e.location_y);
      out.push({
        id: e.id || `${kind}-${e.location_x}-${e.location_y}`,
        kind,
        label: e.name || e.species || kind,
        detail: entityDetail(e),
        u,
        v,
        rotationZ: e.rotation_z ?? null,
        offMap: !isOnMap(e.location_x, e.location_y),
        level: e.level,
        guild: e.guild_name || "",
      });
    });
  };

  pushEntities(world.workers, "worker");
  pushEntities(world.wild_pals, "wild");
  pushEntities(world.npcs, "npc");
  pushEntities(world.otomo_pals, "otomo");

  // Paint order matches z-index hierarchy (wild first … players last).
  const order: Record<MarkerKind, number> = {
    wild: 0,
    worker: 1,
    npc: 2,
    otomo: 3,
    camp: 4,
    player: 5,
  };
  out.sort((a, b) => order[a.kind] - order[b.kind] || a.id.localeCompare(b.id));

  return out;
}

const PalworldWorldMap = forwardRef<PalworldWorldMapHandle, Props>(
  function PalworldWorldMap(
    { world, selected, onSelect, variant = "embedded", toolbarExtra },
    ref
  ) {
    const viewportRef = useRef<HTMLDivElement>(null);
    const [zoom, setZoom] = useState(1);
    const [pan, setPan] = useState({ x: 0, y: 0 });
    const [layers, setLayers] = useState<Record<LayerKey, boolean>>(() =>
      Object.fromEntries(LAYER_META.map((l) => [l.key, l.defaultOn])) as Record<
        LayerKey,
        boolean
      >
    );
    const [mapPx, setMapPx] = useState(DEFAULT_MAP_PX);
    const [viewportSide, setViewportSide] = useState(560);
    const [viewportBox, setViewportBox] = useState({ w: 0, h: 0 });
    const [cursorWorld, setCursorWorld] = useState<{
      x: number;
      y: number;
    } | null>(null);
    const [followingId, setFollowingId] = useState<string | null>(null);
    const [legendOpen, setLegendOpen] = useState(false);
    const [, setSmoothFrame] = useState(0);

    const dragRef = useRef<{
      pointerId: number;
      startX: number;
      startY: number;
      originX: number;
      originY: number;
      moved: boolean;
    } | null>(null);
    const animRef = useRef<number | null>(null);
    const smoothRafRef = useRef<number | null>(null);
    const viewRef = useRef({ zoom: 1, pan: { x: 0, y: 0 }, viewportSide: 560 });
    viewRef.current = { zoom, pan, viewportSide };
    const followingIdRef = useRef<string | null>(null);
    followingIdRef.current = followingId;
    const targetRef = useRef<Map<string, SmoothPos>>(new Map());
    const smoothRef = useRef<Map<string, SmoothPos>>(new Map());
    const lastFrameRef = useRef(performance.now());
    const reduceMotion = useMemo(() => prefersReducedMotion(), []);

    const markers = useMemo(() => buildMarkers(world), [world]);

    const counts = useMemo(() => {
      const c: Record<LayerKey, number> = {
        players: 0,
        camps: 0,
        workers: 0,
        wild: 0,
        npcs: 0,
        otomo: 0,
      };
      for (const m of markers) {
        if (m.offMap) continue;
        if (m.kind === "player") c.players++;
        else if (m.kind === "camp") c.camps++;
        else if (m.kind === "worker") c.workers++;
        else if (m.kind === "wild") c.wild++;
        else if (m.kind === "npc") c.npcs++;
        else if (m.kind === "otomo") c.otomo++;
      }
      return c;
    }, [markers]);

    useEffect(() => {
      const next = new Map<string, SmoothPos>();
      for (const m of markers) {
        if (m.offMap) continue;
        next.set(m.id, { u: m.u, v: m.v });
        if (!smoothRef.current.has(m.id)) {
          smoothRef.current.set(m.id, { u: m.u, v: m.v });
        }
      }
      for (const id of [...smoothRef.current.keys()]) {
        if (!next.has(id)) smoothRef.current.delete(id);
      }
      targetRef.current = next;
    }, [markers]);

    useEffect(() => {
      lastFrameRef.current = performance.now();
      const tau = reduceMotion ? 0.08 : MARKER_SMOOTH_TAU;
      const step = (now: number) => {
        const dt = Math.min(0.05, (now - lastFrameRef.current) / 1000);
        lastFrameRef.current = now;
        const alpha = 1 - Math.exp(-dt / tau);
        let moved = false;
        for (const [id, target] of targetRef.current) {
          const cur = smoothRef.current.get(id) ?? target;
          const u = cur.u + (target.u - cur.u) * alpha;
          const v = cur.v + (target.v - cur.v) * alpha;
          const snap =
            Math.abs(u - target.u) < 1e-5 && Math.abs(v - target.v) < 1e-5;
          const next = snap ? target : { u, v };
          const prev = smoothRef.current.get(id);
          if (!prev || prev.u !== next.u || prev.v !== next.v) {
            smoothRef.current.set(id, next);
            moved = true;
          }
        }
        const followId = followingIdRef.current;
        if (followId) {
          const pos = smoothRef.current.get(followId);
          if (pos) {
            const { zoom: z, viewportSide: side } = viewRef.current;
            const p = panToCenter(pos.u, pos.v, z, side || 560);
            viewRef.current = {
              zoom: z,
              pan: p,
              viewportSide: viewRef.current.viewportSide,
            };
            setPan(p);
            moved = true;
          }
        }
        if (moved) setSmoothFrame((n) => n + 1);
        smoothRafRef.current = requestAnimationFrame(step);
      };
      smoothRafRef.current = requestAnimationFrame(step);
      return () => {
        if (smoothRafRef.current != null) {
          cancelAnimationFrame(smoothRafRef.current);
        }
      };
    }, [reduceMotion]);

    const layerOn = useCallback(
      (kind: MarkerKind) => {
        const meta = LAYER_META.find((l) => l.kind === kind);
        return meta ? layers[meta.key] : true;
      },
      [layers]
    );

    const visible = useMemo(
      () => markers.filter((m) => !m.offMap && layerOn(m.kind)),
      [markers, layerOn]
    );

    const fitScale = mapPx > 0 ? viewportSide / mapPx : 1;
    const scale = fitScale * zoom;
    const mapScreenSide = viewportSide * zoom;
    const showDenseLabels = zoom >= LABEL_ZOOM;

    useEffect(() => {
      const el = viewportRef.current;
      if (!el || typeof ResizeObserver === "undefined") return;
      const measure = () => {
        const rect = el.getBoundingClientRect();
        setViewportBox({ w: rect.width, h: rect.height });
        setViewportSide(Math.min(rect.width, rect.height) || 560);
      };
      measure();
      const ro = new ResizeObserver(measure);
      ro.observe(el);
      return () => ro.disconnect();
    }, []);

    const cancelAnim = useCallback(() => {
      if (animRef.current != null) {
        cancelAnimationFrame(animRef.current);
        animRef.current = null;
      }
    }, []);

    const stopFollow = useCallback(() => setFollowingId(null), []);

    const resetView = useCallback(() => {
      cancelAnim();
      stopFollow();
      setZoom(1);
      setPan({ x: 0, y: 0 });
      viewRef.current = {
        zoom: 1,
        pan: { x: 0, y: 0 },
        viewportSide: viewRef.current.viewportSide,
      };
    }, [cancelAnim, stopFollow]);

    const animateTo = useCallback(
      (u: number, v: number, targetZoom = FOCUS_ZOOM) => {
        cancelAnim();
        const from = viewRef.current;
        const side = from.viewportSide || 560;
        const toZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, targetZoom));
        const toPan = panToCenter(u, v, toZoom, side);
        if (reduceMotion) {
          viewRef.current = {
            zoom: toZoom,
            pan: toPan,
            viewportSide: viewRef.current.viewportSide,
          };
          setZoom(toZoom);
          setPan(toPan);
          return;
        }
        const t0 = performance.now();
        const fromZoom = from.zoom;
        const fromPan = { ...from.pan };
        const tick = (now: number) => {
          const t = Math.min(1, (now - t0) / FOCUS_MS);
          const e = easeInOutCubic(t);
          const z = fromZoom + (toZoom - fromZoom) * e;
          const p = {
            x: fromPan.x + (toPan.x - fromPan.x) * e,
            y: fromPan.y + (toPan.y - fromPan.y) * e,
          };
          viewRef.current = {
            zoom: z,
            pan: p,
            viewportSide: viewRef.current.viewportSide,
          };
          setZoom(z);
          setPan(p);
          if (t < 1) animRef.current = requestAnimationFrame(tick);
          else animRef.current = null;
        };
        animRef.current = requestAnimationFrame(tick);
      },
      [cancelAnim, reduceMotion]
    );

    useImperativeHandle(
      ref,
      () => ({
        focusOn(u, v, kind?, id?) {
          if (kind === "player" || kind === "otomo") {
            setLayers((L) => ({ ...L, players: true, otomo: true }));
          }
          if (kind === "camp" || kind === "worker") {
            setLayers((L) => ({ ...L, camps: true, workers: true }));
          }
          if (kind === "wild") setLayers((L) => ({ ...L, wild: true }));
          if (kind === "npc") setLayers((L) => ({ ...L, npcs: true }));
          if (kind === "player" && id) {
            setFollowingId(id);
            smoothRef.current.set(id, { u, v });
            targetRef.current.set(id, { u, v });
          } else {
            setFollowingId(null);
          }
          viewportRef.current?.scrollIntoView({
            behavior: reduceMotion ? "auto" : "smooth",
            block: "nearest",
          });
          animateTo(u, v, FOCUS_ZOOM);
        },
      }),
      [animateTo, reduceMotion]
    );

    useEffect(() => () => cancelAnim(), [cancelAnim]);

    useEffect(() => {
      if (!followingId) return;
      if (!selected || selected.kind !== "player" || selected.id !== followingId) {
        setFollowingId(null);
      }
    }, [selected, followingId]);

    const followPlayer = useCallback(
      (m: Marker) => {
        const pos = smoothRef.current.get(m.id) ?? { u: m.u, v: m.v };
        setLayers((L) => ({ ...L, players: true }));
        onSelect({ kind: "player", id: m.id });
        setFollowingId(m.id);
        smoothRef.current.set(m.id, pos);
        targetRef.current.set(m.id, pos);
        animateTo(pos.u, pos.v, FOCUS_ZOOM);
      },
      [animateTo, onSelect]
    );

    const flyToMarker = useCallback(
      (m: Marker, follow: boolean) => {
        if (m.kind === "player" && follow) {
          followPlayer(m);
          return;
        }
        stopFollow();
        onSelect({ kind: m.kind, id: m.id });
        if (m.kind === "player") setLayers((L) => ({ ...L, players: true }));
        const pos = smoothRef.current.get(m.id) ?? { u: m.u, v: m.v };
        animateTo(pos.u, pos.v, Math.max(viewRef.current.zoom, 8));
      },
      [animateTo, followPlayer, onSelect, stopFollow]
    );

    /** Single click selects; double-click on a player starts follow + max zoom. */
    const markerClickRef = useRef<{ id: string; t: number } | null>(null);
    const onMarkerActivate = useCallback(
      (m: Marker) => {
        const now = performance.now();
        const last = markerClickRef.current;
        if (
          m.kind === "player" &&
          last &&
          last.id === m.id &&
          now - last.t < 400
        ) {
          markerClickRef.current = null;
          followPlayer(m);
          return;
        }
        markerClickRef.current = { id: m.id, t: now };
        if (followingId && followingId !== m.id) stopFollow();
        if (m.kind !== "player") stopFollow();
        onSelect({ kind: m.kind, id: m.id });
      },
      [followPlayer, followingId, onSelect, stopFollow]
    );

    const rosterClickRef = useRef<{ id: string; t: number } | null>(null);
    const onRosterPlayerClick = useCallback(
      (m: Marker) => {
        const now = performance.now();
        const last = rosterClickRef.current;
        if (last && last.id === m.id && now - last.t < 400) {
          rosterClickRef.current = null;
          followPlayer(m);
          return;
        }
        rosterClickRef.current = { id: m.id, t: now };
        flyToMarker(m, false);
      },
      [flyToMarker, followPlayer]
    );

    const playerRoster = useMemo(
      () =>
        markers
          .filter((m) => m.kind === "player" && !m.offMap)
          .slice()
          .sort((a, b) => a.label.localeCompare(b.label)),
      [markers]
    );

    const zoomAt = useCallback(
      (factor: number, clientX?: number, clientY?: number) => {
        cancelAnim();
        stopFollow();
        const el = viewportRef.current;
        const prevZoom = viewRef.current.zoom;
        const prevPan = viewRef.current.pan;
        const nextZoom = Math.min(
          MAX_ZOOM,
          Math.max(MIN_ZOOM, prevZoom * factor)
        );
        if (nextZoom === prevZoom) return;
        let nextPan = prevPan;
        const ratio = nextZoom / prevZoom;
        if (el != null && clientX != null && clientY != null) {
          const rect = el.getBoundingClientRect();
          const cx = clientX - rect.left - rect.width / 2;
          const cy = clientY - rect.top - rect.height / 2;
          nextPan = {
            x: cx - (cx - prevPan.x) * ratio,
            y: cy - (cy - prevPan.y) * ratio,
          };
        } else {
          nextPan = { x: prevPan.x * ratio, y: prevPan.y * ratio };
        }
        viewRef.current = {
          zoom: nextZoom,
          pan: nextPan,
          viewportSide: viewRef.current.viewportSide,
        };
        setZoom(nextZoom);
        setPan(nextPan);
      },
      [cancelAnim, stopFollow]
    );

    useEffect(() => {
      const el = viewportRef.current;
      if (!el) return;
      const onWheel = (e: WheelEvent) => {
        e.preventDefault();
        zoomAt(e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP, e.clientX, e.clientY);
      };
      el.addEventListener("wheel", onWheel, { passive: false });
      return () => el.removeEventListener("wheel", onWheel);
    }, [zoomAt]);

    const clientToNormalized = useCallback(
      (clientX: number, clientY: number) => {
        const el = viewportRef.current;
        if (!el) return null;
        const rect = el.getBoundingClientRect();
        const localX = clientX - rect.left - rect.width / 2 - pan.x;
        const localY = clientY - rect.top - rect.height / 2 - pan.y;
        const side = viewportSide * zoom;
        return {
          u: 0.5 + localX / side,
          v: 0.5 + localY / side,
        };
      },
      [pan.x, pan.y, zoom, viewportSide]
    );

    const onPointerDown = (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      cancelAnim();
      stopFollow();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      dragRef.current = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        originX: pan.x,
        originY: pan.y,
        moved: false,
      };
    };

    const onPointerMove = (e: React.PointerEvent) => {
      const norm = clientToNormalized(e.clientX, e.clientY);
      if (norm && norm.u >= 0 && norm.u <= 1 && norm.v >= 0 && norm.v <= 1) {
        setCursorWorld(normalizedToWorld(norm.u, norm.v));
      } else setCursorWorld(null);
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      const nextPan = { x: drag.originX + dx, y: drag.originY + dy };
      viewRef.current = { ...viewRef.current, pan: nextPan };
      setPan(nextPan);
    };

    const onPointerUp = (e: React.PointerEvent) => {
      const drag = dragRef.current;
      if (drag && drag.pointerId === e.pointerId) {
        if (!drag.moved) {
          stopFollow();
          onSelect(null);
        }
        dragRef.current = null;
      }
    };

    const displayPos = (m: Marker): SmoothPos =>
      smoothRef.current.get(m.id) ?? { u: m.u, v: m.v };

    const selectedMarker = selected
      ? visible.find((m) => m.kind === selected.kind && m.id === selected.id)
      : undefined;
    const followingLabel = followingId
      ? markers.find((m) => m.id === followingId)?.label
      : null;

    const setLayer = (key: LayerKey, on: boolean) =>
      setLayers((L) => ({ ...L, [key]: on }));

    const showEssentials = () =>
      setLayers({
        players: true,
        camps: true,
        workers: false,
        wild: false,
        npcs: true,
        otomo: true,
      });
    const showAll = () =>
      setLayers({
        players: true,
        camps: true,
        workers: true,
        wild: true,
        npcs: true,
        otomo: true,
      });

    const dayLabel =
      world.in_game_days != null
        ? `Day ${world.in_game_days.toLocaleString()}`
        : null;
    const timeLabel = world.in_game_time || null;

    const showRoster = variant === "fullscreen";

    return (
      <div className={`pw-map${variant === "fullscreen" ? " pw-map-fullscreen" : ""}`}>
        <div className="pw-map-toolbar">
          <div className="pw-map-layers">
            {LAYER_META.map((l) => {
              if (l.key === "otomo" && counts.otomo === 0) return null;
              const on = layers[l.key];
              return (
                <button
                  key={l.key}
                  type="button"
                  className={`pw-layer-chip ${l.key}${on ? " on" : ""}`}
                  aria-pressed={on}
                  onClick={() => setLayer(l.key, !on)}
                >
                  <span className={`pw-map-swatch ${l.kind === "camp" ? "camp" : l.kind}`} />
                  {l.label}
                  <span className="pw-layer-count">{counts[l.key]}</span>
                </button>
              );
            })}
            <div className="pw-layer-presets">
              <button type="button" className="btn small ghost" onClick={showEssentials}>
                Essentials
              </button>
              <button type="button" className="btn small ghost" onClick={showAll}>
                Show all
              </button>
            </div>
          </div>
          <div className="pw-map-tools">
            {followingId && (
              <span className="pw-map-following">
                Following {followingLabel || "player"}
                <button type="button" className="btn small ghost" onClick={stopFollow}>
                  Stop
                </button>
              </span>
            )}
            {toolbarExtra}
            <div className="pw-map-legend-wrap">
              <button
                type="button"
                className="btn small ghost"
                onClick={() => setLegendOpen((o) => !o)}
                aria-expanded={legendOpen}
              >
                Legend
              </button>
              {legendOpen && (
                <div className="pw-map-legend-pop" role="dialog">
                  <div><span className="pw-map-swatch player" /> Player</div>
                  <div><span className="pw-map-swatch camp" /> Base (PalBox)</div>
                  <div><span className="pw-map-swatch worker" /> Base worker</div>
                  <div><span className="pw-map-swatch wild" /> Wild pal</div>
                  <div><span className="pw-map-swatch npc" /> NPC</div>
                  <div><span className="pw-map-swatch otomo" /> Party pal</div>
                  <p className="muted" style={{ margin: "0.4rem 0 0", fontSize: "0.75rem" }}>
                    Double-click a player pin or list row to follow. Labels densify when zoomed in.
                  </p>
                </div>
              )}
            </div>
            <button type="button" className="btn small ghost" onClick={() => zoomAt(1 / ZOOM_STEP)}>
              −
            </button>
            <button type="button" className="btn small ghost" onClick={() => zoomAt(ZOOM_STEP)}>
              +
            </button>
            <button type="button" className="btn small ghost" onClick={resetView}>
              Reset
            </button>
            <span className="pw-map-zoom-label">{Math.round(zoom * 100)}%</span>
          </div>
        </div>

        <div className={`pw-map-body${showRoster ? " has-roster" : ""}`}>
          {showRoster && (
            <aside className="pw-map-roster" aria-label="Online players">
              <div className="pw-map-roster-head">
                <span className="pw-map-roster-title">Players</span>
                <span className="pw-map-roster-count">{playerRoster.length}</span>
              </div>
              {playerRoster.length === 0 ? (
                <p className="pw-map-roster-empty muted">No players online</p>
              ) : (
                <ul className="pw-map-roster-list">
                  {playerRoster.map((m) => {
                    const active =
                      selected?.kind === "player" && selected.id === m.id;
                    const tracking = followingId === m.id;
                    return (
                      <li
                        key={m.id}
                        className={`pw-map-roster-item${active ? " active" : ""}${
                          tracking ? " tracking" : ""
                        }`}
                      >
                        <button
                          type="button"
                          className="pw-map-roster-main"
                          onClick={() => onRosterPlayerClick(m)}
                          title="Click to fly · double-click to follow"
                        >
                          <span className="pw-map-roster-dot" aria-hidden />
                          <span className="pw-map-roster-info">
                            <span className="pw-map-roster-name">{m.label}</span>
                            {m.detail && (
                              <span className="pw-map-roster-meta">{m.detail}</span>
                            )}
                          </span>
                        </button>
                        {tracking ? (
                          <span className="pw-map-roster-badge">Live</span>
                        ) : (
                          <button
                            type="button"
                            className="pw-map-roster-follow"
                            title="Follow this player"
                            onClick={() => followPlayer(m)}
                          >
                            Follow
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              <p className="pw-map-roster-hint muted">
                Click to fly · double-click or Follow to track
              </p>
            </aside>
          )}

        <div
          ref={viewportRef}
          className={`pw-map-viewport${variant === "fullscreen" ? " pw-map-viewport-fill" : ""}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          role="application"
          aria-label="Palworld world map"
        >
          <div
            className="pw-map-stage"
            style={{
              width: mapPx,
              height: mapPx,
              marginLeft: -mapPx / 2,
              marginTop: -mapPx / 2,
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`,
            }}
          >
            <img
              className="pw-map-image"
              src={PALPAGOS_MAP_URL}
              alt="Palpagos Islands"
              draggable={false}
              decoding="async"
              onLoad={(e) => {
                const n = e.currentTarget.naturalWidth;
                if (n > 0) setMapPx(n);
              }}
            />
          </div>

          <div className="pw-map-markers" aria-hidden={visible.length === 0}>
            {visible.map((m) => {
              const isSelected =
                selected?.kind === m.kind && selected.id === m.id;
              const pos = displayPos(m);
              const x =
                (viewportBox.w || 0) / 2 + pan.x + (pos.u - 0.5) * mapScreenSide;
              const y =
                (viewportBox.h || 0) / 2 + pan.y + (pos.v - 0.5) * mapScreenSide;
              const showLabel =
                m.kind === "player" ||
                m.kind === "camp" ||
                isSelected ||
                followingId === m.id ||
                (showDenseLabels && (m.kind === "npc" || m.kind === "wild" || m.kind === "worker" || m.kind === "otomo"));
              const dense = m.kind === "worker" || m.kind === "wild" || m.kind === "otomo";
              // Selected / following always above their kind tier so they stay clickable.
              const z =
                MARKER_Z[m.kind] +
                (isSelected || followingId === m.id ? 100 : 0);
              return (
                <button
                  key={m.id}
                  type="button"
                  className={[
                    "pw-map-marker",
                    m.kind,
                    dense ? "dense" : "",
                    isSelected ? "selected" : "",
                    followingId === m.id ? "following" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  style={{
                    left: `${x}px`,
                    top: `${y}px`,
                    transform: "translate(-50%, -50%)",
                    zIndex: z,
                  }}
                  title={
                    m.kind === "player"
                      ? `${m.label}${m.detail ? ` — ${m.detail}` : ""} · Double-click to follow`
                      : `${m.label}${m.detail ? ` — ${m.detail}` : ""}`
                  }
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    onMarkerActivate(m);
                  }}
                >
                  <span className="pw-map-marker-dot" aria-hidden>
                    {m.kind === "player" && m.rotationZ != null && (
                      <span
                        className="pw-map-facing"
                        style={{ transform: `rotate(${m.rotationZ}deg)` }}
                      />
                    )}
                  </span>
                  {showLabel && (
                    <span className="pw-map-marker-label">{m.label}</span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="pw-map-hud">
            <div className="pw-map-hud-left">
              {(timeLabel || dayLabel) && (
                <span className="pw-map-clock">
                  {timeLabel}
                  {timeLabel && dayLabel ? " · " : ""}
                  {dayLabel}
                </span>
              )}
              {cursorWorld ? (
                <span className="muted">
                  {cursorWorld.x.toFixed(0)}, {cursorWorld.y.toFixed(0)}
                </span>
              ) : (
                <span className="muted">
                  Drag · scroll zoom
                  {showRoster ? " · double-click player to follow" : ""}
                </span>
              )}
            </div>
            <div className="pw-map-hud-right">
              {followingId && (
                <span className="pw-map-hud-follow">Tracking</span>
              )}
              {selectedMarker && (
                <span className="pw-map-hud-selected">
                  {selectedMarker.label}
                  {selectedMarker.detail ? ` · ${selectedMarker.detail}` : ""}
                </span>
              )}
            </div>
          </div>
        </div>
        </div>
      </div>
    );
  }
);

export default PalworldWorldMap;
