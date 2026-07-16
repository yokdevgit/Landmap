"""
Landmap MCP Server — Thai Land Department (DOL) parcel fetcher.

Tools:
1. Query Thai administrative boundaries (provinces, districts, sub-districts).
2. get_land_parcels — one-shot: resolve an area, fetch its land parcels
   click-free from the DOL WFS, and build shapefiles + a QGIS project (.qgs) + zip.
3. Lower-level building blocks (fetch_parcels_wfs, process_to_shapefiles) if you
   want to run the two steps separately.

Paths are configurable via env vars / a .env file at the repo root:
  LANDMAP_SHAPEFILE_DIR  (default: <repo>/shapefiles)
  LANDMAP_OUTPUT_DIR     (default: <repo>/output)
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load_dotenv(path: Path):
    """Minimal .env loader (no dependency): KEY=VALUE lines -> os.environ, without
    overriding vars already set in the real environment."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass
    except Exception:
        pass


# Load .env BEFORE importing the services — they read LANDMAP_* at import time.
_load_dotenv(_REPO_ROOT / ".env")

from .boundary_service import BoundaryService   # noqa: E402
from .tile_fetcher import TileFetcher           # noqa: E402
from .gis_processor import GISProcessor         # noqa: E402

SHAPEFILE_DIR = os.environ.get("LANDMAP_SHAPEFILE_DIR", str(_REPO_ROOT / "shapefiles"))
OUTPUT_DIR = os.environ.get("LANDMAP_OUTPUT_DIR", str(_REPO_ROOT / "output"))

boundary_service = BoundaryService(SHAPEFILE_DIR)
tile_fetcher = TileFetcher()
gis_processor = GISProcessor(OUTPUT_DIR)

server = Server("landmap-mcp-server")


def _slug(*parts) -> str:
    """ASCII slug from location parts for a session/folder name; '' if none usable."""
    text = "_".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_land_parcels",
            description=(
                "ดึงรูปแปลงที่ดิน (โฉนด/แปลง/parcels) ของพื้นที่ที่ระบุ แล้วสร้างไฟล์ "
                "QGIS (.qgs) + Shapefile + ZIP ให้ครบในขั้นตอนเดียว. "
                "ใช้เครื่องมือนี้เมื่อผู้ใช้ขอข้อมูลที่ดิน/โฉนด/แปลงที่ดิน/shapefile/qgis "
                "ของจังหวัด/อำเภอ/ตำบล ใดๆ. "
                "ONE-SHOT: resolves the area boundary, fetches parcels click-free from "
                "the DOL WFS (no throttle-prone canvas clicks), reprojects to WGS84, and "
                "writes shapefiles + a ready-to-open QGIS project bundled in a .zip. "
                "Give a province (and optionally district + subdistrict) OR a bbox."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "ชื่อจังหวัด (ไทย/อังกฤษ) เช่น 'กรุงเทพมหานคร'"},
                    "district": {"type": "string", "description": "ชื่ออำเภอ/เขต (optional) เช่น 'บางรัก'"},
                    "subdistrict": {"type": "string", "description": "ชื่อตำบล/แขวง (optional) เช่น 'สีลม'"},
                    "bbox": {
                        "type": "array", "items": {"type": "number"},
                        "description": "ใช้แทนชื่อพื้นที่ได้: [min_lon, min_lat, max_lon, max_lat]"
                    },
                    "session_name": {"type": "string", "description": "ชื่อ session (optional; ตั้งอัตโนมัติจากชื่อพื้นที่ถ้าไม่ระบุ)"},
                },
                "required": []
            }
        ),
        Tool(
            name="list_provinces",
            description="แสดงรายชื่อจังหวัดทั้งหมดในประเทศไทย (List all provinces in Thailand)",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="list_districts",
            description="แสดงรายชื่ออำเภอ/เขต ในจังหวัดที่ระบุ (List all districts in a province)",
            inputSchema={
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "ชื่อจังหวัด (ภาษาไทยหรืออังกฤษ)"}
                },
                "required": ["province"]
            }
        ),
        Tool(
            name="list_subdistricts",
            description="แสดงรายชื่อตำบล/แขวง ในอำเภอที่ระบุ (List all sub-districts in a district)",
            inputSchema={
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "ชื่อจังหวัด (ภาษาไทยหรืออังกฤษ)"},
                    "district": {"type": "string", "description": "ชื่ออำเภอ/เขต (ภาษาไทยหรืออังกฤษ)"}
                },
                "required": ["province", "district"]
            }
        ),
        Tool(
            name="get_boundary_bbox",
            description="หาขอบเขตพิกัด (BBOX) ของตำบล/อำเภอ/จังหวัด (Get bounding box coordinates)",
            inputSchema={
                "type": "object",
                "properties": {
                    "province": {"type": "string", "description": "ชื่อจังหวัด (ภาษาไทยหรืออังกฤษ)"},
                    "district": {"type": "string", "description": "ชื่ออำเภอ/เขต (optional)"},
                    "subdistrict": {"type": "string", "description": "ชื่อตำบล/แขวง (optional)"}
                },
                "required": ["province"]
            }
        ),
        Tool(
            name="search_location",
            description="ค้นหาตำบล/อำเภอ/จังหวัด จากชื่อ (Search for location by name)",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "ชื่อที่ต้องการค้นหา เช่น 'บางนา', 'ลาดกระบัง'"}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="fetch_parcels_wfs",
            description=(
                "[ขั้นตอนย่อย] ดึงรูปแปลงที่ดิน (vector) จาก DOL WFS แบบ click-free เท่านั้น "
                "(ไม่สร้าง .qgs — ใช้ get_land_parcels ถ้าต้องการไฟล์ครบ). "
                "Low-level: fetch parcels only, without processing to shapefiles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bbox": {"type": "array", "items": {"type": "number"},
                             "description": "Bounding box [min_lon, min_lat, max_lon, max_lat]"},
                    "province": {"type": "string", "description": "ชื่อจังหวัด (ใช้แทน bbox ได้)"},
                    "district": {"type": "string", "description": "ชื่ออำเภอ (optional)"},
                    "subdistrict": {"type": "string", "description": "ชื่อตำบล (optional)"},
                    "session_name": {"type": "string", "description": "ชื่อ session สำหรับบันทึกผลลัพธ์"},
                    "utmmap": {"type": "string", "description": "รหัส map sheet (utmmap) ถ้าทราบ — ข้ามการค้นหา sheet"}
                },
                "required": ["session_name"]
            }
        ),
        Tool(
            name="process_to_shapefiles",
            description="[ขั้นตอนย่อย] แปลงข้อมูล WFS ของ session ที่ดึงไว้ ให้เป็น Shapefiles + QGIS project (.qgs)",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_name": {"type": "string", "description": "ชื่อ session ที่ต้องการแปลง"}
                },
                "required": ["session_name"]
            }
        ),
        Tool(
            name="list_sessions",
            description="แสดงรายการ sessions ที่ดึงข้อมูลไว้แล้ว",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
    ]


