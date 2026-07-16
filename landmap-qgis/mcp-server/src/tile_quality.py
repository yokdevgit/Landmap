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
