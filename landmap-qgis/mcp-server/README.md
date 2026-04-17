# Landmap MCP Server

MCP Server สำหรับดึงข้อมูลแผนที่ที่ดินจากกรมที่ดิน (landsmaps.dol.go.th) ผ่าน Claude Desktop

## Requirements

- Python 3.10+
- QGIS 3.36+ (ต้องการ `gdalbuildvrt` สำหรับสร้าง tile mosaic)
- Playwright Chromium

## Installation

```bash
cd landmap-qgis/mcp-server
pip install -e .
playwright install chromium
```

## Configuration

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LANDMAP_SHAPEFILE_DIR` | `<repo>/shapefiles` | Path ไปยัง Thai admin boundary shapefiles |
| `LANDMAP_OUTPUT_DIR` | `<repo>/output` | Path สำหรับบันทึก session output |
| `LANDMAP_GDAL_BIN` | _(auto-detect)_ | Path ไปยัง directory ที่มี `gdalbuildvrt` — ตั้งเมื่อ QGIS ไม่ได้ติดตั้งใน `C:\Program Files\QGIS*` |

ดู `.env.example` สำหรับ template

### Claude Desktop (`claude_desktop_config.json`)

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`  
**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

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

> ถ้า `cwd` ตั้งค่าเป็น path ที่ถูกต้องและ `shapefiles/` กับ `output/` อยู่ใน repo root ตาม default layout ไม่จำเป็นต้องตั้ง env vars เลย

Restart Claude Desktop หลังแก้ไข config

## Available Tools

| Tool | Description |
|------|-------------|
| `list_provinces` | แสดงจังหวัดทั้งหมด 77 จังหวัด |
| `list_districts` | แสดงอำเภอ/เขต ในจังหวัดที่ระบุ |
| `list_subdistricts` | แสดงตำบล/แขวง ในอำเภอที่ระบุ |
| `get_boundary_bbox` | หาขอบเขตพิกัด (BBOX) ของตำบล/อำเภอ/จังหวัด |
| `search_location` | ค้นหาพื้นที่จากชื่อไทยหรืออังกฤษ |
| `fetch_landmap_tiles` | ดึง tiles จากกรมที่ดิน |
| `process_to_shapefiles` | แปลงข้อมูลเป็น shapefile + QGIS project (.qgs) |
| `process_to_gis` | แปลง tiles เป็น PNG + PGW + QLR (legacy) |
| `list_sessions` | แสดงรายการ sessions ที่ดึงไว้แล้ว |

## Usage Example

```
User: ดึงแผนที่ที่ดินตำบลบางแคเหนือ เขตบางแค กรุงเทพ

Claude: [fetch_landmap_tiles → process_to_shapefiles]

Output: output/bangkhaenuea/data/bangkhaenuea.qgs
```

เปิดไฟล์ `.qgs` ใน QGIS 3.36+ ได้เลย — project เปิดตรงพื้นที่ที่ถูกต้องพร้อม:
- **DOL Tiles** — raster mosaic จาก WMS
- **Boundary** — ขอบเขตตำบล/อำเภอ
- **Parcel (DOL)** — แปลงที่ดินจาก WFS (ซ่อนไว้ by default, เปิดได้ใน Layers panel)

## Output Structure

```
output/<session>/
├── data/
│   ├── parcel_dol.shp            แปลงที่ดิน (EPSG:4326)
│   ├── parcel_dol_3857.geojson   แปลงที่ดิน (EPSG:3857 สำหรับ QGIS project)
│   ├── boundary.shp              ขอบเขตพื้นที่ (EPSG:4326)
│   ├── boundary_3857.geojson     ขอบเขตพื้นที่ (EPSG:3857 สำหรับ QGIS project)
│   ├── grid_4000.csv             รายการ UTM map sheet IDs
│   └── <session>.qgs             QGIS project file
├── tiles_mosaic.vrt              GDAL VRT mosaic
├── images/                       Raw tile PNGs
└── <session>_shp.zip             ไฟล์ทั้งหมดใน ZIP
```

## Troubleshooting

**MCP Server ไม่เชื่อมต่อ**
- ตรวจสอบ `cwd` ใน `claude_desktop_config.json` ว่าชี้ไปยัง `landmap-qgis/mcp-server` ที่ถูกต้อง
- ตรวจสอบว่าติดตั้ง dependencies ครบ (`pip install -e .`)
- Restart Claude Desktop

**ดึง tiles ไม่ได้ / ได้ tiles น้อยมาก**
- ตรวจสอบการเชื่อมต่อ internet
- เว็บกรมที่ดินอาจ rate limit — ลองใหม่อีกครั้ง (server ใช้ incognito browser ใหม่ทุก session)

**QGIS project เปิดแล้ว layers ไม่แสดง**
- ต้องใช้ QGIS 3.36 ขึ้นไป
- ตรวจสอบว่า `tiles_mosaic.vrt` อยู่ใน session directory (ถ้าไม่มี gdalbuildvrt ทำงานล้มเหลว)
- ตั้ง `LANDMAP_GDAL_BIN` ถ้า QGIS ไม่ได้ติดตั้งใน default location

**ไม่พบจังหวัด/อำเภอ/ตำบล**
- ตรวจสอบว่า `LANDMAP_SHAPEFILE_DIR` ชี้ไปยัง directory ที่มี `.shp` files ถูกต้อง
- ลองใช้ `search_location` เพื่อค้นหาชื่อที่ถูกต้อง
