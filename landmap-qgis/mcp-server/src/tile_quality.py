"""Tile quality helpers: blank detection, overview-junk filtering, blank re-fetch.

Extracted as pure, network-free functions so the blank-tile fixes are unit
tested without a live DOL fetch. See tests/test_tile_quality.py.

Root cause these address (measured across the saved output/ sessions):
  * Overview junk  - while the Cesium camera flies between altitudes, GeoServer
    serves huge low-zoom tiles where the parcel layer is below its min render
    scale, so it returns a fully-transparent PNG. 100% of tiles wider than the
    real scan resolution are blank. Real tiles span <= 1.219 km; overview junk
    starts at 2.439 km -> a ~2 km (0.018 deg) cutoff separates them cleanly.
  * Race blanks    - at target zoom GeoServer intermittently returns an empty
    render (proven: the same bbox came back blank in one run and full of
    parcels in another). These are recoverable by re-fetching the tile's exact
    stored URL. The old detector (RGBA and max(alpha)==0) missed near-empty
    tiles that carry a few stray pixels, so they were never retried.
"""
from __future__ import annotations

import json
import math
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

import numpy as np
from PIL import Image

# A tile is "blank" if fewer than this fraction of pixels carry visible content.
# Observed near-empty race tiles sit at 0.002%-0.4% ink; real tiles are far
# denser, so 0.5% is a wide safety margin between the two populations.
DEFAULT_INK_THRESHOLD = 0.005

# Tiles wider than this (in degrees of longitude) are Cesium overview artefacts,
# not real parcel tiles. Sits in the empirical gap [1.219 km, 2.439 km].
MAX_TILE_SPAN_DEG = 0.018  # ~2.0 km at Thailand's latitude

ImageSource = Union[str, Path, bytes, bytearray]


def _open(source: ImageSource) -> Image.Image:
    if isinstance(source, (bytes, bytearray)):
        return Image.open(BytesIO(source))
    return Image.open(Path(source))


def ink_fraction(source: ImageSource) -> float:
    """Fraction of pixels [0.0-1.0] that carry visible content.

    RGBA -> fraction of non-transparent pixels.
    Opaque -> 0.0 if the tile is a single solid colour, else 1.0.
    """
    img = _open(source)
    img.load()
    if img.mode == "RGBA":
        alpha = np.asarray(img)[:, :, 3]
        if alpha.size == 0:
            return 0.0
        return float(np.count_nonzero(alpha) / alpha.size)
    # Opaque tile: blank only if it is one flat colour.
    colors = img.getcolors(maxcolors=16)
    if colors is not None and len(colors) <= 1:
        return 0.0
    return 1.0


def is_blank_tile(source: ImageSource, ink_threshold: float = DEFAULT_INK_THRESHOLD) -> bool:
    """True if the tile carries essentially no visible content.

    Catches both fully-transparent tiles and near-empty tiles with a few stray
    pixels. Returns False (keep the tile) if the image cannot be read, so an
    unreadable tile is never silently dropped.
    """
    try:
        return ink_fraction(source) < ink_threshold
    except Exception:
        return False


def tile_span_deg(bbox) -> float:
    """Width of a tile's bbox [minLon, minLat, maxLon, maxLat] in degrees."""
    return abs(float(bbox[2]) - float(bbox[0]))


def is_overview_tile(bbox, max_span_deg: float = MAX_TILE_SPAN_DEG) -> bool:
    """True if the tile is a coarse Cesium overview tile (always blank junk)."""
    return tile_span_deg(bbox) > max_span_deg


def _iter_xy(coords):
    """Yield (x, y) leaf pairs from arbitrarily nested GeoJSON coordinate lists."""
    if (isinstance(coords, (list, tuple)) and len(coords) >= 2
            and isinstance(coords[0], (int, float))
            and isinstance(coords[1], (int, float))):
        yield (coords[0], coords[1])
        return
    if isinstance(coords, (list, tuple)):
        for c in coords:
            yield from _iter_xy(c)


