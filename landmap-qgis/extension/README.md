# The Scout - Chrome Extension

ตัวเก็บข้อมูลอัจฉริยะสำหรับจับ WMS Tiles จากเว็บกรมที่ดิน พร้อมดาวน์โหลดรูปภาพทันที

## 🎯 หน้าที่หลัก

**The Scout** ทำหน้าที่เป็น **Smart Data Collector** ที่:
- ✅ **Passive Monitoring**: ดักจับ Network Request แบบไม่รบกวนการใช้งาน
- ✅ **Immediate Download**: ดาวน์โหลดรูปภาพทันทีเมื่อจับ URL ได้ (ป้องกัน Session Expiration)
- ✅ **Smart Deduplication**: ตรวจสอบและดาวน์โหลดเฉพาะ URL ที่ไม่ซ้ำ
- ✅ **Base64 Encoding**: แปลงรูปภาพเป็น Base64 สำหรับเก็บใน JSON
- ✅ **Session Management**: จัดการชุดข้อมูลเป็น Session แยกกันไม่ปน

## 🏗️ สถาปัตยกรรม

### การทำงานแบบเดิม (❌ ไม่ทำงาน)
```
1. ดักจับ URL
2. เก็บ URL ใน JSON
3. Export JSON
4. HQ โหลดรูปจาก URL → ล้มเหลว (Session หมดอายุ)
```

### การทำงานแบบใหม่ (✅ ทำงาน)
```
1. ดักจับ URL
2. ตรวจสอบ URL ซ้ำ (ถ้าซ้ำ → ข้าม)
3. ดาวน์โหลดรูปภาพทันที (ขณะ Session ยังใช้งานได้)
4. แปลงเป็น Base64
5. เก็บ Base64 + Metadata ใน JSON
6. Export JSON (มีรูปภาพฝังอยู่แล้ว)
7. HQ แตกไฟล์ Base64 → สำเร็จ 100%
```

## 📦 โครงสร้าง JSON ที่ Export

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
      "imageData": "data:image/png;base64,iVBORw0KGgo..."  // รูปภาพจริง (Base64)
    }
  ]
}
```

## 🚀 การใช้งาน

### 1. ติดตั้ง Extension
```bash
cd extension
bun install
bun run build
```

### 2. โหลดเข้า Chrome
1. เปิด `chrome://extensions/`
2. เปิด "Developer mode"
3. คลิก "Load unpacked"
4. เลือกโฟลเดอร์ `extension/dist`

### 3. เริ่ม Recording
1. เปิดเว็บกรมที่ดิน: `https://landsmaps.dol.go.th`
2. คลิกไอคอน Extension
3. ตั้งชื่อ Session (เช่น "ที่ดินแปลง A")
4. กด **"Start Recording"**
5. เลื่อนดูแผนที่ในพื้นที่ที่ต้องการ
6. กด **"Stop Recording"** เมื่อเสร็จ
7. กด **"Export JSON"**

## ⚡ คุณสมบัติพิเศษ

### Deduplication Engine
- ตรวจสอบ URL ซ้ำก่อนดาวน์โหลด
- ใช้ `Set` เก็บ URL ที่ดาวน์โหลดแล้ว
- ประหยัดเวลาและ bandwidth

### Badge Counter
- แสดงจำนวน Tiles ที่จับได้บน Extension Icon
- อัพเดทแบบ Real-time

### Session Isolation
- แต่ละ Session เก็บข้อมูลแยกกัน
- Clear Session ไม่กระทบ Session อื่น

## 🛠️ Development

```bash
# Install dependencies
bun install

# Development mode (HMR)
bun run dev

# Build for production
bun run build

# Type checking
bun run type-check
```

## 📝 เทคนิคสำคัญ

### การดาวน์โหลดรูปภาพ
```typescript
// ใช้ fetch() ใน background script (มี Session Cookie)
const response = await fetch(url);
const blob = await response.blob();

// แปลงเป็น Base64
const reader = new FileReader();
reader.readAsDataURL(blob);
// ผลลัพธ์: "data:image/png;base64,..."
```

### การป้องกัน Session Expiration
- ดาวน์โหลดทันทีขณะ Browser ยังเปิดหน้าเว็บอยู่
- ใช้ Session Cookie ของ Browser
- ไม่ต้องกังวลเรื่อง CORS หรือ Authentication

## 🐛 Troubleshooting

### Extension ไม่จับ Tiles
- ตรวจสอบว่ากด "Start Recording" แล้ว
- ลองรีโหลดหน้าเว็บ
- ตรวจสอบ Console ใน `chrome://extensions/` (Errors)

### ไฟล์ Export ขนาดใหญ่
- ปกติแล้ว 100 tiles ≈ 5-10 MB
- Base64 ทำให้ไฟล์โตขึ้น ~33% จากรูปต้นฉบับ
- แลกกับความน่าเชื่อถือ 100%

### Badge Counter ไม่อัพเดท
- ตรวจสอบว่า Extension มี Permission `storage`, `webRequest`
- ลองปิด-เปิด Extension ใหม่

## 📚 Tech Stack

- **Manifest V3** - Chrome Extension Standard
- **React + TypeScript** - UI Framework
- **Vite** - Build Tool
- **chrome.webRequest** - Network Monitoring
- **chrome.storage.local** - Data Persistence
- **FileReader API** - Base64 Encoding
- **chrome.downloads** - File Export

## 🔐 Permissions

```json
{
  "permissions": [
    "webRequest",           // ดักจับ Network Request
    "storage",              // เก็บข้อมูล Session
    "downloads"             // Export ไฟล์
  ],
  "host_permissions": [
    "https://landsmaps.dol.go.th/*"  // เข้าถึงเฉพาะเว็บกรมที่ดิน
  ]
}
```

## 📄 License

MIT
