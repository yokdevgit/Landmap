"""
Tile Fetcher - Intercept actual WMS tiles from DOL website

This approach:
1. Opens browser to landsmaps.dol.go.th (visible for captcha)
2. Waits for user to solve captcha if needed
3. Navigates to target bbox using Cesium API
4. Intercepts WMS tile responses with their BBOX coordinates
5. Saves tiles with proper georeferencing
"""

import asyncio
import base64
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse, unquote

from playwright.async_api import async_playwright, Page, BrowserContext

from .tile_quality import (
    is_blank_tile,
    is_overview_tile,
    refetch_blank_tiles,
    load_parcel_bboxes,
    bbox_overlaps_any,
    spread_click_points,
    clicks_per_cell,
    attempt_offset,
    native_epsg_for_layer,
    bbox_to_native,
)


def log(msg: str):
    """Log to stderr so we don't break MCP JSON-RPC on stdout."""
    print(msg, file=sys.stderr)


class TileFetcher:
    """Fetch land map tiles by intercepting actual website requests."""

    DOL_URL = "https://landsmaps.dol.go.th"
    TILE_SIZE = 256
    MAX_TILES_PER_SESSION = 2000

    def __init__(self):
        self.tiles: list[dict] = []
        self.captured_urls: set[str] = set()  # Avoid duplicates
        self.captured_utmmaps: set[str] = set()  # Unique utmmap IDs found
        self.utmmap_layers: dict[str, str] = {}  # utmmap -> WMS layer name (V_PARCEL47 / V_PARCEL48)
        self._lock = asyncio.Lock()

    async def _check_popup_and_close(self, page) -> bool | None:
        """
        Check popup button to determine if data exists, then close it.

        Returns:
            True = has data (button says "รับทราบ")
            False = no data (button says "ตกลง")
            None = no popup detected
        """
        try:
            # Wait a moment for popup to fully render
            await asyncio.sleep(0.5)

            # Check for "รับทราบ" button (has data)
            btn_acknowledge = await page.query_selector('button:has-text("รับทราบ")')
            if btn_acknowledge and await btn_acknowledge.is_visible():
                await btn_acknowledge.click()
                log("  -> Found 'รับทราบ' button = HAS DATA")
                await asyncio.sleep(1)
                return True

            # Check for "ตกลง" button (no data)
            btn_ok = await page.query_selector('button:has-text("ตกลง")')
            if btn_ok and await btn_ok.is_visible():
                await btn_ok.click()
                log("  -> Found 'ตกลง' button = NO DATA")
                await asyncio.sleep(1)
                return False

            # Fallback: try generic swal2 confirm button
            swal_selectors = [
                'button.swal2-confirm',
                '.swal2-confirm.swal2-styled',
            ]
            for selector in swal_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn and await btn.is_visible():
                        # Get button text to determine type
                        btn_text = await btn.inner_text()
                        await btn.click()
                        log(f"  -> Clicked button with text: '{btn_text}'")
                        await asyncio.sleep(1)
                        # Check if it's the acknowledge button
                        if 'รับทราบ' in btn_text:
                            return True
                        elif 'ตกลง' in btn_text:
                            return False
                        else:
                            return None  # Unknown button
                except:
                    continue

            return None  # No popup found

        except Exception as e:
            log(f"Error checking popup: {e}")
            return None

    async def _try_double_click_with_offsets(self, page, canvas_box: dict, points=None) -> tuple[bool, bool, int]:
        """
        Double-click canvas points to find a parcel and activate the layer.

        ``points`` is a list of (x, y) canvas coords (see
        tile_quality.spread_click_points); if None, uses the default 5-point
        spread. Each click counts against DOL's ~20-clicks-per-session budget.

        Returns (found_data, saw_no_data, clicks_used):
          found_data  - True if a click returned the "รับทราบ" (has data) popup.
          saw_no_data - True if a click returned the explicit "ตกลง" (no data) popup.
          clicks_used - number of double-clicks actually performed.
        """
        if points is None:
            points = spread_click_points(canvas_box, 5)

        saw_no_data = False
        clicks_used = 0
        for i, (x, y) in enumerate(points):
            log(f"  Click {i + 1}/{len(points)} at ({x:.0f}, {y:.0f})...")
            await page.mouse.dblclick(x, y)
            clicks_used += 1
            await asyncio.sleep(3)  # Wait for popup

            has_data = await self._check_popup_and_close(page)
            if has_data is True:
                log("  >>> FOUND DATA!")
                return (True, saw_no_data, clicks_used)
            elif has_data is False:
                saw_no_data = True
                log("  >>> No data here, trying next point...")
            else:
                log("  >>> No popup here, trying next point...")

        return (False, saw_no_data, clicks_used)

    def _calculate_grid_steps(self, bbox: list[float]) -> tuple[int, int]:
        """
        Calculate number of grid steps for X and Y based on bbox dimensions.

        Returns:
            Tuple of (steps_x, steps_y) - columns and rows
        """
        min_lon, min_lat, max_lon, max_lat = bbox
        lat_range = max_lat - min_lat
        lon_range = max_lon - min_lon

        # Approximate km per degree at Thailand's latitude
        km_per_deg_lat = 111
        km_per_deg_lon = 110

        width_km = lon_range * km_per_deg_lon
        height_km = lat_range * km_per_deg_lat

        # Target ~2-3 km per grid cell, min 2, max 7 steps per dimension
        target_cell_size = 2.5  # km per cell

        steps_x = max(2, min(7, int(math.ceil(width_km / target_cell_size))))
        steps_y = max(2, min(7, int(math.ceil(height_km / target_cell_size))))

        log(f"Boundary size: {width_km:.1f}km x {height_km:.1f}km -> Grid: {steps_x}x{steps_y}")

        return (steps_x, steps_y)

    async def fetch_tiles(
        self,
        bbox: list[float],
        session_name: str,
        zoom_level: int = 17,
        output_dir: str = "output",
        timeout_seconds: int = 120,
        location_info: dict = None
    ) -> dict:
        """
        Fetch tiles, retrying in fresh browser sessions at shifted locations if a
        session finds no parcel. DOL blocks after ~20 parcel-query clicks per
        session, so a sparse area can exhaust the click budget without activating
        the parcel layer; a fresh session then probes different spots.

        Returns dict with tile_count and output_path.
        """
        self.tiles = []
        self.captured_urls = set()
        self.captured_utmmaps = set()
        self.utmmap_layers = {}

        output_path = Path(output_dir) / session_name
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "images").mkdir(exist_ok=True)

        MAX_SESSIONS = 3
        for attempt in range(MAX_SESSIONS):
            if attempt > 0:
                log(f"\n{'=' * 55}\nSESSION RETRY {attempt + 1}/{MAX_SESSIONS}: "
                    f"no parcel found — reopening at a different location\n{'=' * 55}")
            status = await self._scan_session(
                bbox, session_name, zoom_level, output_dir, location_info, attempt)
            if status == "found" or self.tiles:
                break
            if status == "blocked":
                # Browser closed / DOL IP throttle — retrying would only hammer
                # DOL harder and not help. Back off.
                log("Session blocked (likely DOL IP throttle / load) — backing off, not retrying.")
                break
            # status == "no_parcel": genuinely searched, no hit → retry elsewhere

        # Camera fallback for any blank tiles (opens its own fresh sessions).
        if self.tiles:
            await self._retry_empty_tiles(output_path, session_name, bbox, location_info, zoom_level)
        return {"tile_count": len(self.tiles), "output_path": str(output_path)}

    async def _scan_session(
        self,
        bbox: list[float],
        session_name: str,
        zoom_level: int = 17,
        output_dir: str = "output",
        location_info: dict = None,
        attempt: int = 0,
    ) -> bool:
        """Run ONE browser session: fly the grid, click to activate the parcel
        layer (within the per-session click budget), capture tiles, fetch WFS, and
        re-fetch blanks. Returns a status: 'found' (layer activated), 'no_parcel'
        (searched but no hit — retry elsewhere), or 'blocked' (browser closed /
        DOL throttle — back off)."""
        found_data = False
        blocked = False
        output_path = Path(output_dir) / session_name
        output_path.mkdir(parents=True, exist_ok=True)
        images_dir = output_path / "images"
        images_dir.mkdir(exist_ok=True)

        min_lon, min_lat, max_lon, max_lat = bbox

        # Add padding to bbox (10%) to ensure we capture edges of irregular boundaries
        lat_range_raw = max_lat - min_lat
        lon_range_raw = max_lon - min_lon
        padding_lat = lat_range_raw * 0.10
        padding_lon = lon_range_raw * 0.10

        min_lon_padded = min_lon - padding_lon
        max_lon_padded = max_lon + padding_lon
        min_lat_padded = min_lat - padding_lat
        max_lat_padded = max_lat + padding_lat

        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2

        # Use fresh incognito browser each time (avoids rate limit)
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='th-TH'
        )
        log("Using fresh incognito browser (no cookies = no rate limit)")

        page = await context.new_page()

        # Set up tile interception
        async def capture_tile(response):
            url = response.url

            # Look for WMS GetMap requests - capture any image tile from geoserver
            if 'geoserver' in url.lower() and ('GetMap' in url or 'wms' in url.lower()):
                try:
                    if response.status == 200:
                        content_type = response.headers.get('content-type', '')
                        if 'image' not in content_type:
                            return

                        body = await response.body()
                        if len(body) < 500:  # Too small, likely empty
                            return

                        # Parse URL parameters
                        parsed = urlparse(url)
                        params = parse_qs(parsed.query)

                        # Get BBOX
                        bbox_str = params.get('BBOX', params.get('bbox', ['']))[0]
                        if not bbox_str:
                            return

                        bbox_parts = [float(x) for x in bbox_str.split(',')]

                        # Drop Cesium overview tiles requested mid-flight: the
                        # parcel layer is below its min render scale when zoomed
                        # out, so these are always transparent junk that no retry
                        # can fill (see tile_quality.is_overview_tile).
                        if is_overview_tile(bbox_parts):
                            return

                        # Create unique key for deduplication
                        url_key = f"{bbox_str}_{params.get('LAYERS', params.get('layers', ['']))[0]}"
                        if url_key in self.captured_urls:
                            return

                        # Check if this is a parcel layer tile (has utmmap or parcel-related layer)
                        viewparams = params.get('viewparams', params.get('VIEWPARAMS', ['']))[0]
                        layers = params.get('LAYERS', params.get('layers', ['']))[0]

                        # Extract utmmap if present
                        utmmap = ''
                        if 'utmmap:' in viewparams:
                            match = re.search(r'utmmap:(\d+)', viewparams)
                            if match:
                                utmmap = match.group(1)

                        # Get SRS/CRS
                        srs = params.get('SRS', params.get('srs', params.get('CRS', params.get('crs', ['EPSG:3857']))))[0]

                        tile_data = {
                            'url': url,
                            'bbox': bbox_parts,
                            'srs': srs,
                            'width': int(params.get('WIDTH', params.get('width', ['256']))[0]),
                            'height': int(params.get('HEIGHT', params.get('height', ['256']))[0]),
                            'timestamp': datetime.now().isoformat(),
                            'size': len(body),
                            'utmmap': utmmap,
                            'layers': layers,
                            'imageData': base64.b64encode(body).decode('utf-8')
                        }

                        self.captured_urls.add(url_key)

                        # Save image immediately
                        tile_idx = len(self.tiles)
                        img_path = images_dir / f"tile_{tile_idx}.png"
                        with open(img_path, 'wb') as f:
                            f.write(body)

                        # Save tile metadata immediately (for recovery)
                        meta_path = images_dir / f"tile_{tile_idx}.json"
                        tile_meta = {
                            'fileName': f"images/tile_{tile_idx}.png",
                            'bbox': bbox_parts,
                            'srs': srs,
                            'width': tile_data['width'],
                            'height': tile_data['height'],
                            'timestamp': tile_data['timestamp'],
                            'utmmap': utmmap,
                            'layers': layers
                        }
                        with open(meta_path, 'w', encoding='utf-8') as f:
                            json.dump(tile_meta, f)

                        self.tiles.append(tile_data)
                        if utmmap:
                            self.captured_utmmaps.add(utmmap)
                            # Record which WMS layer this utmmap belongs to (zone 47 vs 48)
                            if utmmap not in self.utmmap_layers and layers:
                                self.utmmap_layers[utmmap] = layers

                        layer_short = layers[:25] if layers else "unknown"
                        utmmap_info = f", utmmap={utmmap}" if utmmap else ""
                        log(f"Tile {len(self.tiles)}: {len(body)}b, {layer_short}{utmmap_info}")
                except Exception as e:
                    pass  # Silent fail for non-critical errors

        page.on('response', capture_tile)

        try:
            log("Opening DOL website...")
            log("If you see a CAPTCHA, please solve it.")
            await page.goto(self.DOL_URL, timeout=120000)

            # Wait for page to load and potential captcha
            log("Waiting for map to initialize...")
            await asyncio.sleep(10)

            # Check if captcha is present
            content = await page.content()
            if 'hcaptcha' in content.lower() or 'captcha' in content.lower():
                log("\n*** CAPTCHA detected - please solve it in the browser ***\n")
                # Wait for captcha to be solved
                for i in range(60):  # Wait up to 60 seconds
                    await asyncio.sleep(2)
                    content = await page.content()
                    if 'cesium' in content.lower() or 'viewer' in content.lower():
                        log("CAPTCHA solved, continuing...")
                        break
                else:
                    log("Timeout waiting for CAPTCHA")

            await asyncio.sleep(3)

            # DOL sits behind an Incapsula challenge (~15-25s, variable) before
            # Cesium initialises. Fixed sleeps race it and intermittently miss the
            # canvas ("No canvas found" -> 0 tiles). Wait on the actual condition.
            log("Waiting for Cesium viewer to be ready...")
            try:
                await page.wait_for_selector('canvas', timeout=60000)
                await page.wait_for_function(
                    "() => typeof viewer !== 'undefined' && viewer && viewer.camera",
                    timeout=60000,
                )
                log("Cesium viewer ready.")
            except Exception:
                log("WARNING: viewer not confirmed ready within 60s; continuing anyway.")

            # Navigate to target location using Cesium
            log(f"Navigating to center: {center_lat}, {center_lon}")

            # Try Cesium navigation
            nav_result = await page.evaluate(f"""
                () => {{
                    // Try Cesium viewer
                    if (typeof viewer !== 'undefined' && viewer.camera) {{
                        viewer.camera.flyTo({{
                            destination: Cesium.Cartesian3.fromDegrees({center_lon}, {center_lat}, 2000),
                            duration: 2
                        }});
                        return 'cesium';
                    }}
                    // Try Leaflet
                    if (typeof map !== 'undefined' && map.setView) {{
                        map.setView([{center_lat}, {center_lon}], {zoom_level});
                        return 'leaflet';
                    }}
                    return 'none';
                }}
            """)
            log(f"Navigation method: {nav_result}")

            # Wait for navigation to complete
            log("Waiting for navigation...")
            await asyncio.sleep(5)

            # Get canvas for double-clicking
            canvas = await page.query_selector('canvas')
            if not canvas:
                log("ERROR: No canvas found!")
                await self._save_session(output_path, session_name, bbox, location_info)
                return "blocked"  # no canvas = DOL didn't serve the app (throttle/load)

            canvas_box = await canvas.bounding_box()
            if not canvas_box:
                log("ERROR: Could not get canvas bounding box!")
                await self._save_session(output_path, session_name, bbox, location_info)
                return "blocked"  # no canvas = DOL didn't serve the app (throttle/load)

            # Calculate pan distances based on padded bbox (covers irregular boundary edges)
            lat_range = max_lat_padded - min_lat_padded
            lon_range = max_lon_padded - min_lon_padded

            # Calculate dynamic grid size based on padded bbox dimensions
            padded_bbox = [min_lon_padded, min_lat_padded, max_lon_padded, max_lat_padded]
            steps_x, steps_y = self._calculate_grid_steps(padded_bbox)
            found_data = False

            # A point far outside the bbox (0.5 degrees north) used to despawn tiles
            far_away_lat = max_lat_padded + 0.5
            far_away_lon = center_lon

            ALTITUDE = 1500   # metres — single zoom level, consistent tile resolution
            TOTAL_PASSES = 3  # retry passes within this session for uncovered grid cells

            cells_with_tiles: set[tuple[int, int]] = set()
            # Cells where DOL explicitly reported "no parcel here" (ตกลง) and no
            # tiles came in — genuinely empty, so don't waste later passes on them.
            no_data_cells: set[tuple[int, int]] = set()
            total_cells = steps_x * steps_y
            log(f"Grid: {steps_x}x{steps_y} = {total_cells} cells, 10% padding, up to {TOTAL_PASSES} passes")

            # DOL blocks after ~20 parcel-query clicks per session. Spread a safe
            # budget across cells (few clicks each); on a session-retry, shift the
            # probe targets (attempt_offset) so a fresh session tries new spots.
            CLICK_BUDGET = 18
            clicks_used_total = 0
            per_cell = clicks_per_cell(total_cells, CLICK_BUDGET)
            off_dx, off_dy = attempt_offset(attempt)
            budget_exhausted = False
            if attempt > 0:
                log(f"Session attempt {attempt + 1}: probing shifted targets (offset {off_dx:+.2f},{off_dy:+.2f})")

            for scan_pass in range(TOTAL_PASSES):
                cells_to_scan = [
                    (row, col)
                    for row in range(steps_y)
                    for col in range(steps_x)
                    if (row, col) not in cells_with_tiles
                    and (row, col) not in no_data_cells
                ]
                if not cells_to_scan:
                    log(f"All {total_cells} cells covered — done")
                    break

                tiles_at_pass_start = len(self.tiles)
                log(f"\nPass {scan_pass + 1}/{TOTAL_PASSES} — {len(cells_to_scan)} cells")

                for row, col in cells_to_scan:
                    target_lat = min_lat_padded + (lat_range * (row + 0.5 + off_dy) / steps_y)
                    target_lon = min_lon_padded + (lon_range * (col + 0.5 + off_dx) / steps_x)

                    log(f"\n  Pass {scan_pass+1} | Cell ({col+1},{row+1}): {target_lat:.4f}, {target_lon:.4f}")
                    tiles_before_cell = len(self.tiles)

                    # Despawn on retry passes so browser requests fresh tiles
                    if scan_pass > 0:
                        await page.evaluate(f"""
                            () => {{
                                if (typeof viewer !== 'undefined' && viewer.camera) {{
                                    viewer.camera.flyTo({{
                                        destination: Cesium.Cartesian3.fromDegrees({far_away_lon}, {far_away_lat}, 1500),
                                        duration: 1
                                    }});
                                }} else if (typeof map !== 'undefined' && map.setView) {{
                                    map.setView([{far_away_lat}, {far_away_lon}], {zoom_level});
                                }}
                            }}
                        """)
                        await asyncio.sleep(2)

                    await page.evaluate(f"""
                        () => {{
                            if (typeof viewer !== 'undefined' && viewer.camera) {{
                                viewer.camera.flyTo({{
                                    destination: Cesium.Cartesian3.fromDegrees({target_lon}, {target_lat}, {ALTITUDE}),
                                    duration: 1
                                }});
                            }} else if (typeof map !== 'undefined' && map.setView) {{
                                map.setView([{target_lat}, {target_lon}], {zoom_level});
                            }}
                        }}
                    """)
                    await asyncio.sleep(3)

                    saw_no_data = False
                    if not found_data:
                        if clicks_used_total >= CLICK_BUDGET:
                            log(f"Click budget ({CLICK_BUDGET}) spent — no parcel found; ending session for retry.")
                            budget_exhausted = True
                            break
                        n = min(per_cell, CLICK_BUDGET - clicks_used_total)
                        points = spread_click_points(canvas_box, n)
                        found, saw_no_data, used = await self._try_double_click_with_offsets(page, canvas_box, points)
                        clicks_used_total += used
                        log(f"  Clicks used this session: {clicks_used_total}/{CLICK_BUDGET}")
                        if found:
                            found_data = True
                            log("Parcel layer activated! Continuing scan...")

                    await asyncio.sleep(3)

                    new_count = len(self.tiles) - tiles_before_cell
                    if new_count > 0:
                        cells_with_tiles.add((row, col))
                    elif saw_no_data:
                        # DOL confirmed no parcel here and nothing loaded — skip re-scan.
                        no_data_cells.add((row, col))
                        log(f"  Cell ({col+1},{row+1}): DOL reports no parcel data — won't re-scan")
                    log(f"  Cell ({col+1},{row+1}): +{new_count} tiles | total: {len(self.tiles)} | covered: {len(cells_with_tiles)}/{total_cells}")

                    if len(self.tiles) >= self.MAX_TILES_PER_SESSION:
                        log(f"Reached max tiles ({self.MAX_TILES_PER_SESSION})")
                        break

                if budget_exhausted and not found_data:
                    break  # click budget spent without activation — let the session retry

                new_tiles = len(self.tiles) - tiles_at_pass_start
                log(f"\nPass {scan_pass + 1} done: +{new_tiles} tiles, {len(cells_with_tiles)}/{total_cells} cells covered")

                if len(self.tiles) >= self.MAX_TILES_PER_SESSION:
                    break
                if scan_pass > 0 and new_tiles == 0:
                    log("No new tiles this pass — stopping")
                    break

            if not found_data:
                log("\nWARNING: No parcel data found in any location!")
                log("The DOL system may not have coverage for this region.")
            else:
                # Fetch WFS vector data for each unique utmmap found
                if self.captured_utmmaps:
                    features_dir = output_path / "features"
                    features_dir.mkdir(exist_ok=True)
                    log(f"\nFetching WFS vector data for {len(self.captured_utmmaps)} utmmap(s)...")
                    await self._fetch_wfs_features(page, self.captured_utmmaps, features_dir, self.utmmap_layers, bbox)

            # Stop intercepting first: the live Cesium map keeps requesting tiles
            # and the re-fetch responses below would otherwise be re-captured as
            # brand-new tiles, growing self.tiles without end.
            page.remove_listener('response', capture_tile)

            # Ground truth for "is there a parcel line here?" from the WFS data,
            # so we never retry blank tiles over genuinely empty land (river/road
            # = 'no line'); those would only ever come back blank.
            features_dir = output_path / "features"
            parcel_boxes = (load_parcel_bboxes(sorted(features_dir.glob("*.geojson")))
                            if features_dir.exists() else [])
            has_data = (lambda b: bbox_overlaps_any(b, parcel_boxes)) if parcel_boxes else None

            # Primary blank recovery: re-GET each blank tile's exact URL while the
            # session is still live. A blank tile over a parcel is almost always a
            # transient empty GeoServer render (proven: same bbox returns parcels
            # on retry), so a direct re-fetch recovers it far more reliably than
            # re-flying the camera. The camera re-scan below is a fallback.
            blank_all = [i for i in range(len(self.tiles))
                         if is_blank_tile(images_dir / f"tile_{i}.png")]
            blank_idx = [i for i in blank_all
                         if has_data is None or not self.tiles[i].get('bbox')
                         or has_data(self.tiles[i]['bbox'])]
            no_data_blanks = len(blank_all) - len(blank_idx)
            if blank_idx:
                log(f"\nDirect re-fetch: {len(blank_idx)} recoverable blank tile(s) via "
                    f"stored URLs ({no_data_blanks} no-parcel blanks left as-is)...")
                fetch_url = self._make_url_fetcher(page)
                filled = await refetch_blank_tiles(
                    self.tiles, images_dir, fetch_url, has_data=has_data)
                log(f"Direct re-fetch filled {filled}/{len(blank_idx)} blank tile(s)")
            elif no_data_blanks:
                log(f"\n{no_data_blanks} blank tile(s) are over no-parcel land — no retry needed.")

        except Exception as e:
            if 'closed' in str(e).lower() or 'crash' in str(e).lower():
                blocked = True
                log(f"Session blocked/closed mid-scan (likely DOL throttle): {e}")
            else:
                log(f"Error: {e}")
        finally:
            # Save mission.json
            await self._save_session(output_path, session_name, bbox, location_info)
            # Close browser completely (fresh start next time)
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await playwright.stop()
            except Exception:
                pass

        # Status drives the wrapper: 'found' = layer activated; 'no_parcel' =
        # searched but no hit (retry elsewhere); 'blocked' = browser closed / DOL
        # throttle (back off).
        if found_data:
            return "found"
        return "blocked" if blocked else "no_parcel"

    def _make_url_fetcher(self, page):
        """Return an async fetch_url(url) -> bytes|None that GETs a WMS tile from
        inside the live page, reusing the DOL session cookies (same technique as
        the WFS fetch). Used by refetch_blank_tiles to recover blank tiles."""
        async def fetch_url(url: str):
            try:
                b64 = await page.evaluate(
                    """async (u) => {
                        const r = await fetch(u, { credentials: 'include' });
                        if (!r.ok) return null;
                        const buf = new Uint8Array(await r.arrayBuffer());
                        let bin = '';
                        for (let i = 0; i < buf.length; i++) bin += String.fromCharCode(buf[i]);
                        return btoa(bin);
                    }""",
                    url,
                )
                return base64.b64decode(b64) if b64 else None
            except Exception:
                return None
        return fetch_url

    async def _is_tile_empty(self, png_path: Path) -> bool:
        """Return True if the tile PNG carries essentially no visible content
        (fully transparent OR near-empty). Shared detector — see tile_quality."""
        return is_blank_tile(png_path)

    async def _retry_empty_tiles(
        self,
        output_path: Path,
        session_name: str,
        bbox: list[float],
        location_info: dict,
        zoom_level: int = 17,
    ) -> int:
        """
        After the main scan, open fresh browser sessions to re-fetch transparent tiles.
        Repeats until no empty tiles remain or 3 rounds with no improvement.
        """
        images_dir = output_path / "images"
        features_dir = output_path / "features"
        total_replaced = 0
        MAX_ROUNDS = 3

        # Only chase blank tiles that actually have a parcel under them; blanks
        # over no-parcel land ('no line') are genuinely empty — don't re-scan.
        parcel_boxes = (load_parcel_bboxes(sorted(features_dir.glob("*.geojson")))
                        if features_dir.exists() else [])

        def _has_parcel(t):
            b = t.get('bbox')
            return (not parcel_boxes) or (not b) or bbox_overlaps_any(b, parcel_boxes)

        for round_num in range(1, MAX_ROUNDS + 1):
            empty = [
                (i, t) for i, t in enumerate(self.tiles)
                if _has_parcel(t) and await self._is_tile_empty(images_dir / f"tile_{i}.png")
            ]
            if not empty:
                if round_num == 1:
                    log("\nNo empty tiles — no retry needed.")
                else:
                    log(f"\nAll empty tiles filled after {round_num - 1} retry round(s).")
                break

            log(f"\n{'='*55}")
            log(f"EMPTY TILE RETRY {round_num}/{MAX_ROUNDS}: {len(empty)} transparent tiles")
            log(f"{'='*55}")

            replaced = await self._run_retry_session(empty, images_dir, bbox, zoom_level)
            total_replaced += replaced
            log(f"Retry round {round_num}: replaced {replaced}/{len(empty)} tiles")

            if replaced == 0:
                log("No improvement — remaining empty tiles likely have no parcel data here.")
                break

        if total_replaced > 0:
            await self._save_session(output_path, session_name, bbox, location_info)

        return total_replaced

    async def _run_retry_session(
        self,
        empty_tiles: list[tuple[int, dict]],
        images_dir: Path,
        bbox: list[float],
        zoom_level: int = 17,
    ) -> int:
        """
        Open a fresh browser and run the same systematic grid scan as the main fetch.
        Only overwrites tiles that are currently empty (fully transparent).
        Returns count of tiles replaced.
        """
        # Register empty tile bboxes as pending replacements
        pending: dict[str, int] = {}   # url_key -> tile_idx
        for tile_idx, tile in empty_tiles:
            bbox_str = ','.join(str(v) for v in tile['bbox'])
            layers = tile.get('layers', '')
            url_key = f"{bbox_str}_{layers}"
            self.captured_urls.discard(url_key)  # allow re-interception
            pending[url_key] = tile_idx

        replaced_count = [0]

        # Recompute padded bbox + grid — identical to main scan
        min_lon, min_lat, max_lon, max_lat = bbox
        lat_range_raw = max_lat - min_lat
        lon_range_raw = max_lon - min_lon
        min_lon_padded = min_lon - lon_range_raw * 0.10
        max_lon_padded = max_lon + lon_range_raw * 0.10
        min_lat_padded = min_lat - lat_range_raw * 0.10
        max_lat_padded = max_lat + lat_range_raw * 0.10
        center_lon = (min_lon + max_lon) / 2

        padded_bbox = [min_lon_padded, min_lat_padded, max_lon_padded, max_lat_padded]
        steps_x, steps_y = self._calculate_grid_steps(padded_bbox)
        lat_range = max_lat_padded - min_lat_padded
        lon_range = max_lon_padded - min_lon_padded
        far_away_lat = max_lat_padded + 0.5
        ALTITUDE = 1500
        TOTAL_PASSES = 3

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='th-TH'
        )
        log("Retry: fresh incognito browser — running same grid scan")
        page = await context.new_page()

        async def capture_retry(response):
            url = response.url
            if 'geoserver' not in url.lower():
                return
            if 'GetMap' not in url and 'wms' not in url.lower():
                return
            if response.status != 200:
                return
            try:
                if 'image' not in response.headers.get('content-type', ''):
                    return
                parsed = urlparse(url)
                params = parse_qs(parsed.query)
                bbox_str = params.get('BBOX', params.get('bbox', ['']))[0]
                if not bbox_str:
                    return
                layers = params.get('LAYERS', params.get('layers', ['']))[0]
                url_key = f"{bbox_str}_{layers}"
                if url_key not in pending:
                    return

                body = await response.body()
                if len(body) < 500:
                    return

                # Only overwrite if the new tile actually has content
                if is_blank_tile(body):
                    return  # Still blank — don't overwrite

                tile_idx = pending.pop(url_key)
                self.captured_urls.add(url_key)
                png_path = images_dir / f"tile_{tile_idx}.png"
                with open(png_path, 'wb') as f:
                    f.write(body)
                self.tiles[tile_idx]['size'] = len(body)
                replaced_count[0] += 1
                log(f"  Replaced tile_{tile_idx}.png: {len(body)}b")
            except Exception:
                pass

        page.on('response', capture_retry)

        try:
            log("Retry: opening DOL website...")
            await page.goto(self.DOL_URL, timeout=120000)
            await asyncio.sleep(10)
            try:
                await page.wait_for_selector('canvas', timeout=60000)
                await page.wait_for_function(
                    "() => typeof viewer !== 'undefined' && viewer && viewer.camera",
                    timeout=60000,
                )
            except Exception:
                pass

            # Navigate to area center first
            await page.evaluate(f"""
                () => {{
                    if (typeof viewer !== 'undefined' && viewer.camera) {{
                        viewer.camera.flyTo({{
                            destination: Cesium.Cartesian3.fromDegrees({center_lon}, {(min_lat + max_lat) / 2}, 2000),
                            duration: 2
                        }});
                    }}
                }}
            """)
            await asyncio.sleep(5)

            canvas = await page.query_selector('canvas')
            canvas_box = await canvas.bounding_box() if canvas else None
            found_data = False
            cells_with_tiles: set[tuple[int, int]] = set()

            # Same grid scan as main fetch — identical cell order, timing, despawn logic
            for scan_pass in range(TOTAL_PASSES):
                if not pending:
                    log("  All empty tiles filled — stopping retry scan")
                    break

                cells_to_scan = [
                    (row, col)
                    for row in range(steps_y)
                    for col in range(steps_x)
                    if (row, col) not in cells_with_tiles
                ]
                if not cells_to_scan:
                    break

                replaced_at_pass_start = replaced_count[0]
                log(f"\n  Retry pass {scan_pass + 1}/{TOTAL_PASSES} — {len(cells_to_scan)} cells, {len(pending)} tiles still pending")

                for row, col in cells_to_scan:
                    if not pending:
                        break
                    target_lat = min_lat_padded + (lat_range * (row + 0.5) / steps_y)
                    target_lon = min_lon_padded + (lon_range * (col + 0.5) / steps_x)

                    if scan_pass > 0:
                        await page.evaluate(f"""
                            () => {{
                                if (typeof viewer !== 'undefined' && viewer.camera) {{
                                    viewer.camera.flyTo({{
                                        destination: Cesium.Cartesian3.fromDegrees({center_lon}, {far_away_lat}, 1500),
                                        duration: 1
                                    }});
                                }}
                            }}
                        """)
                        await asyncio.sleep(2)

                    await page.evaluate(f"""
                        () => {{
                            if (typeof viewer !== 'undefined' && viewer.camera) {{
                                viewer.camera.flyTo({{
                                    destination: Cesium.Cartesian3.fromDegrees({target_lon}, {target_lat}, {ALTITUDE}),
                                    duration: 1
                                }});
                            }}
                        }}
                    """)
                    await asyncio.sleep(3)

                    if not found_data and canvas_box:
                        found, _, _ = await self._try_double_click_with_offsets(page, canvas_box)
                        if found:
                            found_data = True

                    await asyncio.sleep(3)
                    cells_with_tiles.add((row, col))

                new_replaced = replaced_count[0] - replaced_at_pass_start
                log(f"  Retry pass {scan_pass + 1} done: +{new_replaced} replaced, {len(pending)} still pending")
                if scan_pass > 0 and new_replaced == 0:
                    log("  No improvement this pass — stopping")
                    break

        except Exception as e:
            log(f"Retry session error: {e}")
        finally:
            await browser.close()
            await playwright.stop()

        return replaced_count[0]

    async def _fetch_wfs_features(self, page, utmmaps: set[str], features_dir: Path,
                                 utmmap_layers: dict[str, str] = None, bbox: list[float] = None):
        """
        Fetch parcel vector data from DOL WFS for each utmmap.
        Uses the same layer name that was captured for each utmmap (V_PARCEL47 or V_PARCEL48).

        A utmmap map sheet spans ~28 km, far larger than a subdistrict, and the WFS
        caps at maxFeatures — so without a filter it returns thousands of parcels
        mostly OUTSIDE the area. We add a BBOX filter in the layer's native UTM CRS
        (EPSG:24047/24048; a 4326 BBOX does not restrict) so the WFS returns the
        parcels actually inside `bbox`.
        """
        WFS_BASE = "https://landsmaps.dol.go.th/geoserver/LANDSMAPS/wfs"
        if utmmap_layers is None:
            utmmap_layers = {}

        for utmmap in sorted(utmmaps):
            out_path = features_dir / f"utmmap_{utmmap}.geojson"
            if out_path.exists():
                log(f"  WFS utmmap {utmmap}: already cached, skipping")
                continue

            # Use the layer name from the captured WMS tile (e.g. LANDSMAPS:V_PARCEL48)
            # Fall back to V_PARCEL47 if unknown
            wms_layer = utmmap_layers.get(utmmap, "LANDSMAPS:V_PARCEL47")
            # Convert WMS layer name to WFS typeName (same name in DOL's geoserver)
            type_name = wms_layer  # e.g. "LANDSMAPS:V_PARCEL48"

            # BBOX filter in the layer's native CRS, so we get the parcels inside
            # the requested area rather than the whole (huge) map sheet.
            bbox_param = ''
            native_epsg = native_epsg_for_layer(wms_layer)
            if bbox and native_epsg:
                try:
                    e0, n0, e1, n1 = bbox_to_native(bbox, native_epsg)
                    bbox_param = f"&BBOX={e0},{n0},{e1},{n1},EPSG:{native_epsg}"
                except Exception as e:
                    log(f"  WFS bbox reproject failed ({e}); fetching whole sheet")

            log(f"  WFS utmmap {utmmap} (layer={type_name}){' + bbox' if bbox_param else ''}: fetching...")
            try:
                result = await page.evaluate(f"""
                    async () => {{
                        const url = '{WFS_BASE}?service=WFS&version=1.0.0&request=GetFeature' +
                            '&typeName={type_name}' +
                            '&viewparams=utmmap:{utmmap}' +
                            '&outputFormat=application/json' +
                            '&maxFeatures=50000' +
                            '{bbox_param}';
                        try {{
                            const resp = await fetch(url, {{ credentials: 'include' }});
                            if (!resp.ok) return {{ error: resp.status }};
                            const data = await resp.json();
                            return data;
                        }} catch(e) {{
                            return {{ error: e.toString() }};
                        }}
                    }}
                """)

                if result and 'error' not in result:
                    feature_count = len(result.get('features', []))
                    with open(out_path, 'w', encoding='utf-8') as f:
                        json.dump(result, f, ensure_ascii=False)
                    log(f"  WFS utmmap {utmmap}: {feature_count} features saved")
                else:
                    log(f"  WFS utmmap {utmmap}: failed - {result}")

            except Exception as e:
                log(f"  WFS utmmap {utmmap}: error - {e}")

            await asyncio.sleep(1)  # Be polite to the server

    async def _save_session(self, output_path: Path, session_name: str, bbox: list[float], location_info: dict = None):
        """Save mission data with tile information."""
        tiles_data = []

        # First try to use self.tiles
        if self.tiles:
            for i, tile in enumerate(self.tiles):
                tile_info = {
                    'fileName': f"images/tile_{i}.png",
                    'bbox': tile['bbox'],
                    'srs': tile.get('srs', 'EPSG:3857'),
                    'width': tile['width'],
                    'height': tile['height'],
                    'timestamp': tile['timestamp'],
                    'utmmap': tile.get('utmmap', ''),
                    'layers': tile.get('layers', ''),
                    'url': tile.get('url', '')[:200]
                }
                tiles_data.append(tile_info)
        else:
            # Fallback: reconstruct from individual JSON files
            images_dir = output_path / "images"
            if images_dir.exists():
                json_files = sorted(images_dir.glob("tile_*.json"),
                                   key=lambda x: int(x.stem.split('_')[1]))
                for json_file in json_files:
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            tile_meta = json.load(f)
                            tiles_data.append(tile_meta)
                    except Exception as e:
                        log(f"Error reading {json_file}: {e}")

        mission_data = {
            "sessionName": session_name,
            "bbox": bbox,
            "timestamp": datetime.now().isoformat(),
            "tileCount": len(tiles_data),
            "utmmaps": sorted(self.captured_utmmaps),
            "utmmapLayers": self.utmmap_layers,
            "tiles": tiles_data
        }

        # Add location info if provided (for retrieving actual geometry later)
        if location_info:
            mission_data["location"] = location_info

        mission_path = output_path / "mission.json"
        with open(mission_path, 'w', encoding='utf-8') as f:
            json.dump(mission_data, f, indent=2, ensure_ascii=False)

        log(f"Saved {len(tiles_data)} tiles to {output_path}")
