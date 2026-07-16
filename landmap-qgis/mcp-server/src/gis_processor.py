"""
GIS Processor — convert DOL WFS parcels into GIS-ready files.

Reads a session's WFS GeoJSON (features/) and produces:
- parcel_dol.shp   — parcel polygons (reprojected Indian 1975 -> WGS84)
- boundary.shp     — admin boundary polygon (from the local shapefile DB)
- grid_4000.shp    — the 1:4000 map-sheet ids found
- {session}.qgs    — QGIS project (OSM basemap + the vector layers)
- {session}_shp.zip — everything bundled
"""

import json
import os
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

try:
    import geopandas as gpd
    import pandas as pd
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False

# Import boundary service for actual geometry
from .boundary_service import BoundaryService
from .wfs_helpers import dol_wgs84_transformer

# Initialize boundary service
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SHAPEFILE_DIR = os.environ.get(
    "LANDMAP_SHAPEFILE_DIR",
    str(_REPO_ROOT / "shapefiles")
)
boundary_service = BoundaryService(SHAPEFILE_DIR)


class GISProcessor:
    """Convert a session's DOL WFS parcels into shapefiles + a QGIS project."""

    def __init__(self, output_dir: str):
        """
        Initialize with output directory.

        Args:
            output_dir: Base directory for session outputs
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def list_sessions(self) -> list[dict]:
        """List all available sessions."""
        sessions = []

        for session_dir in self.output_dir.iterdir():
            if session_dir.is_dir():
                mission_file = session_dir / "mission.json"
                if mission_file.exists():
                    try:
                        with open(mission_file, 'r', encoding='utf-8') as f:
                            mission_data = json.load(f)

                        sessions.append({
                            "name": session_dir.name,
                            "tile_count": mission_data.get("tileCount", 0),
                            "created_at": mission_data.get("timestamp", "Unknown")
                        })
                    except Exception:
                        pass

        return sorted(sessions, key=lambda x: x.get("created_at", ""), reverse=True)

    async def process_to_shapefiles(self, session_name: str) -> dict:
        """
        Convert captured WFS features into shapefiles + QGIS project.

        Output structure:
          {session_name}/data/
            parcel_dol.shp       - parcel polygons from DOL WFS
            boundary.shp         - admin boundary polygon
            grid_4000.shp        - UTM 4000-scale map grid
            {session_name}.qgs   - QGIS project file
          {session_name}_shp.zip

        Returns:
            Dict with success, parcel_count, zip_path
        """
        if not HAS_GEOPANDAS:
            return {"success": False, "error": "geopandas not installed"}

        session_dir = self.output_dir / session_name
        mission_file = session_dir / "mission.json"
        features_dir = session_dir / "features"

        if not mission_file.exists():
            return {"success": False, "error": f"Session '{session_name}' not found"}

        with open(mission_file, 'r', encoding='utf-8') as f:
            mission_data = json.load(f)

        bbox = mission_data.get("bbox", [])
        location_info = mission_data.get("location", {})
        utmmaps = mission_data.get("utmmaps", [])
        utmmap_layers = mission_data.get("utmmapLayers", {})

        data_dir = session_dir / "data"
        data_dir.mkdir(exist_ok=True)

        results = {}

        # 1. Parcel shapefile from WFS GeoJSON
        import sys

        # Load the admin boundary FIRST — we clip parcels to it. A subdistrict's
        # bounding box can be ~3x its polygon area, so the WFS (BBOX-filtered)
        # returns many parcels from neighbouring subdistricts. Clipping keeps only
        # the requested area and roughly halves the data to reproject/write.
        boundary_gdf = None
        boundary_union = None
        if location_info:
            try:
                boundary_gdf = boundary_service.get_geometry(
                    location_info.get("province"), location_info.get("district"),
                    location_info.get("subdistrict"))
                if boundary_gdf is not None and not boundary_gdf.empty:
                    from shapely.ops import unary_union
                    boundary_union = unary_union(boundary_gdf.to_crs(4326).geometry.values)
            except Exception as e:
                print(f"Error loading boundary: {e}", file=sys.stderr)

        # 1. Parcel shapefile from WFS GeoJSON
        parcel_count = 0
        if features_dir.exists():
            geojson_files = list(features_dir.glob("utmmap_*.geojson"))
            if geojson_files:
                gdfs = []
                for gf in geojson_files:
                    try:
                        gdf = gpd.read_file(gf)
                        if not gdf.empty:
                            gdfs.append(gdf)
                    except Exception as e:
                        print(f"Error reading {gf}: {e}", file=sys.stderr)

                if gdfs:
                    parcel_gdf = gpd.GeoDataFrame(
                        pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)
                    parcel_gdf = parcel_gdf.drop_duplicates(subset=['geometry'])
                    parcel_gdf = parcel_gdf[parcel_gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])]
                    # Reproject Indian 1975 UTM -> WGS84 with the DOL "(2)" datum
                    # transform (pyproj's default "(4)" lands ~187 m NW). VECTORIZED:
                    # one pyproj call over all vertices — the old per-geometry apply
                    # stalled on dense areas.
                    native_epsg = parcel_gdf.crs.to_epsg() if parcel_gdf.crs else None
                    if native_epsg:
                        import numpy as np
                        import shapely
                        tf = dol_wgs84_transformer(native_epsg)
                        geoms = parcel_gdf.geometry.values
                        coords = shapely.get_coordinates(geoms)
                        xs, ys = tf.transform(coords[:, 0], coords[:, 1])
                        parcel_gdf['geometry'] = shapely.set_coordinates(
                            np.array(geoms), np.column_stack([xs, ys]))
                        parcel_gdf = parcel_gdf.set_crs("EPSG:4326", allow_override=True)
                    else:
                        parcel_gdf = parcel_gdf.to_crs("EPSG:4326")
                    # Clip to the requested subdistrict (keep parcels intersecting it).
                    if boundary_union is not None:
                        import shapely
                        before = len(parcel_gdf)
                        mask = shapely.intersects(parcel_gdf.geometry.values, boundary_union)
                        parcel_gdf = parcel_gdf[mask]
                        print(f"Clipped to boundary: {len(parcel_gdf)}/{before} parcels", file=sys.stderr)
                    parcel_gdf.to_file(data_dir / "parcel_dol.shp", encoding='utf-8')
                    parcel_count = len(parcel_gdf)
                    results['parcel_count'] = parcel_count
                    print(f"Saved {parcel_count} parcel features", file=sys.stderr)

        # 2. Write the boundary shapefile (loaded above).
        if boundary_gdf is not None and not boundary_gdf.empty:
            try:
                boundary_gdf.to_file(data_dir / "boundary.shp", encoding='utf-8')
                results['boundary'] = True
            except Exception as e:
                print(f"Error saving boundary: {e}", file=sys.stderr)

        # 3. Grid shapefile from utmmap IDs found during scan
        if utmmaps and parcel_count > 0:
            try:
                self._generate_grid_shapefile(utmmaps, data_dir / "grid_4000.shp")
                results['grid'] = True
            except Exception as e:
                import sys; print(f"Error generating grid: {e}", file=sys.stderr)

        # 4. Ensure the gis/ folder exists (vector-only session, no tiles).
        gis_dir = session_dir / "gis"
        gis_dir.mkdir(parents=True, exist_ok=True)

        # Write boundary as GeoJSON to gis/ so the QGS can use it via relative path.
        # GeoJSON avoids DBF encoding issues with Thai text that can break the OGR provider
        # in QGIS when reading the .shp version.
        if boundary_gdf is not None and not boundary_gdf.empty:
            try:
                geojson_str = boundary_gdf.to_json()
                (gis_dir / "boundary.geojson").write_text(geojson_str, encoding='utf-8')
            except Exception as e:
                import sys; print(f"Error writing boundary GeoJSON: {e}", file=sys.stderr)

        # 5. QGIS project file — placed in gis/ so relative tile paths work
        try:
            qgs_path = gis_dir / f"{session_name}.qgs"
            self._generate_qgs_project(qgs_path, session_name, data_dir, results, bbox=bbox, gis_dir=gis_dir)
            results['qgs'] = True
        except Exception as e:
            import sys; print(f"Error generating .qgs: {e}", file=sys.stderr)

        # 6. ZIP both data/ and gis/ folders
        zip_path = session_dir / f"{session_name}_shp.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for f in data_dir.rglob('*'):
                if f.is_file():
                    zipf.write(f, f.relative_to(session_dir))
            for f in gis_dir.rglob('*'):
                if f.is_file():
                    zipf.write(f, f.relative_to(session_dir))

        return {
            "success": True,
            "parcel_count": parcel_count,
            "zip_path": str(zip_path),
            "layers": list(results.keys())
        }

    def _generate_grid_shapefile(self, utmmaps: list[str], out_path: Path):
        """Generate a grid attribute table (no geometry) for utmmap IDs found."""
        import sys
        # Create as a non-spatial table — geometry decoded from utmmap is not trivial
        # We save it as a CSV instead so it opens cleanly in QGIS as a table
        import pandas as pd
        df = pd.DataFrame({"mapsheet": utmmaps})
        csv_path = out_path.with_suffix('.csv')
        df.to_csv(csv_path, index=False, encoding='utf-8')
        print(f"Saved grid mapsheet list to {csv_path}", file=sys.stderr)

    def _generate_qgs_project(self, qgs_path: Path, session_name: str, data_dir: Path, layers: dict, bbox: list = None, session_dir: Path = None, gis_dir: Path = None):
        """Generate a QGIS 3.40-compatible project file (.qgs)."""
        import sys, math, uuid

        # WKT2 strings shared across the file
        wgs84_ensemble_wkt = (
            'GEOGCRS["WGS 84",ENSEMBLE["World Geodetic System 1984 ensemble",'
            'MEMBER["World Geodetic System 1984 (Transit)"],'
            'MEMBER["World Geodetic System 1984 (G730)"],'
            'MEMBER["World Geodetic System 1984 (G873)"],'
            'MEMBER["World Geodetic System 1984 (G1150)"],'
            'MEMBER["World Geodetic System 1984 (G1674)"],'
            'MEMBER["World Geodetic System 1984 (G1762)"],'
            'MEMBER["World Geodetic System 1984 (G2139)"],'
            'MEMBER["World Geodetic System 1984 (G2296)"],'
            'ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1]],'
            'ENSEMBLEACCURACY[2.0]],'
            'PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433]],'
            'CS[ellipsoidal,2],'
            'AXIS["geodetic latitude (Lat)",north,ORDER[1],ANGLEUNIT["degree",0.0174532925199433]],'
            'AXIS["geodetic longitude (Lon)",east,ORDER[2],ANGLEUNIT["degree",0.0174532925199433]],'
            'USAGE[SCOPE["Horizontal component of 3D system."],AREA["World."],BBOX[-90,-180,90,180]],'
            'ID["EPSG",4326]]'
        )
        merc_wkt = (
            'PROJCRS["WGS 84 / Pseudo-Mercator",BASEGEOGCRS["WGS 84",'
            'DATUM["World Geodetic System 1984",'
            'ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1]]],'
            'PRIMEM["Greenwich",0]],CONVERSION["Popular Visualisation Pseudo-Mercator",'
            'METHOD["Popular Visualisation Pseudo Mercator",ID["EPSG",1024]]],'
            'CS[Cartesian,2],AXIS["easting (X)",east,ORDER[1],LENGTHUNIT["metre",1]],'
            'AXIS["northing (Y)",north,ORDER[2],LENGTHUNIT["metre",1]],'
            'ID["EPSG",3857]]'
        )

        def make_wgs84_srs(parent):
            ref = ET.SubElement(parent, 'spatialrefsys', {'nativeFormat': 'Wkt'})
            ET.SubElement(ref, 'wkt').text = wgs84_ensemble_wkt
            ET.SubElement(ref, 'proj4').text = '+proj=longlat +datum=WGS84 +no_defs'
            ET.SubElement(ref, 'srsid').text = '3452'
            ET.SubElement(ref, 'srid').text = '4326'
            ET.SubElement(ref, 'authid').text = 'EPSG:4326'
            ET.SubElement(ref, 'description').text = 'WGS 84'
            ET.SubElement(ref, 'projectionacronym').text = 'longlat'
            ET.SubElement(ref, 'ellipsoidacronym').text = 'EPSG:7030'
            ET.SubElement(ref, 'geographicflag').text = 'true'

        def make_merc_srs(parent):
            ref = ET.SubElement(parent, 'spatialrefsys', {'nativeFormat': 'Wkt'})
            ET.SubElement(ref, 'wkt').text = merc_wkt
            ET.SubElement(ref, 'proj4').text = '+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 +units=m +nadgrids=@null +no_defs'
            ET.SubElement(ref, 'srsid').text = '3857'
            ET.SubElement(ref, 'srid').text = '3857'
            ET.SubElement(ref, 'authid').text = 'EPSG:3857'
            ET.SubElement(ref, 'description').text = 'WGS 84 / Pseudo-Mercator'
            ET.SubElement(ref, 'projectionacronym').text = 'merc'
            ET.SubElement(ref, 'ellipsoidacronym').text = 'EPSG:7030'
            ET.SubElement(ref, 'geographicflag').text = 'false'

        def lon_to_mercator_x(lon):
            return lon * 20037508.342789244 / 180.0

        def lat_to_mercator_y(lat):
            return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0

        # Canvas extent in EPSG:3857 meters (project CRS = EPSG:3857 to match tiles/OSM,
        # avoids CRS axis-order ambiguity that arises with EPSG:4326 as project CRS
        # when mixing projected EPSG:3857 raster layers with geographic EPSG:4326 vectors).
        canvas_bounds_merc = None
        if bbox and len(bbox) == 4:
            min_lon, min_lat, max_lon, max_lat = bbox
            pad_lon = (max_lon - min_lon) * 0.15
            pad_lat = (max_lat - min_lat) * 0.15
            canvas_bounds_merc = (
                lon_to_mercator_x(min_lon - pad_lon),
                lat_to_mercator_y(min_lat - pad_lat),
                lon_to_mercator_x(max_lon + pad_lon),
                lat_to_mercator_y(max_lat + pad_lat),
            )
        parcel_shp = data_dir / "parcel_dol.shp"
        boundary_shp = data_dir / "boundary.shp"
        boundary_geojson = (gis_dir / "boundary.geojson") if gis_dir else None

        # Assign UUID-format layer IDs matching QGIS 3.40 native format
        def make_layer_id(name):
            return f"{name}_{uuid.uuid4().hex[:8]}_{uuid.uuid4().hex[:4]}_{uuid.uuid4().hex[:4]}_{uuid.uuid4().hex[:4]}_{uuid.uuid4().hex[:12]}"

        # Vector layers: reproject to EPSG:3857 so they match the project CRS —
        # same approach used for DOL tiles (gdalwarp to EPSG:3857).
        # Without this, QGIS treats EPSG:4326 degree values as EPSG:3857 meters
        # and places features near (0°N, 0°E) = middle of ocean.
        def _reproject_to_3857(src_path: Path, out_path: Path):
            gdf = gpd.read_file(str(src_path))
            gdf.to_crs("EPSG:3857").to_file(str(out_path), driver='GeoJSON')

        vector_layers = []
        if parcel_shp.exists() and gis_dir:
            lid = make_layer_id("parcel_dol")
            p3857 = gis_dir / "parcel_dol_3857.geojson"
            try:
                _reproject_to_3857(parcel_shp, p3857)
                # Visible by default: the WFS vector is now BBOX-filtered to the
                # area (complete + crisp), so it is the primary parcel display —
                # unlike the lossy raster tiles it has no gaps or thin lines.
                vector_layers.append({"id": lid, "name": "Parcel (DOL)", "src": "./parcel_dol_3857.geojson", "style": "parcel_dol", "checked": "Qt::Checked"})
            except Exception as e:
                print(f"Parcel reproject failed: {e}", file=sys.stderr)
        if boundary_geojson and boundary_geojson.exists() and gis_dir:
            lid = make_layer_id("boundary")
            b3857 = gis_dir / "boundary_3857.geojson"
            try:
                _reproject_to_3857(boundary_geojson, b3857)
                vector_layers.append({"id": lid, "name": "Boundary", "src": "./boundary_3857.geojson", "style": "boundary"})
            except Exception as e:
                print(f"Boundary reproject failed: {e}", file=sys.stderr)
        elif boundary_shp.exists() and gis_dir:
            lid = make_layer_id("boundary")
            b3857 = gis_dir / "boundary_3857.geojson"
            try:
                _reproject_to_3857(boundary_shp, b3857)
                vector_layers.append({"id": lid, "name": "Boundary", "src": "./boundary_3857.geojson", "style": "boundary"})
            except Exception as e:
                print(f"Boundary reproject failed: {e}", file=sys.stderr)

        osm_id = make_layer_id("OpenStreetMap")
        osm_datasource = (
            "crs=EPSG:3857&format&type=xyz"
            "&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            "&zmax=19&zmin=0"
        )

        # ── Build QGS XML ──────────────────────────────────────────────────────
        home_dir = str((gis_dir if gis_dir else data_dir).resolve()).replace('\\', '/')
        qgs = ET.Element('qgis', {
            'projectname': session_name,
            'version': '3.40.15-Bratislava',
            'saveDateTime': '2026-01-01T00:00:00',
        })
        ET.SubElement(qgs, 'homePath', {'path': ''})
        ET.SubElement(qgs, 'title').text = session_name
        ET.SubElement(qgs, 'transaction', {'mode': 'Disabled'})
        ET.SubElement(qgs, 'projectFlags', {'set': ''})

        proj_crs_el = ET.SubElement(qgs, 'projectCrs')
        make_merc_srs(proj_crs_el)

        # Vertical CRS (empty, required by QGIS 3.40)
        vcrs_el = ET.SubElement(qgs, 'verticalCrs')
        vcrs_ref = ET.SubElement(vcrs_el, 'spatialrefsys', {'nativeFormat': 'Wkt'})
        for tag, val in [('wkt',''),('proj4',''),('srsid','0'),('srid','0'),('authid',''),
                         ('description',''),('projectionacronym',''),('ellipsoidacronym',''),('geographicflag','false')]:
            ET.SubElement(vcrs_ref, tag).text = val

        ET.SubElement(qgs, 'elevation-shading-renderer', {
            'hillshading-z-factor': '1', 'hillshading-is-multidirectional': '0',
            'combined-method': '0', 'hillshading-is-active': '0',
            'edl-distance-unit': '0', 'is-active': '0', 'light-altitude': '45',
            'edl-is-active': '1', 'light-azimuth': '315', 'edl-strength': '1000', 'edl-distance': '0.5'
        })

        # Layer tree group
        layer_tree_group = ET.SubElement(qgs, 'layer-tree-group')
        ltg_custom = ET.SubElement(layer_tree_group, 'customproperties')
        ET.SubElement(ltg_custom, 'Option')

        def add_tree_layer(parent, layer_info, provider, source=None):
            src = source if source else layer_info.get('src', '')
            el = ET.SubElement(parent, 'layer-tree-layer', {
                'name': layer_info['name'],
                'source': src,
                'legend_exp': '',
                'legend_split_behavior': '0',
                'providerKey': provider,
                'expanded': '1',
                'id': layer_info['id'],
                'checked': layer_info.get('checked', 'Qt::Checked'),
                'patch_size': '-1,-1',
            })
            cp = ET.SubElement(el, 'customproperties')
            ET.SubElement(cp, 'Option')
            return el

        for vl in vector_layers:
            add_tree_layer(layer_tree_group, vl, 'ogr')

        # Snapping settings (required for QGIS 3.40, minimal)
        snap = ET.SubElement(qgs, 'snapping-settings', {
            'tolerance': '12', 'enabled': '0', 'scaleDependencyMode': '0',
            'intersection-snapping': '0', 'maxScale': '0', 'unit': '1',
            'mode': '2', 'type': '1', 'self-snapping': '0', 'minScale': '0'
        })
        snap_ind = ET.SubElement(snap, 'individual-layer-settings')
        for vl in vector_layers:
            ET.SubElement(snap_ind, 'layer-setting', {
                'tolerance': '12', 'enabled': '0', 'units': '1',
                'maxScale': '0', 'id': vl['id'], 'type': '1', 'minScale': '0'
            })
        ET.SubElement(qgs, 'relations')

        # Map canvas — EPSG:3857 in meters
        mapcanvas = ET.SubElement(qgs, 'mapcanvas', {'name': 'theMapCanvas', 'annotationsVisible': '1'})
        ET.SubElement(mapcanvas, 'units').text = 'meters'
        if canvas_bounds_merc:
            ext_el = ET.SubElement(mapcanvas, 'extent')
            ET.SubElement(ext_el, 'xmin').text = str(canvas_bounds_merc[0])
            ET.SubElement(ext_el, 'ymin').text = str(canvas_bounds_merc[1])
            ET.SubElement(ext_el, 'xmax').text = str(canvas_bounds_merc[2])
            ET.SubElement(ext_el, 'ymax').text = str(canvas_bounds_merc[3])
        ET.SubElement(mapcanvas, 'rotation').text = '0'
        dest_srs = ET.SubElement(mapcanvas, 'destinationsrs')
        make_merc_srs(dest_srs)
        ET.SubElement(mapcanvas, 'rendermaptile').text = '0'

        ET.SubElement(qgs, 'projectModels')

        # Legend (required by QGIS 3.40 to properly display layers)
        legend_el = ET.SubElement(qgs, 'legend', {'updateDrawingOrder': 'true'})
        for vl in vector_layers:
            ll = ET.SubElement(legend_el, 'legendlayer', {
                'name': vl['name'], 'showFeatureCount': '0',
                'drawingOrder': '-1', 'open': 'true', 'checked': 'Qt::Checked'
            })
            fg = ET.SubElement(ll, 'filegroup', {'open': 'true', 'hidden': 'false'})
            ET.SubElement(fg, 'legendlayerfile', {'visible': '1', 'isInOverview': '0', 'layerid': vl['id']})

        ET.SubElement(qgs, 'mapViewDocks')

        # Project layers
        map_layers = ET.SubElement(qgs, 'projectlayers')

        layer_styles = {
            "parcel_dol": ("255,0,0,255", "0.5", "0,0,0,0"),
            "boundary":   ("0,0,255,255", "1.5", "65,105,225,60"),
        }

        def add_outline_renderer(parent, outline_color, width, fill_color="0,0,0,0"):
            renderer = ET.SubElement(parent, 'renderer-v2', {
                'type': 'singleSymbol', 'symbollevels': '0',
                'enableorderby': '0', 'forceraster': '0'
            })
            symbols = ET.SubElement(renderer, 'symbols')
            symbol = ET.SubElement(symbols, 'symbol', {
                'type': 'fill', 'name': '0', 'alpha': '1',
                'clip_to_extent': '1', 'force_rhr': '0'
            })
            layer_el = ET.SubElement(symbol, 'layer', {
                'pass': '0', 'class': 'SimpleFill', 'locked': '0', 'enabled': '1'
            })
            for k, v in [
                ('border_width_map_unit_scale', '3x:0,0,0,0,0,0'),
                ('color', fill_color), ('joinstyle', 'miter'),
                ('offset', '0,0'), ('offset_map_unit_scale', '3x:0,0,0,0,0,0'),
                ('offset_unit', 'MM'), ('outline_color', outline_color),
                ('outline_style', 'solid'), ('outline_width', width),
                ('outline_width_unit', 'MM'), ('style', 'solid'),
            ]:
                ET.SubElement(layer_el, 'prop', {'k': k, 'v': v})
            ET.SubElement(renderer, 'rotation')
            ET.SubElement(renderer, 'sizescale')

        # Vector maplayers with full QGIS 3.40 attributes
        for vl in vector_layers:
            # Get full bounds from shapefile/geojson metadata (fast, no feature scan)
            xmin, ymin, xmax, ymax = 0.0, 0.0, 0.0, 0.0
            try:
                if vl['src'].endswith('.geojson') or vl['src'].startswith('./'):
                    src_path = gis_dir / Path(vl['src']).name if gis_dir else Path(vl['src'])
                else:
                    src_path = Path(vl['src'])
                from pyogrio import read_info as _read_info
                _info = _read_info(str(src_path))
                b = _info['total_bounds']  # (minx, miny, maxx, maxy)
                xmin, ymin, xmax, ymax = float(b[0]), float(b[1]), float(b[2]), float(b[3])
            except Exception:
                if bbox and len(bbox) == 4:
                    xmin, ymin, xmax, ymax = bbox

            ml = ET.SubElement(map_layers, 'maplayer', {
                'labelsEnabled': '0',
                'hasScaleBasedVisibilityFlag': '0',
                'simplifyDrawingHints': '1',
                'simplifyMaxScale': '1',
                'styleCategories': 'AllStyleCategories',
                'maxScale': '0',
                'autoRefreshMode': 'Disabled',
                'legendPlaceholderImage': '',
                'simplifyLocal': '1',
                'simplifyDrawingTol': '1',
                'type': 'vector',
                'refreshOnNotifyMessage': '',
                'minScale': '100000000',
                'wkbType': 'MultiPolygon',
                'symbologyReferenceScale': '-1',
                'readOnly': '0',
                'autoRefreshTime': '0',
                'simplifyAlgorithm': '0',
                'refreshOnNotifyEnabled': '0',
                'geometry': 'Polygon',
            })
            ext_ml = ET.SubElement(ml, 'extent')
            ET.SubElement(ext_ml, 'xmin').text = str(xmin)
            ET.SubElement(ext_ml, 'ymin').text = str(ymin)
            ET.SubElement(ext_ml, 'xmax').text = str(xmax)
            ET.SubElement(ext_ml, 'ymax').text = str(ymax)
            wgs_ml = ET.SubElement(ml, 'wgs84extent')
            ET.SubElement(wgs_ml, 'xmin').text = str(xmin)
            ET.SubElement(wgs_ml, 'ymin').text = str(ymin)
            ET.SubElement(wgs_ml, 'xmax').text = str(xmax)
            ET.SubElement(wgs_ml, 'ymax').text = str(ymax)
            ET.SubElement(ml, 'id').text = vl['id']
            ET.SubElement(ml, 'datasource').text = vl['src']
            kw = ET.SubElement(ml, 'keywordList'); ET.SubElement(kw, 'value').text = ''
            ET.SubElement(ml, 'layername').text = vl['name']
            srs_el = ET.SubElement(ml, 'srs'); make_merc_srs(srs_el)
            ET.SubElement(ml, 'provider', {'encoding': 'UTF-8'}).text = 'ogr'
            ET.SubElement(ml, 'layerGeometryType').text = '2'
            outline_color, width, fill_color = layer_styles.get(vl['style'], ("128,128,128,255", "0.5", "0,0,0,0"))
            add_outline_renderer(ml, outline_color, width, fill_color)
            ET.SubElement(ml, 'blendMode').text = '0'
            ET.SubElement(ml, 'featureBlendMode').text = '0'
            ET.SubElement(ml, 'layerOpacity').text = '1'


        # OSM at the bottom of layer tree
        osm_tree = ET.SubElement(layer_tree_group, 'layer-tree-layer', {
            'name': 'OpenStreetMap',
            'id': osm_id,
            'checked': 'Qt::Checked',
            'source': osm_datasource,
            'providerKey': 'wms',
            'expanded': '0',
            'legend_exp': '',
            'legend_split_behavior': '0',
            'patch_size': '-1,-1',
        })
        osm_tree_cp = ET.SubElement(osm_tree, 'customproperties')
        ET.SubElement(osm_tree_cp, 'Option')


        # Add OSM maplayer
        osm_ml = ET.SubElement(map_layers, 'maplayer', {
            'type': 'raster',
            'autoRefreshMode': 'Disabled',
            'hasScaleBasedVisibilityFlag': '0',
            'styleCategories': 'AllStyleCategories',
        })
        ET.SubElement(osm_ml, 'id').text = osm_id
        ET.SubElement(osm_ml, 'layername').text = 'OpenStreetMap'
        ET.SubElement(osm_ml, 'datasource').text = osm_datasource
        ET.SubElement(osm_ml, 'provider', {'encoding': ''}).text = 'wms'
        osm_srs_el = ET.SubElement(osm_ml, 'srs')
        make_merc_srs(osm_srs_el)
        ET.SubElement(osm_ml, 'blendMode').text = '0'

        # Write file with DOCTYPE declaration (required by QGIS 3.40 to load layers)
        tree = ET.ElementTree(qgs)
        ET.indent(tree, space='  ')
        import io as _io
        buf = _io.BytesIO()
        tree.write(buf, encoding='utf-8', xml_declaration=True)
        xml_bytes = buf.getvalue()
        doctype = b"<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
        decl_end = xml_bytes.index(b'?>') + 2
        xml_bytes = xml_bytes[:decl_end] + b'\n' + doctype + xml_bytes[decl_end:]
        qgs_path.write_bytes(xml_bytes)