def _resolve_area(arguments: dict):
    """Resolve (bbox, location_info, area_name_en) from province/district/subdistrict
    or an explicit bbox. Returns (bbox, location_info, name_en) or (None, error_msg, None)."""
    bbox = arguments.get("bbox")
    if bbox:
        return bbox, None, None
    province = arguments.get("province")
    district = arguments.get("district")
    subdistrict = arguments.get("subdistrict")
    if not province:
        return None, "กรุณาระบุพื้นที่ (province/district/subdistrict) หรือ bbox", None
    bbox_result = boundary_service.get_bbox(province, district, subdistrict)
    if not bbox_result:
        return None, f"ไม่พบพื้นที่: {subdistrict or district or province}", None
    location_info = {"province": province, "district": district, "subdistrict": subdistrict}
    return bbox_result["bbox"], location_info, bbox_result.get("name_en")


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "get_land_parcels":
            bbox, location_or_err, name_en = _resolve_area(arguments)
            if bbox is None:
                return [TextContent(type="text", text=location_or_err)]
            location_info = location_or_err  # None when bbox was given directly
            session_name = arguments.get("session_name") or _slug(
                name_en,
                arguments.get("subdistrict"), arguments.get("district"), arguments.get("province"),
            ) or "parcels"

            # Step 1 — fetch parcels click-free from the WFS.
            fetch = await tile_fetcher.fetch_parcels_wfs(
                bbox=bbox, session_name=session_name, output_dir=OUTPUT_DIR,
                location_info=location_info)
            if fetch.get("status") == "blocked":
                return [TextContent(type="text", text=(
                    "โดน DOL จำกัดการเข้าถึงชั่วคราว (Incapsula/rate-limit). "
                    "รอสัก 2-3 นาทีแล้วลองใหม่อีกครั้งครับ"))]
            if not fetch.get("utmmaps"):
                return [TextContent(type="text", text=(
                    f"ไม่พบแปลงที่ดินในพื้นที่นี้ (session: {session_name}). "
                    "อาจอยู่นอกพื้นที่ที่กรมที่ดินมีข้อมูล"))]

            # Step 2 — build shapefiles + QGIS project.
            proc = await gis_processor.process_to_shapefiles(session_name)
            if not proc.get("success"):
                return [TextContent(type="text", text=(
                    f"ดึงแปลงได้ {fetch['feature_count']} แปลง แต่สร้างไฟล์ GIS ไม่สำเร็จ: "
                    f"{proc.get('error')}"))]

            return [TextContent(type="text", text=(
                f"เสร็จแล้ว! ({session_name})\n\n"
                f"พื้นที่: {arguments.get('subdistrict') or arguments.get('district') or arguments.get('province') or 'ตาม bbox'}\n"
                f"Map sheet(s): {', '.join(fetch['utmmaps'])}\n"
                f"จำนวนแปลง (parcels): {proc['parcel_count']}\n"
                f"Layers: {', '.join(proc['layers'])}\n\n"
                f"ไฟล์ ZIP: {proc['zip_path']}\n"
                f"วิธีใช้: แตก zip แล้วเปิดไฟล์ .qgs ใน QGIS ได้เลย (มี OSM basemap + แปลงที่ดิน + ขอบเขต)"))]

        elif name == "list_provinces":
            result = boundary_service.list_provinces()
            return [TextContent(type="text",
                text=f"จังหวัดทั้งหมด {len(result)} จังหวัด:\n\n" +
                     "\n".join([f"- {p['name_th']} ({p['name_en']})" for p in result]))]

        elif name == "list_districts":
            province = arguments.get("province", "")
            result = boundary_service.list_districts(province)
            if not result:
                return [TextContent(type="text", text=f"ไม่พบจังหวัด '{province}'")]
            return [TextContent(type="text",
                text=f"อำเภอ/เขต ใน {province} ({len(result)} แห่ง):\n\n" +
                     "\n".join([f"- {d['name_th']} ({d['name_en']})" for d in result]))]

        elif name == "list_subdistricts":
            province = arguments.get("province", "")
            district = arguments.get("district", "")
            result = boundary_service.list_subdistricts(province, district)
            if not result:
                return [TextContent(type="text", text=f"ไม่พบอำเภอ '{district}' ในจังหวัด '{province}'")]
            return [TextContent(type="text",
                text=f"ตำบล/แขวง ใน {district}, {province} ({len(result)} แห่ง):\n\n" +
                     "\n".join([f"- {s['name_th']} ({s['name_en']})" for s in result]))]

        elif name == "get_boundary_bbox":
            province = arguments.get("province", "")
            district = arguments.get("district")
            subdistrict = arguments.get("subdistrict")
            result = boundary_service.get_bbox(province, district, subdistrict)
            if not result:
                return [TextContent(type="text", text="ไม่พบพื้นที่ที่ระบุ")]
            location_name = subdistrict or district or province
            return [TextContent(type="text",
                text=f"ขอบเขตพิกัดของ {location_name}:\n\n" +
                     f"BBOX: [{result['bbox'][0]:.6f}, {result['bbox'][1]:.6f}, {result['bbox'][2]:.6f}, {result['bbox'][3]:.6f}]\n" +
                     f"พื้นที่โดยประมาณ: {result.get('area_km2', 'N/A'):.2f} ตร.กม.")]

        elif name == "search_location":
            query = arguments.get("query", "")
            result = boundary_service.search(query)
            if not result:
                return [TextContent(type="text", text=f"ไม่พบผลลัพธ์สำหรับ '{query}'")]
            text_lines = [f"ผลการค้นหา '{query}' ({len(result)} รายการ):\n"]
            for item in result[:20]:
                text_lines.append(
                    f"- {item['subdistrict_th']} ({item['subdistrict_en']}), "
                    f"{item['district_th']}, {item['province_th']}")
            return [TextContent(type="text", text="\n".join(text_lines))]

        elif name == "fetch_parcels_wfs":
            session_name = arguments.get("session_name", "default")
            bbox, location_or_err, _ = _resolve_area(arguments)
            if bbox is None:
                return [TextContent(type="text", text=location_or_err)]
            location_info = location_or_err
            result = await tile_fetcher.fetch_parcels_wfs(
                bbox=bbox, session_name=session_name, output_dir=OUTPUT_DIR,
                location_info=location_info, utmmap=arguments.get("utmmap"))
            if result.get("status") == "blocked":
                return [TextContent(type="text", text="โดน DOL จำกัดการเข้าถึงชั่วคราว — รอสักครู่แล้วลองใหม่")]
            return [TextContent(type="text",
                text=f"ดึงรูปแปลงที่ดิน (WFS) เสร็จ!\n\n"
                     f"Session: {session_name}\n"
                     f"Map sheet(s): {', '.join(result['utmmaps']) if result['utmmaps'] else '-'}\n"
                     f"จำนวนแปลง (parcels): {result['feature_count']}\n"
                     f"บันทึกที่: {result['output_path']}\n\n"
                     f"ใช้ process_to_shapefiles เพื่อสร้าง .qgs")]

        elif name == "process_to_shapefiles":
            session_name = arguments.get("session_name", "")
            result = await gis_processor.process_to_shapefiles(session_name)
            if not result["success"]:
                return [TextContent(type="text", text=f"เกิดข้อผิดพลาด: {result['error']}")]
            return [TextContent(type="text",
                text=f"สร้าง Shapefiles + .qgs สำเร็จ!\n\n"
                     f"Session: {session_name}\n"
                     f"Parcel features: {result['parcel_count']}\n"
                     f"Layers: {', '.join(result['layers'])}\n\n"
                     f"ZIP: {result['zip_path']}\n\n"
                     f"วิธีใช้: แตก zip แล้วเปิดไฟล์ .qgs ใน QGIS")]

        elif name == "list_sessions":
            sessions = gis_processor.list_sessions()
            if not sessions:
                return [TextContent(type="text", text="ยังไม่มี session ที่บันทึกไว้")]
            text_lines = [f"Sessions ที่มี ({len(sessions)} รายการ):\n"]
            for s in sessions:
                text_lines.append(f"- {s['name']} ({s['created_at']})")
            return [TextContent(type="text", text="\n".join(text_lines))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
