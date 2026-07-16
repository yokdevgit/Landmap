"""Tests for tile blank-detection, overview-junk filtering, and blank re-fetch.

These pin the root-cause fixes for the persistent blank-tile bug:
  * Bug 1 - Cesium overview tiles (huge span) are always transparent junk.
  * Bug 2 - race/timeout blanks at target zoom, recoverable by re-fetching the
            tile's exact URL. The old detector missed near-empty tiles.
"""
import asyncio
import json
from io import BytesIO

import pytest
from PIL import Image

from src.tile_quality import (
    ink_fraction,
    is_blank_tile,
    tile_span_deg,
    is_overview_tile,
    refetch_blank_tiles,
    geometry_bbox,
    load_parcel_bboxes,
    bbox_overlaps_any,
    dominant_sort_key,
)


# ---------- synthetic tile fixtures ----------
def _png(img) -> bytes:
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def fully_transparent(size=256):
    return Image.new("RGBA", (size, size), (0, 0, 0, 0))


def near_empty(size=256, ink_px=10):
    """RGBA tile with only a few opaque pixels (a stray parcel-edge sliver)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for i in range(ink_px):
        img.putpixel((i, 0), (255, 0, 0, 255))
    return img


def full_content(size=256):
    return Image.new("RGBA", (size, size), (120, 180, 90, 255))


def solid_opaque(size=256):
    return Image.new("RGB", (size, size), (255, 255, 255))


# ---------- ink_fraction / is_blank_tile ----------
def test_fully_transparent_is_blank():
    assert is_blank_tile(_png(fully_transparent())) is True


def test_near_empty_tile_is_blank():
    # 10 / 65536 = 0.015% ink, far below the 0.5% threshold -> blank.
    # This is exactly the case the OLD max(alpha)==0 detector MISSED.
    assert is_blank_tile(_png(near_empty(ink_px=10))) is True


def test_content_tile_is_not_blank():
    assert is_blank_tile(_png(full_content())) is False


def test_solid_opaque_tile_is_blank():
    # Opaque single-colour tile carries no information -> blank.
    assert is_blank_tile(_png(solid_opaque())) is True


def test_ink_fraction_extremes():
    assert ink_fraction(_png(fully_transparent())) == 0.0
    assert ink_fraction(_png(full_content())) == 1.0


def test_tile_just_above_threshold_is_not_blank():
    size = 100  # 10_000 px
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for i in range(60):  # 0.6% ink -> above 0.5% -> keep
        img.putpixel((i, 0), (0, 0, 0, 255))
    assert is_blank_tile(_png(img)) is False


# ---------- overview-junk filter ----------
def test_span_helper():
    assert tile_span_deg([100.0, 13.0, 102.8, 15.8]) == pytest.approx(2.8)


def test_overview_tile_detected_by_span():
    # ~312 km wide Cesium overview tile (real example from output/)
    assert is_overview_tile([98.4375, 11.25, 101.25, 14.0625]) is True


def test_real_tile_not_overview():
    # ~75 m wide real parcel tile (real example from output/)
    assert is_overview_tile([100.3697204, 13.7116241, 100.3704071, 13.7123107]) is False


def test_span_gap_boundaries():
    # Largest real content tile observed = 1.219 km (0.01098 deg) -> keep.
    assert is_overview_tile([100.0, 13.0, 100.01098, 13.01098]) is False
    # Smallest overview junk observed = 2.439 km (0.02197 deg) -> drop.
    assert is_overview_tile([100.0, 13.0, 100.02197, 13.02197]) is True


# ---------- refetch_blank_tiles (retry loop, injected fetcher) ----------
def _seed(images_dir, idx, img):
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / f"tile_{idx}.png").write_bytes(_png(img))


def test_refetch_replaces_only_blank_tiles(tmp_path):
    imgs = tmp_path / "images"
    _seed(imgs, 0, fully_transparent())  # blank -> should be re-fetched
    _seed(imgs, 1, full_content())       # content -> should be left alone
    tiles = [{"url": "u0"}, {"url": "u1"}]

    fetched = []

    async def fetch(url):
        fetched.append(url)
        return _png(full_content())

    replaced = asyncio.run(refetch_blank_tiles(tiles, imgs, fetch))

    assert replaced == 1
    assert fetched == ["u0"]  # only the blank tile's URL was hit
    assert is_blank_tile((imgs / "tile_0.png").read_bytes()) is False


def test_refetch_retries_until_content(tmp_path):
    imgs = tmp_path / "images"
    _seed(imgs, 0, fully_transparent())
    tiles = [{"url": "u0"}]
    seq = [_png(fully_transparent()), _png(fully_transparent()), _png(full_content())]

    async def fetch(url):
        return seq.pop(0)

    replaced = asyncio.run(refetch_blank_tiles(tiles, imgs, fetch, max_attempts=3))
    assert replaced == 1
    assert is_blank_tile((imgs / "tile_0.png").read_bytes()) is False


def test_refetch_gives_up_after_max_attempts(tmp_path):
    imgs = tmp_path / "images"
    _seed(imgs, 0, fully_transparent())
    tiles = [{"url": "u0"}]
    attempts = [0]

    async def fetch(url):
        attempts[0] += 1
        return _png(fully_transparent())  # server keeps returning blank

    replaced = asyncio.run(refetch_blank_tiles(tiles, imgs, fetch, max_attempts=3))
    assert replaced == 0
    assert attempts[0] == 3  # tried exactly max_attempts, then stopped
    assert is_blank_tile((imgs / "tile_0.png").read_bytes()) is True  # left untouched


def test_refetch_never_overwrites_with_blank(tmp_path):
    imgs = tmp_path / "images"
    _seed(imgs, 0, fully_transparent())
    original = (imgs / "tile_0.png").read_bytes()
    tiles = [{"url": "u0"}]

    async def fetch(url):
        return _png(near_empty(ink_px=5))  # still blank -> must not be written

    replaced = asyncio.run(refetch_blank_tiles(tiles, imgs, fetch, max_attempts=2))
    assert replaced == 0
    assert (imgs / "tile_0.png").read_bytes() == original


def test_refetch_skips_tiles_without_url(tmp_path):
    imgs = tmp_path / "images"
    _seed(imgs, 0, fully_transparent())
    tiles = [{}]  # no 'url' -> cannot re-fetch, skip gracefully

    async def fetch(url):
        raise AssertionError("should not be called")

    replaced = asyncio.run(refetch_blank_tiles(tiles, imgs, fetch))
    assert replaced == 0


# ---------- WFS parcel-overlap: don't retry "no line" (no-parcel) tiles ----------
def test_geometry_bbox_polygon():
    geom = {"type": "Polygon",
            "coordinates": [[[100.0, 13.0], [100.1, 13.0], [100.1, 13.1], [100.0, 13.1], [100.0, 13.0]]]}
    assert geometry_bbox(geom) == (100.0, 13.0, 100.1, 13.1)


def test_geometry_bbox_multipolygon():
    geom = {"type": "MultiPolygon",
            "coordinates": [[[[100.0, 13.0], [100.2, 13.0], [100.2, 13.2], [100.0, 13.0]]]]}
    assert geometry_bbox(geom) == (100.0, 13.0, 100.2, 13.2)


def test_geometry_bbox_none():
    assert geometry_bbox(None) is None


def test_bbox_overlaps_any():
    boxes = [(100.0, 13.0, 100.1, 13.1)]
    assert bbox_overlaps_any([100.05, 13.05, 100.06, 13.06], boxes) is True   # inside
    assert bbox_overlaps_any([100.09, 13.09, 100.2, 13.2], boxes) is True     # partial
    assert bbox_overlaps_any([100.5, 13.5, 100.6, 13.6], boxes) is False      # far away


def test_load_parcel_bboxes(tmp_path):
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon",
         "coordinates": [[[100.0, 13.0], [100.1, 13.0], [100.1, 13.1], [100.0, 13.0]]]}},
        {"type": "Feature", "geometry": None},  # null geometry -> ignored
    ]}
    p = tmp_path / "parcels.geojson"
    p.write_text(json.dumps(gj), encoding="utf-8")
    assert load_parcel_bboxes([p]) == [(100.0, 13.0, 100.1, 13.1)]


def test_dominant_sort_key_zero_at_dominant():
    assert dominant_sort_key(1.0, 1.0) == 0.0


def test_dominant_sort_key_symmetric_in_zoom():
    # A tile 2x finer and one 2x coarser are equally far from the dominant zoom.
    assert dominant_sort_key(0.5, 1.0) == pytest.approx(dominant_sort_key(2.0, 1.0))


def test_dominant_zoom_tile_composites_last():
    # Sorting by the key descending must put the dominant-zoom tile LAST (on top),
    # so over-zoomed fine tiles don't cover the good tiles with sparse thin lines.
    pxs = [4.0, 0.25, 2.0, 0.5, 1.0]
    ordered = sorted(pxs, key=lambda p: dominant_sort_key(p, 1.0), reverse=True)
    assert ordered[-1] == 1.0


def test_refetch_skips_blanks_with_no_parcel(tmp_path):
    imgs = tmp_path / "images"
    _seed(imgs, 0, fully_transparent())  # blank over NO parcel -> must NOT retry
    _seed(imgs, 1, fully_transparent())  # blank over a parcel  -> retry
    tiles = [
        {"url": "u0", "bbox": [100.5, 13.5, 100.51, 13.51]},   # far from parcels
        {"url": "u1", "bbox": [100.0, 13.0, 100.01, 13.01]},   # inside a parcel
    ]
    parcels = [(100.0, 13.0, 100.1, 13.1)]  # covers tile 1 only

    def has_data(bbox):
        return bbox_overlaps_any(bbox, parcels)

    fetched = []

    async def fetch(url):
        fetched.append(url)
        return _png(full_content())

    replaced = asyncio.run(refetch_blank_tiles(tiles, imgs, fetch, has_data=has_data))
    assert fetched == ["u1"]  # the no-parcel blank was left alone
    assert replaced == 1
