/**
 * Project Palworld world (UE / save / REST LocationX,Y) coordinates onto the
 * in-game Palpagos map image.
 *
 * Transform is the same one the wiki DataMaps use, derived from
 * DT_WorldMapUIData.json:
 *   https://palworld.wiki.gg/wiki/Maps
 *
 * World axes are flipped into map axes:
 *   mapX = (worldY - 158000) / 459
 *   mapY = (worldX + 123888) / 459
 *
 * The 459 units-per-map-unit scale is unchanged since launch; only the framing
 * of the basemap image differs between sources, which is what MAP_CRS encodes.
 *
 * The basemap image covers map-space top-left → bottom-right as below.
 */

export type WorldPoint = { x: number; y: number };
export type MapPoint = { mapX: number; mapY: number };
/** Normalized image position: (0,0) top-left, (1,1) bottom-right. */
export type NormalizedPoint = { u: number; v: number };

/** Shift / scale from palworld-coord + wiki docs. */
export const WORLD_TO_MAP_TRANSLATE_X = 123888;
export const WORLD_TO_MAP_TRANSLATE_Y = 158000;
export const WORLD_TO_MAP_SCALE = 459;

/**
 * Map-space CRS for the bundled basemap (order xy).
 *
 * The image is paldb.cc's Palworld 1.0 render, whose extent is the landscape
 * bounds rather than the wiki's DT_WorldMapUIData frame:
 *   worldX ∈ [-1099400, 349400], worldY ∈ [-724400, 724400]
 * Divided through by the 459 scale that gives, exactly:
 *   topLeft     = (-882400 / 459,  473288 / 459)
 *   bottomRight = ( 566400 / 459, -975512 / 459)
 * Verified against 1.0 boss/incident coordinates and by phase-correlating this
 * image against the previous wiki basemap (predicted offset -82.1 / -557.0 px
 * at 8192², measured -72 / -560).
 *
 * If we ever move back to the wiki's World_Map.webp, its frame is
 *   topLeft { -1954.07407407, 1245.7254902 }, bottomRight { 1200.26143791, -1908.61002179 }.
 */
export const MAP_CRS = {
  topLeft: { mapX: -1922.44008715, mapY: 1031.12854031 },
  bottomRight: { mapX: 1233.98692810, mapY: -2125.29847495 },
} as const;

const MAP_WIDTH = MAP_CRS.bottomRight.mapX - MAP_CRS.topLeft.mapX;
const MAP_HEIGHT = MAP_CRS.topLeft.mapY - MAP_CRS.bottomRight.mapY;

/**
 * Bundled basemap (served from /public).
 * Bump the query when the asset is re-encoded so browsers drop a stale cache.
 */
export const PALPAGOS_MAP_URL = "/palworld/palpagos-map.webp?v=1.0-8192q95";

export function worldToMap(worldX: number, worldY: number): MapPoint {
  return {
    mapX: (worldY - WORLD_TO_MAP_TRANSLATE_Y) / WORLD_TO_MAP_SCALE,
    mapY: (worldX + WORLD_TO_MAP_TRANSLATE_X) / WORLD_TO_MAP_SCALE,
  };
}

export function mapToWorld(mapX: number, mapY: number): WorldPoint {
  return {
    x: mapY * WORLD_TO_MAP_SCALE - WORLD_TO_MAP_TRANSLATE_X,
    y: mapX * WORLD_TO_MAP_SCALE + WORLD_TO_MAP_TRANSLATE_Y,
  };
}

export function mapToNormalized(mapX: number, mapY: number): NormalizedPoint {
  return {
    u: (mapX - MAP_CRS.topLeft.mapX) / MAP_WIDTH,
    v: (MAP_CRS.topLeft.mapY - mapY) / MAP_HEIGHT,
  };
}

export function normalizedToMap(u: number, v: number): MapPoint {
  return {
    mapX: MAP_CRS.topLeft.mapX + u * MAP_WIDTH,
    mapY: MAP_CRS.topLeft.mapY - v * MAP_HEIGHT,
  };
}

/** Full pipeline: REST LocationX/Y → fraction of the basemap image. */
export function worldToNormalized(worldX: number, worldY: number): NormalizedPoint {
  const { mapX, mapY } = worldToMap(worldX, worldY);
  return mapToNormalized(mapX, mapY);
}

export function normalizedToWorld(u: number, v: number): WorldPoint {
  const { mapX, mapY } = normalizedToMap(u, v);
  return mapToWorld(mapX, mapY);
}

/** True when the point projects onto the basemap (with a small margin). */
export function isOnMap(worldX: number, worldY: number, margin = 0.05): boolean {
  const { u, v } = worldToNormalized(worldX, worldY);
  return u >= -margin && u <= 1 + margin && v >= -margin && v <= 1 + margin;
}
