/** World-to-image frames for the four Dune admin maps.

Bounds and flip flags come from Icehunter/dune-admin (MIT) via the
Sergentval egg MapTab. They were validated against live pawn coords.

`liveData` records which maps the game actually reports pawn positions for:
`dune.actors` rows exist for Hagga Basin and the Deep Desert, and upstream's
own config marks Arrakeen and Harko Village `hasLiveData: false`. The egg
confirms it — `/api/map/markers` returns an empty list for both. They stay
selectable as reference maps and the panel says so instead of looking broken.

Both upstreams letterbox Arrakeen and Harko Village into a 512 x 512 canvas
and apply the bounds across the whole square, so the bundled images carried
wide black bars. We ship them cropped to their content, with the bounds
shrunk by the same fraction — see scripts note in ATTRIBUTION.txt. Every
projection is pixel-identical to upstream's; only the padding is gone.
`imgW` / `imgH` therefore differ per map and the viewer fits each aspect.
*/

export type DuneMapKey = "HaggaBasin" | "DeepDesert" | "Arrakeen" | "HarkoVillage";

export type DuneMapCfg = {
  key: DuneMapKey;
  label: string;
  image: string;
  /** Natural pixel size of the bundled image; sets the stage aspect. */
  imgW: number;
  imgH: number;
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  flipX?: boolean;
  flipY?: boolean;
  /** The egg reports pawn positions on this map (markers can be trusted). */
  liveData: boolean;
  /** Deep Desert is addressed by sector, not by coordinate. */
  sectorGrid?: boolean;
};

export const DUNE_MAPS: DuneMapCfg[] = [
  {
    key: "HaggaBasin",
    label: "Hagga Basin",
    image: "/dune/hagga-basin.webp?v=1",
    imgW: 512,
    imgH: 512,
    minX: -437871,
    maxX: 350539,
    minY: -462011,
    maxY: 376267,
    flipY: true,
    liveData: true,
  },
  {
    key: "DeepDesert",
    label: "Deep Desert",
    image: "/dune/deepdesert.webp?v=1",
    imgW: 512,
    imgH: 512,
    minX: -1300000,
    maxX: 1200000,
    minY: -1300000,
    maxY: 1200000,
    liveData: true,
    sectorGrid: true,
  },
  {
    // Cropped from 512 x 512 (content was the top 292 rows).
    key: "Arrakeen",
    label: "Arrakeen",
    image: "/dune/arrakeen.webp?v=2",
    imgW: 512,
    imgH: 292,
    minX: -32000,
    maxX: 17000,
    minY: -10000,
    maxY: 1121.09375,
    flipY: true,
    liveData: false,
  },
  {
    // Cropped from 512 x 512 (content was the top-left 320 x 320).
    key: "HarkoVillage",
    label: "Harko Village",
    image: "/dune/harko.webp?v=2",
    imgW: 320,
    imgH: 320,
    minX: -5000,
    maxX: 7187.5,
    minY: 8562.5,
    maxY: 32000,
    liveData: false,
  },
];

/** Deep Desert sector grid: 9 x 9 over the full map bounds.

Rows run A at the south edge to I at the north and columns 1-9 west to east,
which is both the game's own labelling (A-E is the PvE half, F-I the PvP half)
and what dune-admin's ZoneGridLayer draws. */
export const DD_GRID = 9;
export const DD_ROWS = ["A", "B", "C", "D", "E", "F", "G", "H", "I"];

export function sectorFor(x: number, y: number, cfg: DuneMapCfg): string {
  if (!cfg.sectorGrid) return "";
  const col = Math.floor(((x - cfg.minX) / (cfg.maxX - cfg.minX)) * DD_GRID);
  const row = Math.floor(((y - cfg.minY) / (cfg.maxY - cfg.minY)) * DD_GRID);
  if (col < 0 || col >= DD_GRID || row < 0 || row >= DD_GRID) return "";
  return `${DD_ROWS[row]}${col + 1}`;
}

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

export function worldToPct(
  x: number,
  y: number,
  cfg: DuneMapCfg
): { left: number; top: number } {
  const normX = (x - cfg.minX) / (cfg.maxX - cfg.minX);
  const normY = (y - cfg.minY) / (cfg.maxY - cfg.minY);
  const fracX = clamp01(cfg.flipX ? 1 - normX : normX);
  const fracYup = clamp01(cfg.flipY ? 1 - normY : normY);
  return { left: fracX * 100, top: (1 - fracYup) * 100 };
}

export function pctToWorld(
  clientX: number,
  clientY: number,
  rect: DOMRect,
  cfg: DuneMapCfg
): { x: number; y: number } {
  const fracX = clamp01((clientX - rect.left) / rect.width);
  const fracYup = 1 - clamp01((clientY - rect.top) / rect.height);
  const rawX = cfg.flipX ? 1 - fracX : fracX;
  const rawY = cfg.flipY ? 1 - fracYup : fracYup;
  return {
    x: Math.round(rawX * (cfg.maxX - cfg.minX) + cfg.minX),
    y: Math.round(rawY * (cfg.maxY - cfg.minY) + cfg.minY),
  };
}
