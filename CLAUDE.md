# Agent instructions

## Palworld world map asset

The interactive Palworld map (`frontend/src/components/PalworldWorldMap.tsx`) draws
live player / base-camp positions on a single bundled basemap image,
`frontend/public/palworld/palpagos-map.webp` (8192 x 8192 WebP).

Two known sources for that image — check both when the game ships new geography:

1. **paldb.cc** (current source, Palworld 1.0)
   - Page: https://paldb.cc/en/Palpagos_Islands
   - Tiles: `https://cdn.paldb.cc/image/map8/z{z}x{x}y{y}.webp`, 512 px tiles,
     zoom 0-4; zoom 4 is a 16 x 16 grid = 8192 x 8192. Stitch and re-encode as
     WebP q95. A `Last-Modified` HEAD on `z0x0y0.webp` tells you when paldb
     last re-rendered (2026-07-10 for 1.0).
   - Also hosts a separate World Tree map at `image/treemap8/`. We do not use
     it yet — the World Tree is a distinct map, and whether its actors report
     coordinates in the Palpagos world space is unverified.
   - Extent comes from the landscape bounds in `https://paldb.cc/js/map_data_en.js`
     (`landScapeRealPositionMin/Max`), not from the wiki's map-UI frame.
2. **palworld.wiki.gg** (previous source)
   - File: https://palworld.wiki.gg/wiki/File:World_Map.webp — check for new
     revisions via `https://palworld.wiki.gg/api.php?action=query&titles=File:World%20Map.webp&prop=imageinfo&iiprop=timestamp|size&format=json`.
     As of 2026-08-08 it still has only the 2025-12-09 (pre-1.0) revision.
   - Projection docs: https://palworld.wiki.gg/wiki/Maps and the raw fragment
     `https://palworld.wiki.gg/index.php?title=Map:Fragments/Core&action=raw`.
   - Preferred long term: it is the wiki's own datamined export and matches the
     documented DT_WorldMapUIData frame directly.

**The two sources frame the world differently.** Swapping the image alone
misplaces every marker. `MAP_CRS` in `frontend/src/lib/palworldMapCoords.ts`
must change with it; both sets of constants are recorded in that file, and
`ATTRIBUTION.txt` next to the image records provenance. The world-to-map scale
(459 units per map unit) has been stable across game versions — only the
framing changes.

When replacing the image, bump the `?v=` cache-buster on `PALPAGOS_MAP_URL` and
re-verify alignment rather than trusting the constants: phase-correlate the new
image against the old one and check the measured offset matches what the new
constants predict, then project known 1.0 boss/incident coordinates from
`map_data_en.js` and confirm they land on land.
