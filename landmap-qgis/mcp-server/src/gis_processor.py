"""
GIS Processor - Convert tiles to GIS-ready files

Converts captured tiles to:
- PNG images
- PGW world files (georeferencing)
- QLR layer definition (QGIS)
- ZIP bundle for easy download
"""

import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

try:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import shape, mapping
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False

# Import boundary service for actual geometry
from .boundary_service import BoundaryService

# Initialize boundary service
SHAPEFILE_DIR = os.environ.get(
    "LANDMAP_SHAPEFILE_DIR",
    r"C:\Users\intit\Desktop\landmap\landmap\shapefiles"
)
boundary_service = BoundaryService(SHAPEFILE_DIR)


class GISProcessor:
    """Process captured tiles into GIS-ready files."""

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

    async def process_session(self, session_name: str) -> dict:
        """
        Process a session's tiles into GIS files.

        Args:
            session_name: Name of the session to process

        Returns:
            Dict with success status, tile_count, and zip_path
        """
        session_dir = self.output_dir / session_name
        mission_file = session_dir / "mission.json"

        if not mission_file.exists():
            return {
                "success": False,
                "error": f"Session '{session_name}' not found"
            }

        try:
            with open(mission_file, 'r', encoding='utf-8') as f:
                mission_data = json.load(f)

            tiles = mission_data.get("tiles", [])
            if not tiles:
                return {
                    "success": False,
                    "error": "No tiles found in session"
                }

            # Create GIS output directory
            gis_dir = session_dir / "gis"
            gis_dir.mkdir(exist_ok=True)

            # Process each tile
            processed_tiles = []
            for i, tile in enumerate(tiles):
                tile_result = self._process_tile(session_dir, gis_dir, tile, i)
                if tile_result:
                    processed_tiles.append(tile_result)

            # Create boundary GeoJSON file (actual shape if location info available, else bbox)
            bbox = mission_data.get("bbox", [])
            location_info = mission_data.get("location")
            bbox_geojson_path = None

            if location_info or (bbox and len(bbox) == 4):
                bbox_geojson_path = gis_dir / "boundary.geojson"
                self._create_boundary_geojson(bbox_geojson_path, bbox, session_name, location_info)

            # Generate QLR file
            qlr_path = gis_dir / "landmap.qlr"
            self._generate_qlr(qlr_path, processed_tiles, session_name, bbox_geojson_path)

            # Create ZIP bundle
            zip_path = session_dir / f"{session_name}_gis.zip"
            self._create_zip(gis_dir, zip_path)

            return {
                "success": True,
                "tile_count": len(processed_tiles),
                "zip_path": str(zip_path)
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _process_tile(
        self,
        session_dir: Path,
        gis_dir: Path,
        tile: dict,
        index: int
    ) -> Optional[dict]:
        """Process a single tile - copy PNG and create PGW."""
        try:
            # Get source image path
            source_path = session_dir / tile["fileName"]
            if not source_path.exists():
                return None

            # Get tile info
            bbox = tile["bbox"]  # [minLon, minLat, maxLon, maxLat]
            width = tile.get("width", 256)
            height = tile.get("height", 256)

            # Copy PNG to GIS directory
            png_filename = f"tile_{index}.png"
            png_path = gis_dir / png_filename
            shutil.copy2(source_path, png_path)

            # Create PGW world file
            pgw_path = gis_dir / f"tile_{index}.pgw"
            self._create_world_file(pgw_path, bbox, width, height)

            return {
                "filename": png_filename,
                "bbox": bbox,
                "width": width,
                "height": height
            }

        except Exception as e:
            import sys; print(f"Error processing tile {index}: {e}", file=sys.stderr)
            return None

    def _create_world_file(
        self,
        pgw_path: Path,
        bbox: list[float],
        width: int,
        height: int
    ):
        """
        Create PGW world file for georeferencing.

        World file format:
        Line 1: pixel size in x direction (degrees/pixel)
        Line 2: rotation about y axis (usually 0)
        Line 3: rotation about x axis (usually 0)
        Line 4: pixel size in y direction (negative, degrees/pixel)
        Line 5: x coordinate of center of upper left pixel
        Line 6: y coordinate of center of upper left pixel
        """
        min_lon, min_lat, max_lon, max_lat = bbox

        # Calculate pixel sizes
        pixel_size_x = (max_lon - min_lon) / width
        pixel_size_y = (max_lat - min_lat) / height

        # Calculate center of upper-left pixel
        upper_left_x = min_lon + (pixel_size_x / 2)
        upper_left_y = max_lat - (pixel_size_y / 2)

        # Write world file
        with open(pgw_path, 'w') as f:
            f.write(f"{pixel_size_x:.12f}\n")     # pixel size X
            f.write("0.0\n")                       # rotation Y
            f.write("0.0\n")                       # rotation X
            f.write(f"-{pixel_size_y:.12f}\n")    # pixel size Y (negative)
            f.write(f"{upper_left_x:.12f}\n")     # upper-left X
            f.write(f"{upper_left_y:.12f}\n")     # upper-left Y

    def _create_boundary_geojson(
        self,
        geojson_path: Path,
        bbox: list[float],
        session_name: str,
        location_info: dict = None
    ):
        """
        Create a GeoJSON file with the boundary polygon.

        Uses actual geometry from shapefile if location_info is provided,
        otherwise falls back to simple bbox rectangle.
        """
        geojson = None

        # Try to get actual geometry from boundary service
        if location_info:
            try:
                province = location_info.get("province")
                district = location_info.get("district")
                subdistrict = location_info.get("subdistrict")

                if province:
                    gdf = boundary_service.get_geometry(province, district, subdistrict)
                    if gdf is not None and not gdf.empty:
                        # Convert to GeoJSON
                        geojson = json.loads(gdf.to_json())
                        # Update properties
                        for feature in geojson.get("features", []):
                            feature["properties"]["name"] = session_name
                            feature["properties"]["description"] = "Administrative boundary"
            except Exception as e:
                import sys; print(f"Error getting geometry: {e}", file=sys.stderr)

        # Fallback to bbox rectangle if no geometry found
        if geojson is None and bbox and len(bbox) == 4:
            min_lon, min_lat, max_lon, max_lat = bbox
            geojson = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "name": session_name,
                            "description": "Search area boundary (bbox)"
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[
                                [min_lon, min_lat],
                                [max_lon, min_lat],
                                [max_lon, max_lat],
                                [min_lon, max_lat],
                                [min_lon, min_lat]
                            ]]
                        }
                    }
                ]
            }

        if geojson:
            with open(geojson_path, 'w', encoding='utf-8') as f:
                json.dump(geojson, f, indent=2)

    def _generate_qlr(
        self,
        qlr_path: Path,
        tiles: list[dict],
        session_name: str,
        bbox_geojson_path: Optional[Path] = None
    ):
        """Generate QGIS Layer Definition file (.qlr)."""
        # Create root element
        qlr = ET.Element('qlr')

        # Create layer-tree-group
        layer_tree = ET.SubElement(qlr, 'layer-tree-group', {
            'expanded': '1',
            'checked': 'Qt::Checked',
            'name': session_name
        })

        # Add custom properties
        custom_props = ET.SubElement(layer_tree, 'customproperties')
        prop = ET.SubElement(custom_props, 'Option', {'type': 'Map'})

        # Create maplayers element
        maplayers = ET.SubElement(qlr, 'maplayers')

        # Add BBOX layer first (so it appears under tiles)
        if bbox_geojson_path and bbox_geojson_path.exists():
            bbox_layer_id = f"bbox_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # Add to layer tree
            ET.SubElement(layer_tree, 'layer-tree-layer', {
                'expanded': '0',
                'checked': 'Qt::Checked',
                'id': bbox_layer_id,
                'name': 'Boundary',
                'source': './boundary.geojson',
                'providerKey': 'ogr'
            })

            # Add to maplayers
            bbox_maplayer = ET.SubElement(maplayers, 'maplayer', {
                'minimumScale': '0',
                'maximumScale': '1e+08',
                'type': 'vector',
                'geometry': 'Polygon',
                'hasScaleBasedVisibilityFlag': '0',
                'styleCategories': 'AllStyleCategories'
            })

            ET.SubElement(bbox_maplayer, 'id').text = bbox_layer_id
            ET.SubElement(bbox_maplayer, 'layername').text = 'Boundary'
            ET.SubElement(bbox_maplayer, 'datasource').text = './boundary.geojson'
            ET.SubElement(bbox_maplayer, 'provider', {'encoding': 'UTF-8'}).text = 'ogr'

            # CRS for vector layer
            bbox_srs = ET.SubElement(bbox_maplayer, 'srs')
            bbox_spatial_ref = ET.SubElement(bbox_srs, 'spatialrefsys', {'nativeFormat': 'Wkt'})
            ET.SubElement(bbox_spatial_ref, 'wkt').text = 'GEOGCRS["WGS 84",DATUM["World Geodetic System 1984",ELLIPSOID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],CS[ellipsoidal,2],AXIS["geodetic latitude (Lat)",north],AXIS["geodetic longitude (Lon)",east],UNIT["degree",0.0174532925199433],ID["EPSG",4326]]'
            ET.SubElement(bbox_spatial_ref, 'proj4').text = '+proj=longlat +datum=WGS84 +no_defs'
            ET.SubElement(bbox_spatial_ref, 'authid').text = 'EPSG:4326'

            # Blue styling for BBOX - simple fill with blue stroke, transparent fill
            renderer = ET.SubElement(bbox_maplayer, 'renderer-v2', {
                'type': 'singleSymbol',
                'symbollevels': '0',
                'enableorderby': '0'
            })

            symbols = ET.SubElement(renderer, 'symbols')
            symbol = ET.SubElement(symbols, 'symbol', {
                'type': 'fill',
                'name': '0',
                'alpha': '1',
                'clip_to_extent': '1'
            })

            # Simple fill layer - blue stroke, light blue transparent fill
            layer = ET.SubElement(symbol, 'layer', {
                'pass': '0',
                'class': 'SimpleFill',
                'locked': '0'
            })

            # Blue fill with transparency (RGBA: 65, 105, 225, 50 = royal blue with ~20% opacity)
            ET.SubElement(layer, 'prop', {'k': 'color', 'v': '65,105,225,50'})
            # Blue stroke (RGBA: 0, 0, 255, 255 = solid blue)
            ET.SubElement(layer, 'prop', {'k': 'outline_color', 'v': '0,0,255,255'})
            ET.SubElement(layer, 'prop', {'k': 'outline_style', 'v': 'solid'})
            ET.SubElement(layer, 'prop', {'k': 'outline_width', 'v': '0.5'})
            ET.SubElement(layer, 'prop', {'k': 'style', 'v': 'solid'})

            ET.SubElement(bbox_maplayer, 'blendMode').text = '0'

        for i, tile in enumerate(tiles):
            layer_id = f"tile_{i}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # Add to layer tree
            ET.SubElement(layer_tree, 'layer-tree-layer', {
                'expanded': '0',
                'checked': 'Qt::Checked',
                'id': layer_id,
                'name': tile['filename'],
                'source': f"./{tile['filename']}",
                'providerKey': 'gdal'
            })

            # Add to maplayers
            maplayer = ET.SubElement(maplayers, 'maplayer', {
                'minimumScale': '0',
                'maximumScale': '1e+08',
                'type': 'raster',
                'hasScaleBasedVisibilityFlag': '0',
                'styleCategories': 'AllStyleCategories'
            })

            ET.SubElement(maplayer, 'id').text = layer_id
            ET.SubElement(maplayer, 'layername').text = tile['filename']

            # Data source
            datasource = ET.SubElement(maplayer, 'datasource')
            datasource.text = f"./{tile['filename']}"

            # Provider
            ET.SubElement(maplayer, 'provider').text = 'gdal'

            # CRS
            srs = ET.SubElement(maplayer, 'srs')
            spatial_ref = ET.SubElement(srs, 'spatialrefsys', {'nativeFormat': 'Wkt'})
            ET.SubElement(spatial_ref, 'wkt').text = 'GEOGCRS["WGS 84",DATUM["World Geodetic System 1984",ELLIPSOID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],CS[ellipsoidal,2],AXIS["geodetic latitude (Lat)",north],AXIS["geodetic longitude (Lon)",east],UNIT["degree",0.0174532925199433],ID["EPSG",4326]]'
            ET.SubElement(spatial_ref, 'proj4').text = '+proj=longlat +datum=WGS84 +no_defs'
            ET.SubElement(spatial_ref, 'srsid').text = '3452'
            ET.SubElement(spatial_ref, 'srid').text = '4326'
            ET.SubElement(spatial_ref, 'authid').text = 'EPSG:4326'
            ET.SubElement(spatial_ref, 'description').text = 'WGS 84'
            ET.SubElement(spatial_ref, 'projectionacronym').text = 'longlat'
            ET.SubElement(spatial_ref, 'ellipsoidacronym').text = 'EPSG:7030'
            ET.SubElement(spatial_ref, 'geographicflag').text = 'true'

            # Extent
            bbox = tile['bbox']
            extent = ET.SubElement(maplayer, 'extent')
            ET.SubElement(extent, 'xmin').text = str(bbox[0])
            ET.SubElement(extent, 'ymin').text = str(bbox[1])
            ET.SubElement(extent, 'xmax').text = str(bbox[2])
            ET.SubElement(extent, 'ymax').text = str(bbox[3])

            # Renderer (singleband color data)
            pipe = ET.SubElement(maplayer, 'pipe')
            renderer = ET.SubElement(pipe, 'rasterrenderer', {
                'type': 'singlebandcolordata',
                'opacity': '1',
                'alphaBand': '-1',
                'band': '1'
            })

            # Blending mode
            ET.SubElement(maplayer, 'blendMode').text = '0'

        # Write to file
        tree = ET.ElementTree(qlr)
        ET.indent(tree, space='  ')

        with open(qlr_path, 'wb') as f:
            tree.write(f, encoding='utf-8', xml_declaration=True)

    def _create_zip(self, gis_dir: Path, zip_path: Path):
        """Create ZIP bundle of all GIS files."""
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in gis_dir.iterdir():
                if file_path.is_file():
                    zipf.write(file_path, file_path.name)

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
        tiles = mission_data.get("tiles", [])

        data_dir = session_dir / "data"
        data_dir.mkdir(exist_ok=True)

        results = {}

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
                        import sys; print(f"Error reading {gf}: {e}", file=sys.stderr)

                if gdfs:
                    parcel_gdf = gpd.GeoDataFrame(
                        pd.concat(gdfs, ignore_index=True),
                        crs=gdfs[0].crs
                    )
                    # Drop duplicates by geometry
                    parcel_gdf = parcel_gdf.drop_duplicates(subset=['geometry'])
                    # Keep only polygon/multipolygon features (WFS sometimes includes points)
                    parcel_gdf = parcel_gdf[parcel_gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])]
                    # Reproject to WGS84 so QGIS doesn't need datum shift for Indian 1975
                    parcel_gdf = parcel_gdf.to_crs("EPSG:4326")
                    parcel_gdf.to_file(data_dir / "parcel_dol.shp", encoding='utf-8')
                    parcel_count = len(parcel_gdf)
                    results['parcel_count'] = parcel_count
                    import sys; print(f"Saved {parcel_count} parcel features", file=sys.stderr)

        # 2. Boundary shapefile from our shapefile database
        if location_info:
            try:
                province = location_info.get("province")
                district = location_info.get("district")
                subdistrict = location_info.get("subdistrict")
                boundary_gdf = boundary_service.get_geometry(province, district, subdistrict)
                if boundary_gdf is not None and not boundary_gdf.empty:
                    boundary_gdf.to_file(data_dir / "boundary.shp", encoding='utf-8')
                    results['boundary'] = True
            except Exception as e:
                import sys; print(f"Error saving boundary: {e}", file=sys.stderr)

        # 3. Grid shapefile from utmmap IDs found during scan
        if utmmaps and parcel_count > 0:
            try:
                self._generate_grid_shapefile(utmmaps, data_dir / "grid_4000.shp")
                results['grid'] = True
            except Exception as e:
                import sys; print(f"Error generating grid: {e}", file=sys.stderr)

        # 4. Ensure gis/ folder exists (same tiles that work in process_to_gis)
        gis_dir = session_dir / "gis"
        if not gis_dir.exists():
            await self.process_session(session_name)

        # 5. QGIS project file — placed in gis/ so relative tile paths work
        try:
            qgs_path = gis_dir / f"{session_name}.qgs"
            self._generate_qgs_project(qgs_path, session_name, data_dir, results, bbox=bbox, tiles=tiles, gis_dir=gis_dir)
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

    def _find_gdalbuildvrt(self) -> Optional[str]:
        """Find gdalbuildvrt.exe — bundled with QGIS or on PATH."""
        import shutil
        # Common QGIS install locations on Windows
        candidates = [
            r"C:\Program Files\QGIS 3.40.15\bin\gdalbuildvrt.exe",
            r"C:\Program Files\QGIS 3.38\bin\gdalbuildvrt.exe",
            r"C:\Program Files\QGIS 3.36\bin\gdalbuildvrt.exe",
            r"C:\OSGeo4W\bin\gdalbuildvrt.exe",
        ]
        import glob as _glob
        for pattern in [r"C:\Program Files\QGIS*\bin\gdalbuildvrt.exe"]:
            matches = _glob.glob(pattern)
            if matches:
                return matches[0]
        for c in candidates:
            if Path(c).exists():
                return c
        return shutil.which('gdalbuildvrt')

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

    def _generate_qgs_project(self, qgs_path: Path, session_name: str, data_dir: Path, layers: dict, bbox: list = None, tiles: list = None, session_dir: Path = None, gis_dir: Path = None):
        """Generate a QGIS project file (.qgs) with proper CRS and canvas extent."""
        import sys

        import math

        # Use EPSG:3857 (Web Mercator) as project CRS — same as OSM basemap,
        # same as what users have when they load the QLR into an existing session.
        # This ensures QGIS correctly reprojects WGS84 raster tiles for display.
        crs_wkt = (
            'GEOGCRS["WGS 84",DATUM["World Geodetic System 1984",'
            'ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1]]],'
            'PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433]],'
            'CS[ellipsoidal,2],'
            'AXIS["geodetic latitude (Lat)",north,ORDER[1],ANGLEUNIT["degree",0.0174532925199433]],'
            'AXIS["geodetic longitude (Lon)",east,ORDER[2],ANGLEUNIT["degree",0.0174532925199433]],'
            'ID["EPSG",4326]]'
        )
        crs_proj4 = '+proj=longlat +datum=WGS84 +no_defs'
        proj_crs_wkt = (
            'PROJCRS["WGS 84 / Pseudo-Mercator",BASEGEOGCRS["WGS 84",'
            'DATUM["World Geodetic System 1984",'
            'ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1]]],'
            'PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433]]],'
            'CONVERSION["Popular Visualisation Pseudo-Mercator",'
            'METHOD["Popular Visualisation Pseudo Mercator",ID["EPSG",1024]],'
            'PARAMETER["Longitude of natural origin",0,ANGLEUNIT["degree",0.0174532925199433],ID["EPSG",8802]],'
            'PARAMETER["False easting",0,LENGTHUNIT["metre",1],ID["EPSG",8806]],'
            'PARAMETER["False northing",0,LENGTHUNIT["metre",1],ID["EPSG",8807]]],'
            'CS[Cartesian,2],AXIS["easting (X)",east,ORDER[1],LENGTHUNIT["metre",1]],'
            'AXIS["northing (Y)",north,ORDER[2],LENGTHUNIT["metre",1]],'
            'USAGE[SCOPE["Web mapping and visualisation."],'
            'AREA["World between 85.06 S and 85.06 N."],BBOX[-85.06,-180,85.06,180]],'
            'ID["EPSG",3857]]'
        )

        def make_spatialrefsys(parent, use_proj_crs=True):
            """Write EPSG:3857 (project) or EPSG:4326 (tile/layer) SRS element."""
            ref = ET.SubElement(parent, 'spatialrefsys', {'nativeFormat': 'Wkt'})
            if use_proj_crs:
                ET.SubElement(ref, 'wkt').text = proj_crs_wkt
                ET.SubElement(ref, 'proj4').text = '+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 +units=m +nadgrids=@null +wktext +no_defs'
                ET.SubElement(ref, 'srsid').text = '3857'
                ET.SubElement(ref, 'srid').text = '3857'
                ET.SubElement(ref, 'authid').text = 'EPSG:3857'
                ET.SubElement(ref, 'description').text = 'WGS 84 / Pseudo-Mercator'
                ET.SubElement(ref, 'projectionacronym').text = 'merc'
                ET.SubElement(ref, 'ellipsoidacronym').text = 'EPSG:7030'
                ET.SubElement(ref, 'geographicflag').text = 'false'
            else:
                ET.SubElement(ref, 'wkt').text = crs_wkt
                ET.SubElement(ref, 'proj4').text = crs_proj4
                ET.SubElement(ref, 'srsid').text = '3452'
                ET.SubElement(ref, 'srid').text = '4326'
                ET.SubElement(ref, 'authid').text = 'EPSG:4326'
                ET.SubElement(ref, 'description').text = 'WGS 84'
                ET.SubElement(ref, 'projectionacronym').text = 'longlat'
                ET.SubElement(ref, 'ellipsoidacronym').text = 'EPSG:7030'
                ET.SubElement(ref, 'geographicflag').text = 'true'

        def lon_to_mercator_x(lon):
            return lon * 20037508.342789244 / 180.0

        def lat_to_mercator_y(lat):
            lat_rad = math.radians(lat)
            return math.log(math.tan(math.pi / 4 + lat_rad / 2)) * 6378137.0

        # Canvas extent in EPSG:3857 meters
        canvas_bounds = None
        if bbox and len(bbox) == 4:
            min_lon, min_lat, max_lon, max_lat = bbox
            pad_lon = (max_lon - min_lon) * 0.15
            pad_lat = (max_lat - min_lat) * 0.15
            canvas_bounds = (
                lon_to_mercator_x(min_lon - pad_lon),
                lat_to_mercator_y(min_lat - pad_lat),
                lon_to_mercator_x(max_lon + pad_lon),
                lat_to_mercator_y(max_lat + pad_lat),
            )

        parcel_shp = data_dir / "parcel_dol.shp"
        boundary_shp = data_dir / "boundary.shp"

        # Build .qgs XML
        home_dir = str((gis_dir if gis_dir else data_dir).resolve()).replace('\\', '/')
        qgs = ET.Element('qgis', {'projectname': session_name, 'version': '3.22.0'})
        ET.SubElement(qgs, 'homePath', {'path': home_dir})

        # Project CRS = EPSG:3857
        proj_crs_el = ET.SubElement(qgs, 'projectCrs')
        make_spatialrefsys(proj_crs_el, use_proj_crs=True)

        # Layer tree
        layer_tree_group = ET.SubElement(qgs, 'layer-tree-group')
        ET.SubElement(layer_tree_group, 'custom-order', {'enabled': '0'})

        # Map canvas in EPSG:3857
        mapcanvas = ET.SubElement(qgs, 'mapcanvas', {
            'name': 'theMapCanvas',
            'annotationsVisible': '1'
        })
        ET.SubElement(mapcanvas, 'units').text = 'meters'
        if canvas_bounds:
            ext_el = ET.SubElement(mapcanvas, 'extent')
            ET.SubElement(ext_el, 'xmin').text = str(canvas_bounds[0])
            ET.SubElement(ext_el, 'ymin').text = str(canvas_bounds[1])
            ET.SubElement(ext_el, 'xmax').text = str(canvas_bounds[2])
            ET.SubElement(ext_el, 'ymax').text = str(canvas_bounds[3])
        ET.SubElement(mapcanvas, 'rotation').text = '0'
        dest_srs = ET.SubElement(mapcanvas, 'destinationsrs')
        make_spatialrefsys(dest_srs, use_proj_crs=True)

        # Project layers
        map_layers = ET.SubElement(qgs, 'projectlayers')

        # Vector layers (parcel on top, boundary below)
        vector_layers = []
        if parcel_shp.exists():
            vector_layers.append(("parcel_dol", "Parcel (DOL)", "parcel_dol.shp"))
        if boundary_shp.exists():
            vector_layers.append(("boundary", "Boundary", "boundary.shp"))

        # OSM basemap (bottom of stack)
        osm_id = "OpenStreetMap_basemap"
        osm_datasource = (
            "crs=EPSG:3857&format&type=xyz"
            "&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            "&zmax=19&zmin=0"
        )
        osm_crs_wkt = (
            'PROJCRS["WGS 84 / Pseudo-Mercator",'
            'BASEGEOGCRS["WGS 84",DATUM["World Geodetic System 1984",'
            'ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1]]],'
            'PRIMEM["Greenwich",0],CS[ellipsoidal,2],'
            'AXIS["latitude",north],AXIS["longitude",east],'
            'ID["EPSG",4326]],'
            'CONVERSION["Popular Visualisation Pseudo-Mercator",'
            'METHOD["Popular Visualisation Pseudo Mercator",ID["EPSG",1024]]],'
            'CS[Cartesian,2],AXIS["(E)",east],AXIS["(N)",north],'
            'LENGTHUNIT["metre",1],ID["EPSG",3857]]'
        )

        # Add vector layers to tree (parcel, boundary)
        for layer_id, layer_name, filename in vector_layers:
            abs_shp = str((data_dir / filename).resolve()).replace('\\', '/')
            ET.SubElement(layer_tree_group, 'layer-tree-layer', {
                'name': layer_name,
                'id': layer_id,
                'checked': 'Qt::Checked',
                'source': abs_shp,
                'providerKey': 'ogr',
                'expanded': '1'
            })

        # Raster tiles: write per-tile VRTs → WGS84 mosaic → warp to EPSG:3857.
        # Filtering to the dominant pixel size avoids gdalbuildvrt picking the
        # coarsest resolution and shrinking all fine tiles to ~20px (invisible).
        mosaic_layer_id = None
        mosaic_merc_bbox = None  # extent in EPSG:3857 meters for QGS
        wgs84_wkt_vrt = ('GEOGCS["WGS 84",DATUM["WGS_1984",'
                         'SPHEROID["WGS 84",6378137,298.257223563]],'
                         'PRIMEM["Greenwich",0],'
                         'UNIT["degree",0.0174532925199433]]')
        if gis_dir and gis_dir.exists() and tiles and bbox and len(bbox) == 4:
            sess_min_lon, sess_min_lat, sess_max_lon, sess_max_lat = bbox
            # Small pad — only include tiles that fall inside (or just touch) the session bbox
            pad = 0.05
            candidate_tiles = [
                t for t in tiles
                if (t['bbox'][0] >= sess_min_lon - pad and
                    t['bbox'][2] <= sess_max_lon + pad and
                    t['bbox'][1] >= sess_min_lat - pad and
                    t['bbox'][3] <= sess_max_lat + pad)
            ]

            # Compute pixel sizes, filter out background/basemap outliers, then sort
            # coarsest-first so fine tiles composite on top (gdalbuildvrt last-source wins).
            from collections import Counter
            raw_px = [(t['bbox'][2]-t['bbox'][0]) / t.get('width', 256) for t in candidate_tiles]
            dominant_px = Counter(round(p, 10) for p in raw_px).most_common(1)[0][0] if raw_px else None

            # Keep only tiles within 8x of dominant (≤3 zoom levels away in either direction).
            # This excludes Cesium background tiles (zoom 0-12) that span huge areas and would
            # inflate the mosaic raster to billions of pixels when forced to the finest resolution.
            if dominant_px:
                valid_pairs = [(px, t) for px, t in zip(raw_px, candidate_tiles)
                               if dominant_px / 8 <= px <= dominant_px * 8]
            else:
                valid_pairs = list(zip(raw_px, candidate_tiles))

            # Sort descending by pixel size (coarsest first) so fine tiles overwrite gaps
            valid_pairs.sort(key=lambda x: x[0], reverse=True)
            px_sizes = [p[0] for p in valid_pairs]
            valid_tiles = [p[1] for p in valid_pairs]

            print(f"Mosaic: {len(valid_tiles)}/{len(candidate_tiles)} tiles after zoom filter "
                  f"(dominant={dominant_px:.8f} deg/px)", file=sys.stderr)

            vrt_paths = []
            skipped_transparent = 0
            for tile in valid_tiles:
                gis_png = Path(tile['fileName']).name
                png_path = gis_dir / gis_png
                # Skip fully-transparent tiles — they would win over useful coarse tiles
                # in gdalbuildvrt (last-source-wins) but contribute no visible content.
                if png_path.exists():
                    try:
                        from PIL import Image as _PIL
                        _img = _PIL.open(png_path)
                        if _img.mode == 'RGBA':
                            import struct as _struct
                            # Fast check: read only the alpha channel max
                            _r, _g, _b, _a = _img.split()
                            if max(_a.getdata()) == 0:
                                skipped_transparent += 1
                                continue
                    except Exception:
                        pass  # If PIL fails, include the tile anyway
                vrt_name = gis_png.replace('.png', '.vrt')
                vrt_path = gis_dir / vrt_name
                tb = tile['bbox']
                w = tile.get('width', 256)
                h = tile.get('height', 256)
                px = (tb[2] - tb[0]) / w
                py = (tb[3] - tb[1]) / h
                vrt_path.write_text(
                    f'<VRTDataset rasterXSize="{w}" rasterYSize="{h}">\n'
                    f'  <SRS>{wgs84_wkt_vrt}</SRS>\n'
                    f'  <GeoTransform>{tb[0]}, {px}, 0.0, {tb[3]}, 0.0, -{py}</GeoTransform>\n'
                    + ''.join(
                        f'  <VRTRasterBand dataType="Byte" band="{b}" subClass="VRTSourcedRasterBand">\n'
                        f'    <SimpleSource>\n'
                        f'      <SourceFilename relativeToVRT="1">{gis_png}</SourceFilename>\n'
                        f'      <SourceBand>{b}</SourceBand>\n'
                        f'      <SourceProperties RasterXSize="{w}" RasterYSize="{h}" DataType="Byte" BlockXSize="{w}" BlockYSize="1"/>\n'
                        f'      <SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>\n'
                        f'      <DstRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>\n'
                        f'    </SimpleSource>\n'
                        f'  </VRTRasterBand>\n'
                        for b in range(1, 5)
                    )
                    + '</VRTDataset>\n',
                    encoding='utf-8'
                )
                vrt_paths.append(str(vrt_path))

            print(f"VRT list: {len(vrt_paths)} tiles included, {skipped_transparent} fully-transparent skipped",
                  file=sys.stderr)
            import subprocess
            gdalbuildvrt_exe = self._find_gdalbuildvrt()
            if gdalbuildvrt_exe:
                qgis_bin = Path(gdalbuildvrt_exe).parent
                gdalwarp_exe = str(qgis_bin / 'gdalwarp.exe')
                gdalinfo_exe = str(qgis_bin / 'gdalinfo.exe')
                if not Path(gdalwarp_exe).exists():
                    gdalwarp_exe = None
            else:
                gdalwarp_exe = None
                gdalinfo_exe = None

            if gdalbuildvrt_exe and gdalwarp_exe and vrt_paths:
                filelist_path = gis_dir / '_tile_filelist.txt'
                filelist_path.write_text('\n'.join(vrt_paths), encoding='utf-8')

                # Step 1: WGS84 mosaic — force output to dominant pixel size so the mosaic
                # stays at a manageable resolution. Coarser gap-fill tiles get upsampled
                # at most 8x; finer tiles contribute at dominant resolution (slight downsample).
                wgs84_mosaic = gis_dir / 'tiles_mosaic_wgs84.vrt'
                vrt_cmd = [gdalbuildvrt_exe]
                if dominant_px:
                    vrt_cmd += ['-tr', str(dominant_px), str(dominant_px)]
                vrt_cmd += ['-input_file_list', str(filelist_path), str(wgs84_mosaic)]
                subprocess.run(vrt_cmd, capture_output=True, text=True)
                if not wgs84_mosaic.exists():
                    print("gdalbuildvrt failed — tiles omitted from QGS", file=sys.stderr)
                    gdalwarp_exe = None  # skip warp step

            if gdalbuildvrt_exe and gdalwarp_exe and vrt_paths:
                # Step 2: Warp to EPSG:3857 (VRT = lazy, no pixel processing now)
                mosaic_path = gis_dir / 'tiles_mosaic.vrt'
                if mosaic_path.exists():
                    mosaic_path.unlink()  # remove stale file so gdalwarp can write fresh
                r = subprocess.run(
                    [gdalwarp_exe, '-t_srs', 'EPSG:3857', '-r', 'bilinear',
                     '-of', 'VRT', str(wgs84_mosaic), str(mosaic_path)],
                    capture_output=True, text=True
                )

                if mosaic_path.exists():
                    # Read extent from the EPSG:3857 mosaic
                    ri = subprocess.run(
                        [gdalinfo_exe, str(mosaic_path)],
                        capture_output=True, text=True
                    )
                    # Parse corners from gdalinfo output
                    import re
                    m_ul = re.search(r'Upper Left\s+\(\s*([\d.]+),\s*([\d.]+)\)', ri.stdout)
                    m_lr = re.search(r'Lower Right\s+\(\s*([\d.]+),\s*([\d.]+)\)', ri.stdout)
                    if m_ul and m_lr:
                        mosaic_merc_bbox = [
                            float(m_ul.group(1)), float(m_lr.group(2)),
                            float(m_lr.group(1)), float(m_ul.group(2))
                        ]
                    else:
                        # Fallback: convert session bbox to EPSG:3857
                        mosaic_merc_bbox = [
                            lon_to_mercator_x(sess_min_lon), lat_to_mercator_y(sess_min_lat),
                            lon_to_mercator_x(sess_max_lon), lat_to_mercator_y(sess_max_lat),
                        ]
                    print(f"Mosaic VRT (EPSG:3857) created, extent: {mosaic_merc_bbox}", file=sys.stderr)
                    mosaic_layer_id = "dol_tiles_mosaic"
                    ET.SubElement(layer_tree_group, 'layer-tree-layer', {
                        'name': 'DOL Tiles',
                        'id': mosaic_layer_id,
                        'checked': 'Qt::Checked',
                        'source': './tiles_mosaic.vrt',
                        'providerKey': 'gdal',
                        'expanded': '0'
                    })
                else:
                    print(f"gdalwarp failed: {r.stderr}", file=sys.stderr)
            else:
                print("GDAL tools not found — tiles omitted from QGS", file=sys.stderr)

        # OSM at the bottom of layer tree
        ET.SubElement(layer_tree_group, 'layer-tree-layer', {
            'name': 'OpenStreetMap',
            'id': osm_id,
            'checked': 'Qt::Checked',
            'source': osm_datasource,
            'providerKey': 'wms'
        })

        def make_layer_srs(parent, shp_path: Path):
            """Read CRS from shapefile and write <srs> element."""
            try:
                gdf = gpd.read_file(shp_path, rows=1)
                crs = gdf.crs
                if crs:
                    layer_srs = ET.SubElement(parent, 'srs')
                    ref = ET.SubElement(layer_srs, 'spatialrefsys', {'nativeFormat': 'Wkt'})
                    ET.SubElement(ref, 'wkt').text = crs.to_wkt()
                    ET.SubElement(ref, 'proj4').text = crs.to_proj4()
                    ET.SubElement(ref, 'authid').text = crs.to_authority(min_confidence=20)[0] + ':' + crs.to_authority(min_confidence=20)[1] if crs.to_authority(min_confidence=20) else str(crs)
                    ET.SubElement(ref, 'description').text = crs.name
                    ET.SubElement(ref, 'geographicflag').text = 'true' if crs.is_geographic else 'false'
            except Exception as e:
                print(f"Warning: could not read CRS from {shp_path}: {e}", file=sys.stderr)

        def add_outline_renderer(parent, outline_color, width, fill_color="0,0,0,0"):
            """Add a SimpleFill renderer with a solid style (fill + outline)."""
            renderer = ET.SubElement(parent, 'renderer-v2', {
                'type': 'singleSymbol',
                'symbollevels': '0',
                'enableorderby': '0',
                'forceraster': '0'
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
                ('color', fill_color),
                ('joinstyle', 'miter'),
                ('offset', '0,0'),
                ('offset_map_unit_scale', '3x:0,0,0,0,0,0'),
                ('offset_unit', 'MM'),
                ('outline_color', outline_color),
                ('outline_style', 'solid'),
                ('outline_width', width),
                ('outline_width_unit', 'MM'),
                ('style', 'solid'),
            ]:
                ET.SubElement(layer_el, 'prop', {'k': k, 'v': v})
            ET.SubElement(renderer, 'rotation')
            ET.SubElement(renderer, 'sizescale')

        # Styling: parcel = red outline (transparent fill), boundary = blue semi-transparent fill + outline
        layer_styles = {
            "parcel_dol": ("255,0,0,255", "0.5", "0,0,0,0"),
            "boundary":   ("0,0,255,255", "1.0", "65,105,225,30"),  # royal blue, 12% opacity fill
        }

        # Add vector maplayers
        for layer_id, layer_name, filename in vector_layers:
            shp_path = data_dir / filename
            ml = ET.SubElement(map_layers, 'maplayer', {
                'type': 'vector',
                'autoRefreshEnabled': '0',
                'geometry': 'Polygon',
                'hasScaleBasedVisibilityFlag': '0',
                'styleCategories': 'AllStyleCategories'
            })
            ET.SubElement(ml, 'id').text = layer_id
            # Use absolute path so QGIS finds the file regardless of working dir
            ET.SubElement(ml, 'datasource').text = str(shp_path.resolve()).replace('\\', '/')
            kw = ET.SubElement(ml, 'keywordList')
            ET.SubElement(kw, 'value')
            ET.SubElement(ml, 'layername').text = layer_name
            # Include actual CRS from shapefile so QGIS reprojects correctly
            make_layer_srs(ml, shp_path)
            ET.SubElement(ml, 'provider', {'encoding': 'UTF-8'}).text = 'ogr'
            outline_color, width, fill_color = layer_styles.get(layer_id, ("128,128,128,255", "0.5", "0,0,0,0"))
            add_outline_renderer(ml, outline_color, width, fill_color)
            ET.SubElement(ml, 'blendMode').text = '0'
            ET.SubElement(ml, 'featureBlendMode').text = '0'
            ET.SubElement(ml, 'layerOpacity').text = '1'

        # Add single mosaic tile layer — EPSG:3857, extent in meters = same as project
        if mosaic_layer_id and mosaic_merc_bbox:
            tml = ET.SubElement(map_layers, 'maplayer', {
                'minimumScale': '0', 'maximumScale': '1e+08',
                'type': 'raster', 'hasScaleBasedVisibilityFlag': '0',
                'styleCategories': 'AllStyleCategories'
            })
            ET.SubElement(tml, 'id').text = mosaic_layer_id
            ET.SubElement(tml, 'layername').text = 'DOL Tiles'
            ET.SubElement(tml, 'datasource').text = './tiles_mosaic.vrt'
            ET.SubElement(tml, 'provider').text = 'gdal'
            # Layer SRS = EPSG:3857 (matches file CRS after gdalwarp + project CRS)
            t_srs = ET.SubElement(tml, 'srs')
            make_spatialrefsys(t_srs, use_proj_crs=True)
            t_ext = ET.SubElement(tml, 'extent')
            ET.SubElement(t_ext, 'xmin').text = str(mosaic_merc_bbox[0])
            ET.SubElement(t_ext, 'ymin').text = str(mosaic_merc_bbox[1])
            ET.SubElement(t_ext, 'xmax').text = str(mosaic_merc_bbox[2])
            ET.SubElement(t_ext, 'ymax').text = str(mosaic_merc_bbox[3])
            t_pipe = ET.SubElement(tml, 'pipe')
            ET.SubElement(t_pipe, 'rasterrenderer', {
                'type': 'multibandcolor', 'opacity': '1',
                'redBand': '1', 'greenBand': '2', 'blueBand': '3', 'alphaBand': '4'
            })
            ET.SubElement(tml, 'blendMode').text = '0'

        # Add OSM maplayer
        osm_ml = ET.SubElement(map_layers, 'maplayer', {
            'type': 'raster',
            'autoRefreshEnabled': '0',
            'hasScaleBasedVisibilityFlag': '0'
        })
        ET.SubElement(osm_ml, 'id').text = osm_id
        ET.SubElement(osm_ml, 'layername').text = 'OpenStreetMap'
        ET.SubElement(osm_ml, 'datasource').text = osm_datasource
        ET.SubElement(osm_ml, 'provider', {'encoding': ''}).text = 'wms'
        osm_srs_el = ET.SubElement(osm_ml, 'srs')
        osm_ref = ET.SubElement(osm_srs_el, 'spatialrefsys', {'nativeFormat': 'Wkt'})
        ET.SubElement(osm_ref, 'wkt').text = osm_crs_wkt
        ET.SubElement(osm_ref, 'proj4').text = '+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 +units=m +nadgrids=@null +wktext +no_defs'
        ET.SubElement(osm_ref, 'authid').text = 'EPSG:3857'
        ET.SubElement(osm_ref, 'description').text = 'WGS 84 / Pseudo-Mercator'
        ET.SubElement(osm_ref, 'projectionacronym').text = 'merc'
        ET.SubElement(osm_ref, 'geographicflag').text = 'false'

        tree = ET.ElementTree(qgs)
        ET.indent(tree, space='  ')
        with open(qgs_path, 'wb') as f:
            tree.write(f, encoding='utf-8', xml_declaration=True)
