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

    async def _try_double_click_with_offsets(self, page, canvas_box: dict) -> bool:
        """
        Try double-clicking at different positions within the current view.

        Tries: center, left 25%, right 25%, top 25%, bottom 25%

        Returns True if data found at any position.
        """
        center_x = canvas_box['x'] + canvas_box['width'] / 2
        center_y = canvas_box['y'] + canvas_box['height'] / 2
        offset_x = canvas_box['width'] * 0.25
        offset_y = canvas_box['height'] * 0.25

        # Define positions to try: (name, x, y)
        positions = [
            ("center", center_x, center_y),
            ("left 25%", center_x - offset_x, center_y),
            ("right 25%", center_x + offset_x, center_y),
            ("top 25%", center_x, center_y - offset_y),
            ("bottom 25%", center_x, center_y + offset_y),
        ]

        for name, x, y in positions:
            log(f"  Trying {name} ({x:.0f}, {y:.0f})...")

            await page.mouse.dblclick(x, y)
            await asyncio.sleep(3)  # Wait for popup

            has_data = await self._check_popup_and_close(page)

            if has_data is True:
                log(f"  >>> FOUND DATA at {name}!")
                return True
            elif has_data is False:
                log(f"  >>> No data at {name}, trying next position...")
                continue
            else:
                log(f"  >>> No popup at {name}, trying next position...")
                continue

        return False

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
        Fetch tiles by intercepting WMS requests from the website.

        Args:
            bbox: [min_lon, min_lat, max_lon, max_lat] in EPSG:4326
            session_name: Name for this session
            zoom_level: Map zoom level (15-19)
            output_dir: Directory to save output
            timeout_seconds: Max time to wait for tiles
            location_info: Optional dict with province, district, subdistrict for geometry

        Returns:
            Dict with tile_count and output_path
        """
        self.tiles = []
        self.captured_urls = set()
        self.captured_utmmaps = set()
        self.utmmap_layers = {}

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
                return {"tile_count": 0, "output_path": str(output_path)}

            canvas_box = await canvas.bounding_box()
            if not canvas_box:
                log("ERROR: Could not get canvas bounding box!")
                await self._save_session(output_path, session_name, bbox, location_info)
                return {"tile_count": 0, "output_path": str(output_path)}

            # Calculate pan distances based on padded bbox (covers irregular boundary edges)
            lat_range = max_lat_padded - min_lat_padded
            lon_range = max_lon_padded - min_lon_padded

            # Calculate dynamic grid size based on padded bbox dimensions
            padded_bbox = [min_lon_padded, min_lat_padded, max_lon_padded, max_lat_padded]
            steps_x, steps_y = self._calculate_grid_steps(padded_bbox)
            found_data = False

            # A point far outside the bbox (0.5 degrees north) used to despawn tiles on retries
            far_away_lat = max_lat_padded + 0.5
            far_away_lon = center_lon

            TOTAL_PASSES = 3
            cells_with_tiles: set[tuple[int, int]] = set()

            log(f"Searching for parcel data in {steps_x}x{steps_y} grid ({steps_x * steps_y} positions) with 10% padding, up to {TOTAL_PASSES} passes...")

            for scan_pass in range(TOTAL_PASSES):
                tiles_at_pass_start = len(self.tiles)

                cells_to_scan = [
                    (row, col)
                    for row in range(steps_y)
                    for col in range(steps_x)
                    if (row, col) not in cells_with_tiles
                ]

                if not cells_to_scan:
                    log(f"\nAll cells covered — stopping after pass {scan_pass}")
                    break

                log(f"\n{'='*50}")
                log(f"PASS {scan_pass + 1}/{TOTAL_PASSES} — {len(cells_to_scan)} cells to scan")
                log(f"{'='*50}")

                for row, col in cells_to_scan:
                    target_lat = min_lat_padded + (lat_range * (row + 0.5) / steps_y)
                    target_lon = min_lon_padded + (lon_range * (col + 0.5) / steps_x)

                    log(f"\n=== Pass {scan_pass + 1} | Cell ({col+1},{row+1}) of ({steps_x},{steps_y}): {target_lat:.4f}, {target_lon:.4f} ===")

                    tiles_before_cell = len(self.tiles)

                    # On retry passes: fly far away first (same altitude = parcel layer stays active)
                    # to fully despawn tiles, then fly back for a clean reload
                    if scan_pass > 0:
                        log(f"  Flying far away to despawn tiles...")
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

                    # Navigate to target cell
                    await page.evaluate(f"""
                        () => {{
                            if (typeof viewer !== 'undefined' && viewer.camera) {{
                                viewer.camera.flyTo({{
                                    destination: Cesium.Cartesian3.fromDegrees({target_lon}, {target_lat}, 1500),
                                    duration: 1
                                }});
                            }} else if (typeof map !== 'undefined' && map.setView) {{
                                map.setView([{target_lat}, {target_lon}], {zoom_level});
                            }}
                        }}
                    """)
                    await asyncio.sleep(3)

                    if not found_data:
                        if await self._try_double_click_with_offsets(page, canvas_box):
                            found_data = True
                            log("Parcel layer activated! Continuing to pan and capture tiles...")

                    # Wait for tiles to load
                    await asyncio.sleep(3)

                    if len(self.tiles) > tiles_before_cell:
                        cells_with_tiles.add((row, col))
                        log(f"  Cell ({col+1},{row+1}): {len(self.tiles) - tiles_before_cell} new tiles")
                    else:
                        log(f"  Cell ({col+1},{row+1}): 0 tiles — will retry next pass")

                    log(f"  Total tiles: {len(self.tiles)}")

                    if len(self.tiles) >= self.MAX_TILES_PER_SESSION:
                        log(f"Reached max tiles ({self.MAX_TILES_PER_SESSION})")
                        break

                new_tiles = len(self.tiles) - tiles_at_pass_start
                log(f"\nPass {scan_pass + 1} done: {new_tiles} new tiles, {len(cells_with_tiles)}/{steps_x * steps_y} cells covered")

                if len(self.tiles) >= self.MAX_TILES_PER_SESSION:
                    break

                if scan_pass > 0 and new_tiles == 0:
                    log("No new tiles in this pass — stopping early")
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
                    await self._fetch_wfs_features(page, self.captured_utmmaps, features_dir, self.utmmap_layers)

        except Exception as e:
            log(f"Error: {e}")
        finally:
            # Save mission.json
            await self._save_session(output_path, session_name, bbox, location_info)
            # Close browser completely (fresh start next time)
            await browser.close()
            await playwright.stop()

        return {
            "tile_count": len(self.tiles),
            "output_path": str(output_path)
        }

    async def _fetch_wfs_features(self, page, utmmaps: set[str], features_dir: Path, utmmap_layers: dict[str, str] = None):
        """
        Fetch parcel vector data from DOL WFS for each utmmap.
        Uses the same layer name that was captured for each utmmap (V_PARCEL47 or V_PARCEL48).
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

            log(f"  WFS utmmap {utmmap} (layer={type_name}): fetching...")
            try:
                result = await page.evaluate(f"""
                    async () => {{
                        const url = '{WFS_BASE}?service=WFS&version=1.0.0&request=GetFeature' +
                            '&typeName={type_name}' +
                            '&viewparams=utmmap:{utmmap}' +
                            '&outputFormat=application/json' +
                            '&maxFeatures=5000';
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
