/** World-to-image frames for the four Dune admin maps.

Bounds and flip flags come from Icehunter/dune-admin (MIT) via the
Sergentval egg MapTab. They were validated against live pawn coords.
*/

export type DuneMapKey = "HaggaBasin" | "DeepDesert" | "Arrakeen" | "HarkoVillage";

export type DuneMapCfg = {
  key: DuneMapKey;
  label: string;
  image: string;
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  flipX?: boolean;
  flipY?: boolean;
};

export const DUNE_MAPS: DuneMapCfg[] = [
  {
    key: "HaggaBasin",
    label: "Hagga Basin",
    image: "/dune/hagga-basin.webp?v=1",
    minX: -437871,
    maxX: 350539,
    minY: -462011,
    maxY: 376267,
    flipY: true,
  },
  {
    key: "DeepDesert",
    label: "Deep Desert",
    image: "/dune/deepdesert.webp?v=1",
    minX: -1300000,
    maxX: 1200000,
    minY: -1300000,
    maxY: 1200000,
  },
  {
    key: "Arrakeen",
    label: "Arrakeen",
    image: "/dune/arrakeen.webp?v=1",
    minX: -32000,
    maxX: 17000,
    minY: -10000,
    maxY: 9500,
    flipY: true,
  },
  {
    key: "HarkoVillage",
    label: "Harko Village",
    image: "/dune/harko.webp?v=1",
    minX: -5000,
    maxX: 14500,
    minY: -5500,
    maxY: 32000,
  },
];

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