def geometry_bbox(geometry: Optional[dict]):
    """Bounding box (minx, miny, maxx, maxy) of a GeoJSON geometry, or None."""
    if not geometry:
        return None
    xs, ys = [], []
    for x, y in _iter_xy(geometry.get("coordinates", [])):
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def load_parcel_bboxes(geojson_paths) -> list:
    """Return one bounding box per parcel feature across the given GeoJSON files.

    Used as the ground truth for 'is there a parcel line here?' — a blank tile
    whose bbox overlaps none of these is genuinely empty and must not be retried.
    """
    boxes = []
    for p in geojson_paths:
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        for feat in data.get("features", []):
            bb = geometry_bbox(feat.get("geometry"))
            if bb:
                boxes.append(bb)
    return boxes


def dominant_sort_key(px, dominant):
    """Log-scale distance of a tile's pixel size from the dominant resolution.

    The mosaic composites the LAST source on top, so sorting tiles by this key
    descending puts dominant-zoom tiles last (= on top). Over-zoomed fine tiles
    (captured when the camera dove in during the double-click) otherwise win the
    compositing and cover the good target-zoom tiles with only a few sparse
    parcels — the "thin line" bug. Preferring the dominant zoom makes the good
    tiles win; off-zoom tiles only fill gaps where nothing closer exists.
    """
    if not px or not dominant or px <= 0 or dominant <= 0:
        return 0.0
    return abs(math.log(px / dominant))


def bbox_overlaps_any(tile_bbox, boxes) -> bool:
    """True if tile_bbox [minlon,minlat,maxlon,maxlat] overlaps any box in boxes."""
    tx0, ty0, tx1, ty1 = tile_bbox[0], tile_bbox[1], tile_bbox[2], tile_bbox[3]
    for bx0, by0, bx1, by1 in boxes:
        if tx0 <= bx1 and tx1 >= bx0 and ty0 <= by1 and ty1 >= by0:
            return True
    return False


# ---- Parcel-search helpers (DOL allows ~20 parcel-query clicks per session) ----
def spread_click_points(canvas_box, n):
    """Up to ``n`` canvas click points, spread out, centre first.

    Used to search for a parcel (to activate the layer) while respecting DOL's
    per-session click budget: spend few clicks per cell and cover many cells.
    """
    cx = canvas_box["x"] + canvas_box["width"] / 2
    cy = canvas_box["y"] + canvas_box["height"] / 2
    ox = canvas_box["width"] * 0.25
    oy = canvas_box["height"] * 0.25
    points = [
        (cx, cy),
        (cx - ox, cy), (cx + ox, cy), (cx, cy - oy), (cx, cy + oy),
        (cx - ox, cy - oy), (cx + ox, cy - oy), (cx - ox, cy + oy), (cx + ox, cy + oy),
    ]
    return points[:max(1, n)]


