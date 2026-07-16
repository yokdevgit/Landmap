"""
Pure helpers for the click-free DOL WFS pipeline (no network, unit-tested).

- zone_for_longitude / native_epsg_for_layer: which UTM zone/CRS a parcel is in.
- bbox_to_native: reproject a WGS84 bbox to the WFS BBOX filter CRS.
- wfs_index_url / label_to_utmmap: discover the 1:4000 map sheet(s) over a bbox
  from the grid layer and turn each sheet label into the utmmap id V_PARCEL needs.
- wfs_getfeature_url: the BBOX-filtered parcel GetFeature URL.
- dol_wgs84_transformer: the correct Indian 1975 -> WGS84 datum transform for DOL.

See tests/test_wfs_helpers.py.
"""


def zone_for_longitude(lon):
    """DOL parcel UTM zone from longitude: > 102 deg E -> zone 48 (east/NE), else 47."""
    return 48 if lon > 102 else 47


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
