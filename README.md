# Landmap — Thai Land Department Map Fetcher

MCP server that fetches land parcel maps from กรมที่ดิน (DOL) and exports them as GIS-ready files for QGIS.

## What it does

1. **Query** Thai administrative boundaries (province / district / sub-district)
2. **Fetch** land map tiles from [landsmaps.dol.go.th](https://landsmaps.dol.go.th) using a headless Playwright browser that intercepts WMS tile requests and fetches WFS parcel vectors
3. **Export** to QGIS-ready files

## Output formats

| Tool | Output | Status |
|------|--------|--------|
| `process_to_gis` | PNG tiles + PGW world files + `.qlr` layer definition | ✅ Working — drag `.qlr` into QGIS on top of an OSM project to see red parcel lines + blue boundary |
| `process_to_shapefiles` | Parcel `.shp` + boundary `.shp` + `.qgs` project | ⚠️ Partial — shapefile vectors (parcel + boundary) load correctly, but the raster tile layer in the `.qgs` still appears in the middle of the ocean instead of Thailand (CRS/axis-order issue under investigation) |

> **Workaround for full output:** use `process_to_gis` for the raster tiles (QLR), and `process_to_shapefiles` for the vector parcel shapefile. Open them both in the same QGIS session.

## Setup

### Prerequisites

- Python 3.11+
- [Playwright](https://playwright.dev/python/) — `pip install playwright && playwright install chromium`
- `geopandas`, `pandas`, `shapely` — `pip install geopandas pandas shapely`
- MCP SDK — `pip install mcp`

### Configuration

Set these environment variables (or edit the defaults in `server.py`):

```
LANDMAP_SHAPEFILE_DIR   path to the provincial boundary shapefiles (default: ./shapefiles)
LANDMAP_OUTPUT_DIR      path where session output is written  (default: ./output)
```

### Running as MCP server

```bash
cd landmap-qgis/mcp-server
python -m src.server
```

Add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "landmap": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "C:/path/to/landmap-qgis/mcp-server"
    }
  }
}
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_provinces` | List all 77 Thai provinces |
| `list_districts` | List districts in a province |
| `list_subdistricts` | List sub-districts in a district |
| `get_boundary_bbox` | Get bounding box for a province/district/sub-district |
| `search_location` | Search by Thai or English name |
| `fetch_landmap_tiles` | Fetch tiles from DOL (specify `province`/`district`/`subdistrict` or raw `bbox`) |
| `process_to_gis` | Convert tiles → PNG + PGW + QLR (raster, works) |
| `process_to_shapefiles` | Convert WFS data → SHP + QGS project (vectors work, raster tiles misplaced) |

## Architecture

```
src/
  server.py           MCP server entry point
  tile_fetcher.py     Playwright automation — intercepts WMS tiles, fetches WFS GeoJSON
  gis_processor.py    Converts tiles/WFS data to GIS output files
  boundary_service.py Thai admin boundary queries from local shapefiles
shapefiles/           Provincial boundary shapefiles (Thai admin divisions)
output/               Session output — gitignored
```

## Technical notes

- DOL site uses a Cesium 3D globe. Parcel layer is activated by double-clicking the canvas (triggers a SweetAlert2 popup).
- Fresh incognito browser is used per session to bypass rate limits.
- Two parcel WMS/WFS zones: `V_PARCEL47` (UTM zone 47, western Thailand ≤ 102°E) and `V_PARCEL48` (UTM zone 48, eastern Thailand > 102°E). The fetcher tracks which zone each `utmmap` ID belongs to and uses the correct WFS endpoint.
- Grid scan uses 2.5 km cells with 10% bbox padding and up to 3 passes per session (max 2000 tiles).

## Known issues

- **`process_to_shapefiles` — raster tiles appear in ocean in QGIS**: The `.qgs` project file declares tile layers with `OGC:CRS84` SRS and GDAL PAM `aux.xml` for explicit georeferencing, but QGIS still misplaces them. Root cause is axis-order ambiguity between how GDAL reports WGS84 coordinates and how QGIS interprets them in a `EPSG:3857` project. Investigation ongoing. The `.qlr` output from `process_to_gis` does not have this problem.
- **WFS occasionally returns XML error instead of GeoJSON**: Transient server issue on the DOL side. Re-running `fetch_landmap_tiles` usually resolves it.