def clicks_per_cell(num_cells, budget, cap=3):
    """How many click positions to try per grid cell so ``budget`` clicks cover
    all cells. DOL blocks after ~20 parcel-query clicks per session, so spending 5
    on one cell wastes the budget — spread it thin across cells instead.
    """
    if num_cells <= 0:
        return cap
    return max(1, min(cap, budget // num_cells))


def wfs_getfeature_url(wfs_base, type_name, utmmap, bbox, epsg, max_features=50000):
    """Build a DOL WFS GetFeature URL for the parcels inside `bbox`.

    The V_PARCEL view is parameterised, so viewparams=utmmap is required; the BBOX
    must be in the layer's native CRS (EPSG:24047/24048) to actually restrict to
    the area. Returns the complete URL (GeoJSON output)."""
    e0, n0, e1, n1 = bbox_to_native(bbox, epsg)
    return (
        f"{wfs_base}?service=WFS&version=1.0.0&request=GetFeature"
        f"&typeName={type_name}"
        f"&viewparams=utmmap:{utmmap}"
        f"&outputFormat=application/json"
        f"&maxFeatures={max_features}"
        f"&BBOX={e0},{n0},{e1},{n1},EPSG:{epsg}"
    )


_ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10}


def label_to_utmmap(indlabel):
    """Convert a 1:4000 map-sheet index label to the utmmap id V_PARCEL needs.

    Format is "<utm1> <ROMAN section> <number>" (e.g. '5136 III 6418'); the utmmap
    is utm1 + the section as a digit + number -> '513636418'. Verified against live
    DOL for 4 sheets. Returns None if the label doesn't parse. This is what lets us
    discover the map sheet from the WFS grid layer with NO double-click.
    """
    parts = (indlabel or "").split()
    if len(parts) != 3:
        return None
    utm1, roman, number = parts
    section = _ROMAN.get(roman.upper())
    if section is None or not utm1.isdigit() or not number.isdigit():
        return None
    return f"{utm1}{section}{number}"


def wfs_index_url(wfs_base, zone, bbox, max_features=50):
    """WFS GetFeature URL for the 1:4000 map-sheet grid (V_INDEX4000_<zone>_LANDNO)
    over bbox, in the native CRS — used to discover the map sheet(s) click-free."""
    epsg = 24047 if zone == 47 else 24048
    e0, n0, e1, n1 = bbox_to_native(bbox, epsg)
    return (
        f"{wfs_base}?service=WFS&version=1.0.0&request=GetFeature"
        f"&typeName=LANDSMAPS:V_INDEX4000_{zone}_LANDNO"
        f"&outputFormat=application/json&maxFeatures={max_features}"
        f"&BBOX={e0},{n0},{e1},{n1},EPSG:{epsg}"
    )


def parse_wms_utmmap(url):
    """Extract (utmmap, layer) from a DOL WMS tile URL, or (None, None)."""
    import re
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(url).query)
    viewparams = q.get('viewparams', q.get('VIEWPARAMS', ['']))[0]
    layer = q.get('LAYERS', q.get('layers', ['']))[0] or None
    m = re.search(r'utmmap:(\d+)', viewparams)
    return (m.group(1) if m else None, layer)


def native_epsg_for_layer(layer):
    """DOL parcel layers are stored in Indian 1975 UTM (metres): V_PARCEL47 ->
    EPSG:24047, V_PARCEL48 -> EPSG:24048. The WFS BBOX filter must be given in
    this native CRS to actually restrict to the requested area (a 4326 BBOX does
    not filter). Returns the EPSG int, or None if unknown."""
    u = (layer or "").upper()
    if "PARCEL48" in u:
        return 24048
    if "PARCEL47" in u:
        return 24047
    return None


def bbox_to_native(bbox, epsg):
    """Reproject a [minlon, minlat, maxlon, maxlat] EPSG:4326 bbox to the given
    projected EPSG, returning (minE, minN, maxE, maxN) for a WFS BBOX filter."""
    from pyproj import Transformer
    t = Transformer.from_crs(4326, epsg, always_xy=True)
    min_e, min_n = t.transform(bbox[0], bbox[1])
    max_e, max_n = t.transform(bbox[2], bbox[3])
    return (min_e, min_n, max_e, max_n)


# The DOL parcel datum transform to use. pyproj defaults to "Indian 1975 to
# WGS 84 (4)" (a 7-parameter fit) but DOL's parcels only line up with basemaps
# (streets + the admin boundary, verified for Si Lom) under the 3-parameter
# "(2)" variant — "(4)" sits ~187 m NW of where the land actually is.
DOL_DATUM_TRANSFORM = "Indian 1975 to WGS 84 (2)"


def dol_wgs84_transformer(native_epsg):
    """pyproj Transformer from a DOL parcel CRS (Indian 1975 UTM, EPSG:240xx) to
    WGS84 (EPSG:4326), forcing the DOL_DATUM_TRANSFORM datum operation. Selected by
    name (not list index) so a pyproj registry reorder can't silently pick the
    wrong one. Falls back to pyproj's default transformer if the named op is
    unavailable. Use this instead of GeoDataFrame.to_crs(4326) for DOL parcels."""
    from pyproj import Transformer
    from pyproj.transformer import TransformerGroup
    try:
        tg = TransformerGroup(native_epsg, 4326, always_xy=True)
        for t in tg.transformers:
            if DOL_DATUM_TRANSFORM in (t.description or ""):
                return t
    except Exception:
        pass
    return Transformer.from_crs(native_epsg, 4326, always_xy=True)


