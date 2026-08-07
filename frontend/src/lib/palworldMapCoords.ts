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
 * Map-space CRS for World_Map.webp (order xy).
 * topLeft / bottomRight match Map:Fragments/Core on the wiki.
 */
export const MAP_CRS = {
  topLeft: { mapX: -1954.07407407, mapY: 1245.7254902 },
  bottomRight: { mapX: 1200.26143791, mapY: -1908.61002179 },
} as const;

const MAP_WIDTH = MAP_CRS.bottomRight.mapX - MAP_CRS.topLeft.mapX;
const MAP_HEIGHT = MAP_CRS.topLeft.mapY - MAP_CRS.bottomRight.mapY;

/**
 * Bundled basemap (served from /public).
 * Bump the query when the asset is re-encoded so browsers drop a stale cache.
 */
export const PALPAGOS_MAP_URL = "/palworld/palpagos-map.webp?v=8192q95";

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
