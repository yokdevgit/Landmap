# The HQ - Processing Dashboard

ศูนย์บัญชาการสำหรับประมวลผลข้อมูล WMS Tiles และสร้างไฟล์ GIS

## 🎯 หน้าที่หลัก

**The HQ** ทำหน้าที่เป็น **Processor & Visualization Center** ที่:
- ✅ **JSON Parser**: อ่านไฟล์ JSON จาก The Scout
- ✅ **Base64 Decoder**: แปลงรูปภาพจาก Base64 กลับเป็นไฟล์ PNG
- ✅ **Interactive Map**: แสดง Preview ของพื้นที่ที่จับมา
- ✅ **World File Generator**: สร้างไฟล์ `.pgw` สำหรับ Georeferencing
- ✅ **QGIS Integration**: สร้างไฟล์ `.qlr` สำหรับนำเข้า QGIS แบบครบเซ็ต
- ✅ **ZIP Packager**: รวมไฟล์ทั้งหมดเป็น ZIP พร้อมดาวน์โหลด

## 🏗️ สถาปัตยกรรม

### Data Flow
```
1. อัปโหลด JSON (ที่มี Base64 Images)
2. Parse และแสดง Preview บนแผนที่
3. กด "Process & Download"
   ├─ แตก Base64 → PNG Files
   ├─ คำนวณ BBOX → สร้าง .pgw Files
   ├─ สร้างไฟล์ .qlr (QGIS Layer Definition)
   └─ รวมทุกอย่างเป็น ZIP
4. ดาวน์โหลด ZIP ไปใช้งานใน QGIS
```

### การประมวลผลแบบใหม่ (✅ ไม่ต้องโหลดจาก Server)
```
ข้อมูลเดิม (❌):
- JSON มีแค่ URL
- HQ พยายาม fetch จาก URL
- ล้มเหลวเพราะ Session หมดอายุ/CORS

ข้อมูลใหม่ (✅):
- JSON มีรูปภาพ Base64 ฝังอยู่แล้ว
- HQ แค่ decode Base64 → รูปภาพ
- ได้รูปภาพ 100% ไม่ต้องง Network Request
```

## 📦 Input Format (JSON จาก Scout)

```json
{
  "session": "ที่ดินแปลง A",
  "recordedAt": "2024-12-21T10:30:00.000Z",
  "totalTiles": 150,
  "data": [
    {
      "url": "https://landsmaps.dol.go.th/geoserver/...",
      "bbox": [101.678, 15.018, 101.689, 15.029],
      "width": 256,
      "height": 256,
      "timestamp": 1703152200000,
      "imageData": "data:image/png;base64,iVBORw0KGgo..."
    }
  ]
}
```

## 📦 Output Format (ZIP)

```
landmap-session-name.zip/
├── tile_0.png          # รูปภาพ Tile
├── tile_0.pgw          # World File สำหรับ tile_0.png
├── tile_1.png
├── tile_1.pgw
├── ...
└── landmap.qlr         # QGIS Layer Definition (ครบเซ็ต)
```

## 🚀 การใช้งาน

### 1. ติดตั้งและรัน
```bash
cd dashboard
bun install
bun run dev
```

เปิดเบราว์เซอร์ที่ `http://localhost:5173`

### 2. อัปโหลด JSON
- ลากไฟล์ JSON (จาก The Scout) ลงในกล่อง "Drop Mission JSON Here"
- หรือคลิกกล่องเพื่อเลือกไฟล์

### 3. ตรวจสอบ Preview
- แผนที่จะแสดงพื้นที่ที่จับมา (Red Dashed Box)
- ตรวจสอบจำนวน Tiles และพิกัดใน Sidebar

### 4. Process & Download
- กด **"⚡ Process & Download"**
- รอให้ระบบประมวลผล (มี Progress Bar)
- ดาวน์โหลดไฟล์ ZIP อัตโนมัติ

