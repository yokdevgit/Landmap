# Landmap MCP Server — Known Issues & Improvements

Running backlog of reliability, completeness, portability, and correctness items.
Newest / highest-impact first. See git history for what has already been fixed.

## Reliability

### DOL IP-based rate-limiting under rapid/successive fetches  ⚠️ (biggest gap)
- **Symptom:** the first fetch of a session works; after ~3–4 back-to-back
  fetches, subsequent fetches on *any* area (even one that just succeeded) get
  **0 tiles**. Every parcel double-click returns "no popup" or "ตกลง / no data",
  and the run ends with `Target page, context or browser has been closed`
  mid-scan.
- **Cause:** `landsmaps.dol.go.th` sits behind **Incapsula** bot detection.
  The "fresh incognito browser = no rate limit" assumption only defeats
  *cookie*-based limits — it does **not** defeat **IP-based** rate limiting.
  A burst of automated sessions from one IP gets throttled/soft-blocked: the
  parcel-query backend returns empty and the session is torn down.
- **Proven:** Ubon Pathum returned 260 tiles on the 1st attempt and 0 on the
  2nd and 3rd, same area, minutes apart — temporal, not spatial.
- **Workarounds today:** space fetches out (don't batch), and wait for the
  throttle to expire (minutes to tens of minutes) before retrying.
- **Fixes to build:**
  - Detect the block explicitly (Incapsula markers / mid-scan page-close) and
    surface a clear "rate-limited, back off" error instead of "0 tiles".
  - Whole-fetch **retry with exponential backoff** on browser/page close.
  - Optional pacing/cooldown between batched fetches.

### No whole-fetch crash recovery
- When the browser/page closes mid-scan, the run just saves 0 tiles — no retry,
  no distinction from a genuine no-data area. Add retry-with-backoff (above).

### Fixed-sleep timing is brittle
- The scan is driven by `asyncio.sleep(10/5/3/2/1/0.5)` throughout. On slow
  networks tiles can be missed; on fast ones it wastes time. Prefer
  event/network-idle/condition waits (as done for the Cesium-ready gate).

### Headless=False only
- `chromium.launch(headless=False)` requires a real desktop session and can
  require a human to solve a CAPTCHA. Cannot run headless / in CI.

## Data completeness

### WFS `maxFeatures=5000` cap per map sheet
- `tile_fetcher._fetch_wfs_features` caps at 5000 features per `utmmap`. Dense
  sheets are silently truncated (observed: every busy area hits exactly 5000).
  This also weakens the WFS-based "has a parcel here?" no-data retry filter in
  truncated areas. Consider paging (startIndex/count) to fetch all features.

### Overview-tile cutoff is a fixed threshold
- `tile_quality.MAX_TILE_SPAN_DEG` (~2 km) cleanly separates real tiles from
  Cesium overview junk at the current `zoom_level=17` / `ALTITUDE=1500`, but is
  hard-coded. Make it relative to the requested zoom if those change.

## Portability

- **GDAL discovery is Windows-only:** globs `C:\Program Files\QGIS*\bin` and
  `C:\OSGeo4W*\bin`. On Linux/macOS (or a non-standard install) the tile mosaic
  is silently skipped. Honor `LANDMAP_GDAL_BIN` / PATH first on all platforms.
- **Hardcoded QGIS version** `3.40.15-Bratislava` (and a fixed saveDateTime) in
  the generated `.qgs`; may not open cleanly in other QGIS versions.
- **Generated QGS bakes in the external OpenStreetMap XYZ basemap** → the
  project needs internet to open; no offline fallback.

## Correctness / cleanup

- **Legacy `process_to_gis` (QLR) CRS mismatch:** world files declare EPSG:4326
  while tiles are effectively Web Mercator — georeferences the raster wrongly.
  Prefer `process_to_shapefiles` (VRT → gdalwarp to EPSG:3857). Consider
  deprecating the legacy path.
- **README output path is wrong:** docs say `data/<session>.qgs`, but the
  project is written to **`gis/<session>.qgs`**.
- **Broken console script:** `pyproject [project.scripts] landmap-mcp =
  src.server:main` points at an `async def main()`; entry-point scripts call it
  synchronously, so it never runs. Add a sync wrapper (`def run():
  asyncio.run(main())`).
- **`get_boundary_bbox` format bug:** `f"{result.get('area_km2','N/A'):.2f}"`
  raises if `area_km2` is missing (float format on the string default).
- **Redundant launchers:** `main.py` and `run_server.py` are near-duplicates;
  the documented Claude Desktop config uses `python -m src.server`.

## Security (before making the repo public)

- **GitHub PAT in the git remote URL** (`.git/config`, local only, not in
  history): rotate the token and switch to a tokenless URL + credential manager.
- **`session_state.json` (DOL cookies) is in git *history*** (commit `8ddd895`),
  even though it is now untracked. Scrub it from history with `git filter-repo`
  / BFG before publishing.
- **`claude_desktop_config.example.json`** hardcodes a real Windows
  account/machine name (`PKO-X1-...`) — replace with a placeholder.

---

## Already fixed (this line of work)

- Persistent blank tiles root-caused and fixed: drop Cesium overview junk at
  capture; ink-fraction blank detector; direct-URL re-fetch of race blanks with
  camera re-fly fallback; wait for Cesium canvas/viewer (Incapsula load race);
  detach the capture listener during re-fetch; mosaic + legacy paths skip
  missing/blank tiles; don't retry blanks over no-parcel land (WFS filter);
  scan short-circuits cells DOL reports as no-parcel. Covered by
  `tests/test_tile_quality.py` (21 tests). See `src/tile_quality.py`.
