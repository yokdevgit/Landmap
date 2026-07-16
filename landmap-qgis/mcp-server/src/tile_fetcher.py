"""
Parcel fetcher — click-free DOL WFS.

Fetches Thai DOL land parcels WITHOUT the (throttled) canvas double-click:
1. Open landsmaps.dol.go.th just far enough to carry the Incapsula cookie.
2. Discover the 1:4000 map sheet(s) over the bbox from the WFS grid layer
   (V_INDEX4000_<zone>_LANDNO) and derive each utmmap id from the sheet label.
3. Fetch the parcels for those sheets from the WFS (V_PARCEL47/48), BBOX-filtered
   in the native UTM CRS, several sheets in parallel.

Everything rides the un-throttled WFS endpoint — no 3D viewer, no tile capture.
Vector GeoJSON is the output (see gis_processor.process_to_shapefiles).
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from .wfs_helpers import (
    native_epsg_for_layer,
    zone_for_longitude,
    wfs_getfeature_url,
    wfs_index_url,
    label_to_utmmap,
)


def log(msg: str):
    """Log to stderr so we don't break MCP JSON-RPC on stdout."""
    print(msg, file=sys.stderr)


# How many WFS map-sheet fetches to run at once. A dense subdistrict can span
# 10+ sheets (~20 MB each); sequential fetches blew the client timeout, so we
# parallelize with a small pool (kept modest to avoid tripping DOL/Incapsula).
WFS_CONCURRENCY = 4


