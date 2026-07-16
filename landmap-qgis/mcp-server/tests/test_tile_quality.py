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
    spread_click_points,
    clicks_per_cell,
    attempt_offset,
    native_epsg_for_layer,
    bbox_to_native,
    zone_for_longitude,
    activation_anchor,
    ACTIVATION_ANCHORS,
    wfs_getfeature_url,
    parse_wms_utmmap,
    wfs_index_url,
    label_to_utmmap,
    dol_true_epsg,
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


# ---------- parcel-search: click spread, budget, session-retry offset ----------
def test_spread_click_points_center_first():
    cb = {"x": 0, "y": 0, "width": 1000, "height": 800}
    assert spread_click_points(cb, 1) == [(500.0, 400.0)]  # single click = centre


def test_spread_click_points_count_and_center():
    cb = {"x": 0, "y": 0, "width": 1000, "height": 800}
    pts = spread_click_points(cb, 5)
    assert len(pts) == 5 and pts[0] == (500.0, 400.0)  # centre first, then spread


def test_spread_click_points_caps_at_available():
    cb = {"x": 0, "y": 0, "width": 100, "height": 100}
    assert len(spread_click_points(cb, 99)) == 9  # 9 distinct positions defined


def test_clicks_per_cell_spreads_the_budget():
    # Many cells -> 1 click each so the 20-click budget covers the whole area.
    assert clicks_per_cell(16, 18) == 1
    # Few cells -> a few clicks each, but capped so we never blow the budget.
    assert clicks_per_cell(4, 18) == 3
    assert clicks_per_cell(0, 18) == 3


def test_attempt_offset_differs_per_session():
    assert attempt_offset(0) == (0.0, 0.0)          # first session: no shift
    assert attempt_offset(1) != attempt_offset(0)   # retries probe different spots
    assert attempt_offset(2) != attempt_offset(1)


# ---------- WFS BBOX filter: native UTM CRS per parcel zone ----------
def test_native_epsg_for_layer():
    assert native_epsg_for_layer("LANDSMAPS:V_PARCEL47") == 24047
    assert native_epsg_for_layer("LANDSMAPS:V_PARCEL48") == 24048
    assert native_epsg_for_layer("") is None


def test_bbox_to_native_matches_known_value():
    # rb_phrasing_1 bbox in EPSG:24047 — verified against live DOL WFS.
    e0, n0, e1, n1 = bbox_to_native([98.9775, 18.781, 98.9934, 18.7896], 24047)
    assert abs(e0 - 498145.6) < 2
    assert abs(n0 - 2076243.2) < 2
    assert abs(e1 - 499821.3) < 2
    assert abs(n1 - 2077194.6) < 2


# ---------- parcel-layer activation anchors (per UTM zone) ----------
def test_zone_for_longitude():
    assert zone_for_longitude(100.5) == 47   # Bangkok (west)
    assert zone_for_longitude(98.9) == 47     # Chiang Mai
    assert zone_for_longitude(102.0) == 47    # boundary: 102 -> still 47
    assert zone_for_longitude(102.1) == 48    # just east -> 48
    assert zone_for_longitude(104.8) == 48    # Ubon (east)


def test_activation_anchor_picks_zone_anchor():
    assert activation_anchor([100.5, 13.7, 100.52, 13.72]) == ACTIVATION_ANCHORS[47]   # Bangkok
    assert activation_anchor([104.8, 15.2, 104.9, 15.3]) == ACTIVATION_ANCHORS[48]      # Ubon


# ---------- WFS-direct helpers ----------
def test_wfs_getfeature_url():
    u = wfs_getfeature_url("https://x/wfs", "LANDSMAPS:V_PARCEL47", "474619676",
                           [98.9775, 18.781, 98.9934, 18.7896], 24047, max_features=50000)
    assert "request=GetFeature" in u
    assert "typeName=LANDSMAPS:V_PARCEL47" in u
    assert "viewparams=utmmap:474619676" in u
    assert "outputFormat=application/json" in u
    assert "maxFeatures=50000" in u
    assert "EPSG:24047" in u  # declared label (server does no reprojection)
    # BBOX numbers are WGS84/UTM (no datum shift) so they match DOL's stored coords
    assert "&BBOX=497" in u   # 24047 would be 498xxx; 32647 (true) is 497xxx


def test_parse_wms_utmmap():
    url = ("https://landsmaps.dol.go.th/geoserver/LANDSMAPS/wms?"
           "LAYERS=LANDSMAPS%3AV_PARCEL47&viewparams=utmmap%3A503624816&format=image%2Fpng")
    utm, layer = parse_wms_utmmap(url)
    assert utm == "503624816"
    assert layer == "LANDSMAPS:V_PARCEL47"


def test_parse_wms_utmmap_missing():
    assert parse_wms_utmmap("https://x/wms?service=WMS&request=GetMap") == (None, None)


# ---------- map-sheet grid -> utmmap (click-free discovery) ----------
def test_label_to_utmmap():
    # utmmap = utm1 + roman-section-digit + number — verified vs live DOL for all 4.
    assert label_to_utmmap("5136 III 6418") == "513636418"
    assert label_to_utmmap("5036 II 4816") == "503624816"
    assert label_to_utmmap("5438 IV 9060") == "543849060"
    assert label_to_utmmap("4746 I 9676") == "474619676"


def test_label_to_utmmap_bad():
    assert label_to_utmmap("garbage") is None
    assert label_to_utmmap("5136 Z 6418") is None   # unknown roman section
    assert label_to_utmmap("") is None


def test_wfs_index_url():
    u = wfs_index_url("https://x/wfs", 47, [98.9775, 18.781, 98.9934, 18.7896])
    assert "typeName=LANDSMAPS:V_INDEX4000_47_LANDNO" in u
    assert "EPSG:24047" in u          # declared label
    assert "&BBOX=497" in u           # WGS84/UTM numbers (no datum shift)
    # zone 48 picks the 48 grid + CRS
    u48 = wfs_index_url("https://x/wfs", 48, [104.8, 15.2, 104.9, 15.3])
    assert "V_INDEX4000_48_LANDNO" in u48 and "EPSG:24048" in u48


def test_dol_true_epsg():
    # DOL mislabels the datum: 240xx numbers are really WGS84/UTM 326xx (same zone).
    assert dol_true_epsg(24047) == 32647
    assert dol_true_epsg(24048) == 32648
    assert dol_true_epsg("24047") == 32647   # accepts str
    assert dol_true_epsg(4326) == 4326        # leaves other CRS untouched
    assert dol_true_epsg(None) is None


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
