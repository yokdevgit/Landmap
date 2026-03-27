# Landmap MCP Server

MCP Server สำหรับดึงข้อมูลแผนที่ที่ดินจากกรมที่ดิน (landsmaps.dol.go.th) ผ่าน Claude Desktop

## Requirements

- **Python 3.10+** (required by MCP and Playwright)
- Shapefile ของตำบล/อำเภอ/จังหวัดในประเทศไทย

## Features

- **ค้นหาพื้นที่**: ค้นหาจังหวัด อำเภอ ตำบล จาก shapefile
- **หาพิกัด BBOX**: หาขอบเขตพิกัดของตำบล/อำเภอ/จังหวัด
- **ดึงแผนที่**: ดึง tiles จากกรมที่ดินผ่าน headless browser
- **สร้างไฟล์ GIS**: แปลงเป็น PNG + PGW + QLR สำหรับ QGIS

## Installation

### 1. ติดตั้ง Dependencies

```bash
cd mcp-server
pip install -e .
```

หรือติดตั้ง dependencies โดยตรง:

```bash
pip install mcp geopandas playwright Pillow aiofiles
playwright install chromium
```

### 2. ตั้งค่า Environment Variables (Optional)

```bash
# Path ไปยัง shapefile directory
export LANDMAP_SHAPEFILE_DIR="C:\Users\PKO-X1-Yoga-G6\Desktop\landmap\shapefiles"

# Path ไปยัง output directory
export LANDMAP_OUTPUT_DIR="C:\Users\PKO-X1-Yoga-G6\Desktop\landmap\output"
```

### 3. ตั้งค่า Claude Desktop

แก้ไขไฟล์ `claude_desktop_config.json`:

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "landmap": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "C:\\Users\\PKO-X1-Yoga-G6\\Desktop\\landmap\\landmap-qgis\\mcp-server",
      "env": {
        "LANDMAP_SHAPEFILE_DIR": "C:\\Users\\PKO-X1-Yoga-G6\\Desktop\\landmap\\shapefiles",
        "LANDMAP_OUTPUT_DIR": "C:\\Users\\PKO-X1-Yoga-G6\\Desktop\\landmap\\output"
      }
    }
  }
}
```

### 4. Restart Claude Desktop

ปิดและเปิด Claude Desktop ใหม่เพื่อโหลด MCP Server

## Available Tools

### 1. `list_provinces`
แสดงรายชื่อจังหวัดทั้งหมด 77 จังหวัด

**ตัวอย่าง**: "แสดงรายชื่อจังหวัดทั้งหมด"

### 2. `list_districts`
แสดงรายชื่ออำเภอ/เขต ในจังหวัดที่ระบุ

**ตัวอย่าง**: "แสดงอำเภอทั้งหมดในจังหวัดเชียงใหม่"

### 3. `list_subdistricts`
แสดงรายชื่อตำบล/แขวง ในอำเภอที่ระบุ

**ตัวอย่าง**: "แสดงตำบลทั้งหมดในอำเภอเมือง จังหวัดเชียงใหม่"

### 4. `get_boundary_bbox`
หาขอบเขตพิกัด (BBOX) ของตำบล/อำเภอ/จังหวัด

**ตัวอย่าง**: "หาพิกัดของตำบลบางนา กรุงเทพ"

### 5. `search_location`
ค้นหาตำบล/อำเภอ/จังหวัด จากชื่อ

**ตัวอย่าง**: "ค้นหาพื้นที่ที่มีคำว่า บางนา"

### 6. `fetch_landmap_tiles`
ดึงแผนที่ที่ดินจากกรมที่ดิน

**ตัวอย่าง**: "ดึงแผนที่ที่ดินตำบลบางนา กรุงเทพ ตั้งชื่อว่า bangna_session"

### 7. `process_to_gis`
แปลง tiles ที่ดึงมาเป็นไฟล์ GIS

**ตัวอย่าง**: "แปลง session bangna_session เป็นไฟล์ GIS"

### 8. `list_sessions`
แสดงรายการ sessions ที่ดึงข้อมูลไว้แล้ว

## Usage Examples

### ดึงแผนที่ตำบล
```
User: ดึงแผนที่ที่ดินตำบลบางนา เขตบางนา กรุงเทพ

Claude: [เรียก get_boundary_bbox เพื่อหาพิกัด]
        [เรียก fetch_landmap_tiles เพื่อดึง tiles]
        [เรียก process_to_gis เพื่อสร้างไฟล์ GIS]

ผลลัพธ์: ไฟล์ ZIP ที่ C:\Users\...\output\bangna\bangna_gis.zip
```

### ค้นหาและดึงข้อมูล
```
User: ช่วยหาพื้นที่ "ลาดกระบัง" และดึงแผนที่ให้หน่อย

Claude: [เรียก search_location เพื่อค้นหา]
        พบ: ลาดกระบัง, เขตลาดกระบัง, กรุงเทพมหานคร
        [เรียก fetch_landmap_tiles]
        ...
```

## Output Files

หลังจากเรียก `process_to_gis` จะได้ไฟล์:

```
output/
└── session_name/
    ├── mission.json          # Metadata
    ├── images/               # Raw tiles
    │   ├── tile_0.png
    │   ├── tile_1.png
    │   └── ...
    ├── gis/                  # GIS-ready files
    │   ├── tile_0.png
    │   ├── tile_0.pgw
    │   ├── tile_1.png
    │   ├── tile_1.pgw
    │   ├── ...
    │   └── landmap.qlr       # QGIS Layer Definition
    └── session_name_gis.zip  # All GIS files bundled
```

## วิธีใช้ใน QGIS

1. แตกไฟล์ ZIP
2. ลากไฟล์ `landmap.qlr` เข้า QGIS
3. หรือใช้ Layer > Add Layer > Add Raster Layer > เลือก PNG ทั้งหมด

## Troubleshooting

### MCP Server ไม่เชื่อมต่อ
- ตรวจสอบ path ใน `claude_desktop_config.json`
- ตรวจสอบว่าติดตั้ง dependencies ครบ
- Restart Claude Desktop

### ดึง tiles ไม่ได้
- ตรวจสอบการเชื่อมต่อ internet
- เว็บกรมที่ดินอาจมี rate limiting
- ลองลด zoom level หรือลดขนาด bbox

### ไม่พบจังหวัด/อำเภอ
- ลองใช้ชื่อภาษาอังกฤษ
- ใช้ search_location เพื่อค้นหา

## License

MIT