### 5. นำเข้า QGIS
ดูรายละเอียดที่ [Main README](../README.md#-วิธีนำเข้า-qgis)

## ⚡ คุณสมบัติพิเศษ

### Interactive Map Preview
- ใช้ Leaflet แสดงตำแหน่งพื้นที่
- Red Dashed Box = บริเวณที่จับมา
- Zoom Control Button สำหรับมุมมองที่แม่นยำ

### Smart BBOX Calculation
- คำนวณพิกัดรวมจาก Tiles ทั้งหมด
- กรอง Outliers (พิกัดนอกขอบเขตประเทศไทย)
- Auto-fit Map ให้เห็นพื้นที่ที่เหมาะสม

### World File Generation
สร้างไฟล์ `.pgw` โดยอัตโนมัติตามมาตรฐาน:
```
pixelSizeX
0.0
0.0
-pixelSizeY  (ติดลบเพราะ Y แกนกลับ)
minLon       (X coordinate ของมุมบนซ้าย)
maxLat       (Y coordinate ของมุมบนซ้าย)
```

### QGIS Layer Definition (.qlr)
สร้างไฟล์ XML ที่ QGIS อ่านได้:
- CRS: EPSG:4326 (WGS 84)
- Extent: BBOX ของแต่ละ Tile
- Provider: GDAL (Raster)
- Renderer: Single Band Color Data

## 🛠️ Development

```bash
# Install
bun install

# Dev (HMR + Proxy)
bun run dev

# Build
bun run build

# Preview Production Build
bun run preview

# Type Check
bun run type-check
```

## 🔧 Vite Proxy Configuration

Dashboard ใช้ Proxy สำหรับ Development (ถ้าต้องการทดสอบโหลดจาก URL):
```typescript
// vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      '/geoserver': {
        target: 'https://landsmaps.dol.go.th',
        changeOrigin: true,
        headers: {
          'Referer': 'https://landsmaps.dol.go.th/',
          'User-Agent': 'Mozilla/5.0...'
        }
      }
    }
  }
})
```

## 🐛 Troubleshooting

### แผนที่ไม่แสดงพื้นที่
- ตรวจสอบว่า JSON มี `bbox` ที่ถูกต้อง
- ลองกด **"🔍 Zoom to Data"**
- ดูพิกัดใน Sidebar (ต้องอยู่ในประเทศไทย)

### รูปภาพใน ZIP เป็นไฟล์เปล่า
- ตรวจสอบว่า JSON มี `imageData` (Base64)
- ถ้าไม่มี = ใช้ Scout เวอร์ชันเก่า (ต้องอัพเดท)

### ZIP ขนาดใหญ่มาก
- ปกติ: 100 tiles ≈ 5-10 MB
- ขึ้นอยู่กับรูปภาพต้นฉบับ

### QGIS โหลด .qlr แล้วหาไฟล์ไม่เจอ
- ต้องแตก ZIP ก่อน
- ไฟล์ `.png`, `.pgw`, `.qlr` ต้องอยู่ใน Folder เดียวกัน
- ลาก `.qlr` เข้า QGIS จากภายใน Folder ที่แตกแล้ว

## 📚 Tech Stack

- **React + TypeScript** - UI Framework
- **Vite** - Build Tool & Dev Server
- **Leaflet** -  Map Visualization
- **react-leaflet** - React Bindings
- **JSZip** - ZIP File Creation
- **file-saver** - Download Trigger
- **Tailwind CSS** - (Optional) Styling

## 🎨 UI Components

```
App.tsx
├─ MapContainer (Leaflet)
│  ├─ TileLayer (Base Map)
│  ├─ MapFlyTo (Auto Zoom)
│  ├─ ZoomControl (Manual Zoom Button)
│  ├─ Rectangle (Visual BBOX)
│  └─ ImageOverlay[] (Preview Tiles - ถ้ามี URL)
├─ Sidebar
│  ├─ DropZone (Drag & Drop JSON)
│  ├─ Stats Card (Mission Info, Tiles Count)
│  └─ Process Button
└─ Loading Overlay (During Processing)
```

## 🔐 Security

- ไม่มีการส่งข้อมูลออกนอก Browser
- ทุกอย่างประมวลผลใน Client-side
- ไม่ต้อง Backend Server

## 📄 License

MIT
