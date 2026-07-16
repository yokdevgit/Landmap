# Landmap MCP Server

MCP server สำหรับดึง **รูปแปลงที่ดิน (land parcels)** จากกรมที่ดิน (landsmaps.dol.go.th)
ผ่าน Claude Desktop แล้วสร้างไฟล์ **QGIS project (.qgs) + Shapefile** ให้พร้อมใช้งาน

ดึงข้อมูลแบบ **click-free** ผ่าน WFS ล้วน — ไม่ต้องดับเบิลคลิกบน 3D viewer
(ซึ่งเป็นตัวที่โดนกรมที่ดิน rate-limit) จึงเร็วและเสถียรกว่าเดิมมาก

## Requirements

- Python 3.10+
- Playwright Chromium (`playwright install chromium`)
- QGIS 3.36+ (สำหรับ *เปิด* ไฟล์ `.qgs` — ไม่ต้องใช้ตอนดึงข้อมูลแล้ว)

## Installation

```bash
cd landmap-qgis/mcp-server
pip install -e .
playwright install chromium
```

## Configuration

ทุกอย่างมี default เป็น path ภายใน repo — **clone แล้วใช้ได้เลยโดยไม่ต้องตั้งค่า**
ตั้ง env var (หรือไฟล์ `.env` ที่ repo root — โหลดอัตโนมัติ) เฉพาะเมื่อเก็บข้อมูลไว้ที่อื่น:

| Variable | Default | Description |
|----------|---------|-------------|
| `LANDMAP_SHAPEFILE_DIR` | `<repo>/shapefiles` | Thai admin-boundary shapefiles |
| `LANDMAP_OUTPUT_DIR` | `<repo>/output` | ที่บันทึก session output |

ดู `.env.example` เป็น template (คัดลอกเป็น `.env`)

### Claude Desktop (`claude_desktop_config.json`)

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json` ·
**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "landmap": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "C:\\path\\to\\landmap\\landmap-qgis\\mcp-server"
    }
  }
}
```

> ถ้า `cwd` ถูกต้องและ `shapefiles/` + `output/` อยู่ใน repo ตาม default layout
> ไม่ต้องตั้ง env vars เลย · restart Claude Desktop หลังแก้ config

## Usage — พิมพ์ธรรมดาได้เลย

ไม่ต้องระบุชื่อ tool — บอกพื้นที่ที่ต้องการก็พอ Claude จะเลือก `get_land_parcels` เอง:

```
User: ขอรูปแปลงที่ดินตำบลสีลม เขตบางรัก กรุงเทพ

Claude: [get_land_parcels] → เสร็จแล้ว! (silom)
        จำนวนแปลง: 9,087 · ไฟล์ ZIP: output/silom/silom_shp.zip
```

`get_land_parcels` ทำครบในขั้นตอนเดียว: หาขอบเขตพื้นที่ → ดึงแปลงที่ดินจาก WFS →
reproject เป็น WGS84 → สร้าง shapefile + `.qgs` + zip

เปิดไฟล์ `.qgs` ใน QGIS 3.36+ ได้ทันที (project เปิดตรงพื้นที่พร้อม 3 เลเยอร์):
- **Parcel (DOL)** — แปลงที่ดินจาก WFS (vector ครบ คมชัด)
- **Boundary** — ขอบเขตตำบล/อำเภอ
- **OpenStreetMap** — basemap

## Available Tools

| Tool | Description |
|------|-------------|
| **`get_land_parcels`** | **One-shot**: พื้นที่ → แปลงที่ดิน + shapefile + `.qgs` + zip (ใช้ตัวนี้เป็นหลัก) |
| `list_provinces` | แสดงจังหวัดทั้งหมด 77 จังหวัด |
| `list_districts` | แสดงอำเภอ/เขต ในจังหวัดที่ระบุ |
| `list_subdistricts` | แสดงตำบล/แขวง ในอำเภอที่ระบุ |
| `get_boundary_bbox` | หาขอบเขตพิกัด (BBOX) ของพื้นที่ |
| `search_location` | ค้นหาพื้นที่จากชื่อไทย/อังกฤษ |
| `fetch_parcels_wfs` | *(ขั้นตอนย่อย)* ดึงแปลงที่ดินอย่างเดียว (ไม่สร้าง `.qgs`) |
| `process_to_shapefiles` | *(ขั้นตอนย่อย)* แปลง session ที่ดึงไว้เป็น shapefile + `.qgs` |
| `list_sessions` | แสดงรายการ sessions ที่ดึงไว้แล้ว |

## Output Structure

```
output/<session>/
├── mission.json                 metadata (bbox, map sheets, location)
├── features/
│   └── utmmap_<id>.geojson       raw WFS parcels ต่อ map sheet
├── data/
│   ├── parcel_dol.shp            แปลงที่ดิน (EPSG:4326)
│   ├── boundary.shp              ขอบเขตพื้นที่ (EPSG:4326)
│   └── grid_4000.csv             รายการ UTM map-sheet IDs
├── gis/
│   ├── parcel_dol_3857.geojson   แปลงที่ดิน (EPSG:3857 สำหรับ QGIS)
│   ├── boundary_3857.geojson     ขอบเขต (EPSG:3857)
│   └── <session>.qgs             QGIS project file
└── <session>_shp.zip            ทุกไฟล์ใน ZIP
```

## How it works (click-free WFS)

1. เปิด DOL แค่พอให้ได้ Incapsula cookie (ไม่รอ 3D viewer)
2. ถาม **grid layer** (`V_INDEX4000_<zone>_LANDNO`) ว่า bbox นี้อยู่ในระวางแผนที่ 1:4000 ใด
   แล้วถอด `utmmap` จาก label ของระวาง (เช่น `"5136 III 6418"` → `513636418`)
3. ดึงแปลงจาก **`V_PARCEL47/48`** ผ่าน WFS (BBOX-filtered, native UTM) หลายระวางพร้อมกัน
4. reproject Indian 1975 → WGS84 ด้วย datum transform **"(2)"** (ตรงกับ basemap)

ทั้งหมดวิ่งผ่าน WFS ที่ไม่โดน throttle จึงไม่ต้องดับเบิลคลิก

## Troubleshooting

**MCP Server ไม่เชื่อมต่อ** — เช็ก `cwd` ใน config, ติดตั้ง deps (`pip install -e .`), restart Claude Desktop

**ได้ข้อความ "โดน DOL จำกัดการเข้าถึงชั่วคราว"** — Incapsula rate-limit ชั่วคราว รอ 2-3 นาทีแล้วลองใหม่

**"ไม่พบแปลงที่ดินในพื้นที่นี้"** — พื้นที่อาจอยู่นอกความครอบคลุมของกรมที่ดิน หรือ bbox ผิด

**QGIS เปิดแล้ว layers ไม่แสดง** — ใช้ QGIS 3.36+ และเปิดจากไฟล์ `.qgs` ใน `gis/` (หรือแตกจาก zip)

**ไม่พบจังหวัด/อำเภอ/ตำบล** — เช็ก `LANDMAP_SHAPEFILE_DIR`, ลอง `search_location` หาชื่อที่ถูกต้อง
