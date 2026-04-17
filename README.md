# Landmap — Thai Land Department Map Fetcher

MCP server that fetches land parcel maps from กรมที่ดิน (DOL) and exports them as GIS-ready files for QGIS.

## What it does

1. **Query** Thai administrative boundaries (province / district / sub-district) from a local shapefile database
2. **Fetch** land map tiles from [landsmaps.dol.go.th](https://landsmaps.dol.go.th) using a headless Playwright browser that intercepts WMS tile requests and fetches WFS parcel vectors
3. **Export** to a ready-to-open QGIS project (`.qgs`) with OSM basemap, parcel polygons, and administrative boundary

## Output (`process_to_shapefiles`)

```
output/<session>/
├── data/
│   ├── parcel_dol.shp        Parcel polygons from DOL WFS (EPSG:4326)
│   ├── parcel_dol_3857.geojson  Same, reprojected to EPSG:3857 for QGIS
│   ├── boundary.shp          Administrative boundary polygon
│   ├── boundary_3857.geojson Same, reprojected to EPSG:3857 for QGIS
│   ├── grid_4000.csv         UTM map sheet IDs found during scan
│   └── <session>.qgs         QGIS 3.40 project (OSM basemap + all layers)
├── tiles_mosaic.vrt          GDAL VRT mosaic of all WMS tiles
├── images/                   Raw tile PNGs
└── <session>_shp.zip         Everything bundled
```

Open `<session>.qgs` in QGIS 3.36+ — the project opens centered on the correct area with:
- **DOL Tiles** — raster mosaic from WMS (EPSG:3857)
- **Boundary** — administrative boundary polygon (visible by default)
- **Parcel (DOL)** — WFS parcel polygons (hidden by default, enable as needed)

## Setup

### 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.10+ | |
| QGIS 3.36+ | Required for `gdalbuildvrt` to build the tile mosaic |
| Playwright Chromium | `playwright install chromium` |

### 2. Install Python dependencies

```bash
cd landmap-qgis/mcp-server
pip install -e .
playwright install chromium
```

### 3. Get the boundary shapefiles

The server needs Thai administrative boundary shapefiles to resolve province/district/sub-district names to bounding boxes. Place them in the `shapefiles/` directory at the repo root (or set `LANDMAP_SHAPEFILE_DIR`).

Expected structure:
```
shapefiles/
├── tha_admbnda_adm0_rtsd_20220121.shp   country boundary
├── tha_admbnda_adm1_rtsd_20220121.shp   provinces
├── tha_admbnda_adm2_rtsd_20220121.shp   districts
└── tha_admbnda_adm3_rtsd_20220121.shp   sub-districts
```

These are publicly available from [OCHA HDX](https://data.humdata.org/dataset/cod-ab-tha) (Thailand administrative boundaries).

### 4. Configure Claude Desktop

Edit `claude_desktop_config.json`:
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "landmap": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "C:\\path\\to\\landmap\\landmap-qgis\\mcp-server",
      "env": {
        "LANDMAP_SHAPEFILE_DIR": "C:\\path\\to\\landmap\\shapefiles",
        "LANDMAP_OUTPUT_DIR": "C:\\path\\to\\landmap\\output"
      }
    }
  }
}
```

> **Note:** If `LANDMAP_SHAPEFILE_DIR` and `LANDMAP_OUTPUT_DIR` are not set, the server auto-detects paths relative to its own location (works if you clone the repo and keep the default directory layout).

Restart Claude Desktop after editing the config.

### 5. Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LANDMAP_SHAPEFILE_DIR` | `<repo>/shapefiles` | Path to Thai admin boundary shapefiles |
| `LANDMAP_OUTPUT_DIR` | `<repo>/output` | Where session output is written |
| `LANDMAP_GDAL_BIN` | _(auto-detect)_ | Path to directory containing `gdalbuildvrt` — only needed if QGIS is not in `C:\Program Files\QGIS*` |

See `landmap-qgis/mcp-server/.env.example` for a template.

## Usage

Talk to Claude with the MCP server connected:

```
"ดึงแผนที่ที่ดินตำบลบางแคเหนือ เขตบางแค กรุงเทพ"
"Fetch land map for Bang Khae Nuea subdistrict, Bang Khae district, Bangkok"
```

Claude will call `fetch_landmap_tiles` then `process_to_shapefiles` and return the path to the `.qgs` project file.

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_provinces` | List all 77 Thai provinces |
| `list_districts` | List districts in a province |
| `list_subdistricts` | List sub-districts in a district |
| `get_boundary_bbox` | Get bounding box for any admin area |
| `search_location` | Search by Thai or English name |
| `fetch_landmap_tiles` | Fetch tiles from DOL for a given area |
| `process_to_shapefiles` | Export WFS parcels + tiles → QGIS project |
| `process_to_gis` | Export tiles → PNG + PGW + QLR (legacy) |
| `list_sessions` | List previously fetched sessions |

## Architecture

```
landmap-qgis/mcp-server/src/
  server.py           MCP server entry point + tool definitions
  tile_fetcher.py     Playwright automation — intercepts WMS tiles, fetches WFS GeoJSON
  gis_processor.py    Converts tiles/WFS data to GIS output (shapefiles, VRT, QGIS project)
  boundary_service.py Thai admin boundary queries from local shapefiles
shapefiles/           Thai admin boundary shapefiles (not included — download separately)
output/               Session output — gitignored
```

## Technical notes

- DOL site uses a Cesium 3D globe. The parcel layer activates when the map loads (intercepted via Playwright).
- A fresh incognito browser is used per session to bypass rate limits.
- Two parcel WMS/WFS zones: `V_PARCEL47` (UTM zone 47, western Thailand ≤ 102°E) and `V_PARCEL48` (UTM zone 48, eastern Thailand > 102°E). The fetcher tracks which zone each `utmmap` ID belongs to and uses the correct WFS endpoint automatically.
- Grid scan uses 2.5 km cells with 10% bbox padding and up to 3 passes per session.
- All layers in the output QGIS project use **EPSG:3857** (Web Mercator) so they align correctly.