class TileFetcher:
    """Fetch DOL land parcels click-free via the WFS grid + parcel layers."""

    DOL_URL = "https://landsmaps.dol.go.th"

    def __init__(self):
        self.captured_utmmaps: set[str] = set()   # 1:4000 map-sheet ids found
        self.utmmap_layers: dict[str, str] = {}    # utmmap -> V_PARCEL47 / V_PARCEL48

    async def _discover_utmmaps_via_index(self, page, bbox, attempts=4):
        """Discover the map sheet(s) covering ``bbox`` by querying the 1:4000 grid
        layer (V_INDEX4000_<zone>_LANDNO) over WFS — NO double-click, so immune to
        the DOL parcel-query throttle. Populates self.captured_utmmaps + layers.

        Retries a few times: right after page load the Incapsula cookie may not be
        set yet, so the first fetch can return the challenge HTML instead of JSON.
        Returns "found" / "empty" (valid JSON, no sheets here) / "blocked" (never
        got JSON — likely Incapsula/throttle) so the caller can report clearly."""
        zone = zone_for_longitude((bbox[0] + bbox[2]) / 2)
        parcel_layer = f"LANDSMAPS:V_PARCEL{zone}"
        url = wfs_index_url(f"{self.DOL_URL}/geoserver/LANDSMAPS/wfs", zone, bbox)
        got_json = False
        for i in range(attempts):
            txt = await page.evaluate(
                """async (u) => {
                    try { const r = await fetch(u, { credentials: 'include' }); return await r.text(); }
                    catch (e) { return null; }
                }""", url)
            try:
                data = json.loads(txt)
                got_json = True
            except Exception:
                data = None
            if data is not None:
                for feat in data.get('features', []):
                    label = (feat.get('properties') or {}).get('indlabel1')
                    utm = label_to_utmmap(label)
                    if utm:
                        self.captured_utmmaps.add(utm)
                        self.utmmap_layers.setdefault(utm, parcel_layer)
                if self.captured_utmmaps:
                    return "found"
            if i < attempts - 1:
                await asyncio.sleep(4)  # let the Incapsula cookie settle, then retry
        return "empty" if got_json else "blocked"

    async def _fetch_wfs_features(self, page, utmmaps: set[str], features_dir: Path,
                                 utmmap_layers: dict[str, str] = None, bbox: list[float] = None):
        """Fetch parcel vector data (GeoJSON) from DOL WFS for each utmmap.

        A map sheet is far larger than a subdistrict, so we BBOX-filter in the
        layer's native UTM CRS (EPSG:24047/24048; a 4326 BBOX does not restrict).
        Sheets are fetched CONCURRENTLY (bounded) — a subdistrict can span 10+
        sheets (~20 MB each) and sequential fetches blew the client timeout.
        context.request shares the page's Incapsula cookie and gives real
        parallelism; an in-page fetch is the per-sheet fallback."""
        WFS_BASE = "https://landsmaps.dol.go.th/geoserver/LANDSMAPS/wfs"
        if utmmap_layers is None:
            utmmap_layers = {}

        # Build the work list (skip already-cached sheets).
        work = []
        for utmmap in sorted(utmmaps):
            out_path = features_dir / f"utmmap_{utmmap}.geojson"
            if out_path.exists():
                log(f"  WFS utmmap {utmmap}: already cached, skipping")
                continue
            wms_layer = utmmap_layers.get(utmmap, "LANDSMAPS:V_PARCEL47")
            type_name = wms_layer  # DOL geoserver uses the same name for WFS
            native_epsg = native_epsg_for_layer(wms_layer)
            url = None
            if bbox and native_epsg:
                try:
                    url = wfs_getfeature_url(WFS_BASE, type_name, utmmap, bbox, native_epsg)
                except Exception as e:
                    log(f"  WFS bbox reproject failed ({e}); fetching whole sheet")
            if url is None:  # no bbox -> whole map sheet (fallback)
                url = (f"{WFS_BASE}?service=WFS&version=1.0.0&request=GetFeature"
                       f"&typeName={type_name}&viewparams=utmmap:{utmmap}"
                       f"&outputFormat=application/json&maxFeatures=50000")
            work.append((utmmap, type_name, url, out_path))

        if not work:
            return

        log(f"  Fetching {len(work)} map sheet(s) via WFS (up to {WFS_CONCURRENCY} in parallel)...")
        context = page.context
        sem = asyncio.Semaphore(WFS_CONCURRENCY)

        async def fetch_one(utmmap, type_name, url, out_path):
            async with sem:
                try:
                    resp = await context.request.get(url, timeout=90000)
                    if resp.ok:
                        text = await resp.text()
                        try:
                            data = json.loads(text)
                        except Exception:
                            data = None
                        if isinstance(data, dict) and 'features' in data:
                            out_path.write_text(text, encoding='utf-8')
                            log(f"  WFS utmmap {utmmap}: {len(data['features'])} features saved")
                            return
                    # Fall back to an in-page fetch (same-origin, passes Incapsula JS).
                    result = await page.evaluate(
                        """async (url) => {
                            try { const r = await fetch(url, { credentials: 'include' });
                                  if (!r.ok) return { error: r.status }; return await r.json(); }
                            catch(e) { return { error: e.toString() }; }
                        }""", url)
                    if result and 'error' not in result:
                        out_path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
                        log(f"  WFS utmmap {utmmap}: {len(result.get('features', []))} features saved (fallback)")
                    else:
                        log(f"  WFS utmmap {utmmap}: failed - {result}")
                except Exception as e:
                    log(f"  WFS utmmap {utmmap}: error - {e}")

        await asyncio.gather(*[fetch_one(*w) for w in work])

    async def fetch_parcels_wfs(self, bbox, session_name, output_dir="output",
                                location_info=None, utmmap=None, layer=None):
        """Click-free parcel fetch.

        Opens DOL only far enough to carry the Incapsula cookie (no 3D viewer),
        discovers the map sheet(s) over ``bbox`` from the WFS grid layer, then
        fetches the parcels for those sheets (BBOX-filtered, in parallel). If a
        known ``utmmap`` is passed, discovery is skipped entirely.

        Returns {utmmaps, feature_count, output_path, status}. status is one of
        "ok" | "blocked" (rate-limited/Incapsula) | "empty" (no sheet for area)."""
        self.captured_utmmaps = set()
        self.utmmap_layers = {}
        output_path = Path(output_dir) / session_name
        output_path.mkdir(parents=True, exist_ok=True)
        features_dir = output_path / "features"
        features_dir.mkdir(exist_ok=True)
        center_lon = (bbox[0] + bbox[2]) / 2

        # Known map sheet -> skip discovery entirely.
        given_utmmap = str(utmmap) if utmmap else None
        if given_utmmap:
            self.captured_utmmaps.add(given_utmmap)
            self.utmmap_layers[given_utmmap] = layer or (
                "LANDSMAPS:V_PARCEL48" if zone_for_longitude(center_lon) == 48
                else "LANDSMAPS:V_PARCEL47")

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=False, args=['--disable-blink-features=AutomationControlled'])
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='th-TH')
        page = await context.new_page()

        feature_count = 0
        status = "ok"
        try:
            log("WFS-direct: opening DOL website...")
            # Load the DOM only — the click-free WFS path never uses the 3D viewer,
            # and waiting for it was the cause of the ~4-minute client timeouts.
            try:
                await page.goto(self.DOL_URL, timeout=60000, wait_until='domcontentloaded')
            except Exception as e:
                log(f"Page load slow/incomplete ({e}) — trying WFS anyway.")
            await asyncio.sleep(4)

            if not given_utmmap:
                log("Discovering map sheet(s) via the WFS grid layer (no double-click)...")
                disc = await self._discover_utmmaps_via_index(page, bbox)
                if disc == "found":
                    log(f"Grid found map sheet(s): {sorted(self.captured_utmmaps)}")
                elif disc == "blocked":
                    status = "blocked"
                    log("DOL WFS returned no JSON after retries — likely rate-limited/"
                        "blocked (Incapsula). Back off and try again in a few minutes.")
                else:
                    status = "empty"
                    log("No map sheet found over this area (WFS grid returned nothing) — "
                        "it may be outside DOL parcel coverage.")

            if self.captured_utmmaps:
                log(f"Map sheet(s): {sorted(self.captured_utmmaps)} — fetching parcels via WFS...")
                await self._fetch_wfs_features(page, self.captured_utmmaps, features_dir,
                                               self.utmmap_layers, bbox)

            await self._save_session(output_path, session_name, bbox, location_info)
            for gj in features_dir.glob("*.geojson"):
                try:
                    feature_count += len(json.loads(gj.read_text(encoding='utf-8')).get('features', []))
                except Exception:
                    pass
        except Exception as e:
            log(f"WFS-direct error: {e}")
            status = "error"
        finally:
            try:
                await browser.close()
            except Exception:
                pass
            try:
                await playwright.stop()
            except Exception:
                pass

        return {
            "utmmaps": sorted(self.captured_utmmaps),
            "feature_count": feature_count,
            "output_path": str(output_path),
            "status": status,
        }

    async def _save_session(self, output_path: Path, session_name: str,
                            bbox: list[float], location_info: dict = None):
        """Write mission.json for the session (vector/WFS only — no tiles).
        gis_processor.process_to_shapefiles reads bbox, location, utmmaps and
        utmmapLayers from here."""
        mission_data = {
            "sessionName": session_name,
            "bbox": bbox,
            "timestamp": datetime.now().isoformat(),
            "utmmaps": sorted(self.captured_utmmaps),
            "utmmapLayers": self.utmmap_layers,
            "tiles": [],
        }
        if location_info:
            mission_data["location"] = location_info
        with open(output_path / "mission.json", 'w', encoding='utf-8') as f:
            json.dump(mission_data, f, indent=2, ensure_ascii=False)
        log(f"Saved session {session_name} ({len(self.captured_utmmaps)} map sheet(s)) to {output_path}")