# Known-dense spots (lon, lat) where a double-click reliably hits a parcel and
# activates the layer. One per UTM parcel zone (activation is per-zone). Used to
# turn the parcel layer ON before scanning a possibly-sparse target area.
ACTIVATION_ANCHORS = {
    47: (100.3967, 13.7203),   # Bang Khae Nuea, Bangkok — representative point (inside polygon)
    48: (102.0686, 14.9716),   # Nai Mueang, Nakhon Ratchasima (Korat) — representative point
}


def zone_for_longitude(lon):
    """DOL parcel UTM zone from longitude: > 102 deg E -> zone 48 (east/NE), else 47."""
    return 48 if lon > 102 else 47


def activation_anchor(bbox):
    """(lon, lat) of the known-dense activation anchor for the target bbox's zone."""
    lon = (bbox[0] + bbox[2]) / 2
    return ACTIVATION_ANCHORS[zone_for_longitude(lon)]


def attempt_offset(attempt):
    """Fractional (dx, dy) shift of the grid targets for each session-retry.

    After the click budget is spent without finding a parcel, DOL requires closing
    the session and reopening at a NEW location; shifting the targets makes a fresh
    session probe different spots inside the same area.
    """
    offsets = [(0.0, 0.0), (0.25, 0.25), (-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25)]
    return offsets[attempt % len(offsets)]


async def refetch_blank_tiles(
    tiles: list[dict],
    images_dir: Path,
    fetch_url: Callable[[str], Awaitable[Union[bytes, None]]],
    *,
    max_attempts: int = 3,
    ink_threshold: float = DEFAULT_INK_THRESHOLD,
    has_data: Optional[Callable[[list], bool]] = None,
) -> int:
    """Re-fetch each blank tile directly from its stored WMS URL.

    For every tile whose PNG is currently blank, GET its exact ``url`` up to
    ``max_attempts`` times via the injected ``fetch_url`` coroutine. The first
    response that is a valid, non-blank image overwrites the tile on disk. A
    tile is never overwritten with another blank response, and tiles that have
    no ``url`` are skipped. Returns the number of tiles replaced.

    If ``has_data`` is given, a blank tile is only retried when
    ``has_data(tile['bbox'])`` is true — genuine no-parcel ("no line") tiles are
    left alone instead of being pointlessly re-fetched.

    ``fetch_url`` is injected (rather than doing the HTTP call here) so the loop
    is fully unit tested without a browser or network.
    """
    images_dir = Path(images_dir)
    # Snapshot the blank tiles up front so the loop is immune to the tile list
    # changing underneath it (e.g. a still-attached capture listener appending
    # new tiles as the re-fetch responses come back).
    blank_indices = [
        i for i in range(len(tiles))
        if (images_dir / f"tile_{i}.png").exists()
        and is_blank_tile(images_dir / f"tile_{i}.png", ink_threshold)
    ]
    replaced = 0
    for idx in blank_indices:
        tile = tiles[idx]
        png_path = images_dir / f"tile_{idx}.png"
        url = tile.get("url")
        if not url:
            continue
        # Skip genuine no-data tiles: no parcel line under them, so re-fetching
        # will only ever return the same blank.
        if has_data is not None and tile.get("bbox") and not has_data(tile["bbox"]):
            continue
        for _ in range(max_attempts):
            body = await fetch_url(url)
            if not body or len(body) < 500:
                continue
            if is_blank_tile(body, ink_threshold):
                continue  # still blank - do not overwrite, try again
            png_path.write_bytes(body)
            tile["size"] = len(body)
            replaced += 1
            break
    return replaced
