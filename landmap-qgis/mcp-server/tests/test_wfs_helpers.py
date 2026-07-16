"""Unit tests for the click-free DOL WFS helpers (src/wfs_helpers.py)."""
from src.wfs_helpers import (
    zone_for_longitude,
    native_epsg_for_layer,
    bbox_to_native,
    wfs_getfeature_url,
    label_to_utmmap,
    wfs_index_url,
    dol_wgs84_transformer,
)


def test_zone_for_longitude():
    assert zone_for_longitude(100.5) == 47   # Bangkok (west)
    assert zone_for_longitude(98.9) == 47      # Chiang Mai
    assert zone_for_longitude(102.0) == 47     # boundary: 102 -> still 47
    assert zone_for_longitude(102.1) == 48     # just east -> 48


def test_native_epsg_for_layer():
    assert native_epsg_for_layer("LANDSMAPS:V_PARCEL47") == 24047
    assert native_epsg_for_layer("LANDSMAPS:V_PARCEL48") == 24048
    assert native_epsg_for_layer("") is None


def test_bbox_to_native_matches_known_value():
    # Phra Sing bbox in EPSG:24047 — verified against live DOL WFS.
    e0, n0, e1, n1 = bbox_to_native([98.9775, 18.781, 98.9934, 18.7896], 24047)
    assert abs(e0 - 498145.6) < 2
    assert abs(n0 - 2076243.2) < 2
    assert abs(e1 - 499821.3) < 2
    assert abs(n1 - 2077194.6) < 2


def test_wfs_getfeature_url():
    u = wfs_getfeature_url("https://x/wfs", "LANDSMAPS:V_PARCEL47", "474619676",
                           [98.9775, 18.781, 98.9934, 18.7896], 24047, max_features=50000)
    assert "request=GetFeature" in u
    assert "typeName=LANDSMAPS:V_PARCEL47" in u
    assert "viewparams=utmmap:474619676" in u
    assert "outputFormat=application/json" in u
    assert "maxFeatures=50000" in u
    assert "EPSG:24047" in u
    assert "&BBOX=498" in u  # native easting, verified against live WFS


def test_label_to_utmmap():
    # utmmap = utm1 + roman-section-digit + number — verified vs live DOL.
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
    assert "EPSG:24047" in u
    assert "&BBOX=498" in u
    u48 = wfs_index_url("https://x/wfs", 48, [104.8, 15.2, 104.9, 15.3])
    assert "V_INDEX4000_48_LANDNO" in u48 and "EPSG:24048" in u48


def test_dol_wgs84_transformer_uses_variant_2():
    # DOL parcels align to basemaps under the Indian1975->WGS84 "(2)" transform,
    # not pyproj's default "(4)" (which is ~187 m NW). Verified vs streets/boundary.
    t = dol_wgs84_transformer(24047)
    assert "(2)" in t.description
    lon, lat = t.transform(667362.88937339, 1516748.45877363)  # a Silom parcel corner
    assert abs(lon - 100.544664) < 1e-4   # (2) position; (4) default would be 100.542995
    assert abs(lat - 13.717807) < 1e-4
    assert "(2)" in dol_wgs84_transformer(24048).description
