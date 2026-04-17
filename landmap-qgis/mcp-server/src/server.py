"""
Landmap MCP Server - Thai Land Department Map Fetcher

This MCP server provides tools to:
1. Query Thai administrative boundaries (provinces, districts, sub-districts)
2. Fetch land map tiles from กรมที่ดิน (DOL) using headless browser
3. Process tiles into GIS-ready files (PNG + PGW + QLR)
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .boundary_service import BoundaryService
from .tile_fetcher import TileFetcher
from .gis_processor import GISProcessor

# Initialize services
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SHAPEFILE_DIR = os.environ.get(
    "LANDMAP_SHAPEFILE_DIR",
    str(_REPO_ROOT / "shapefiles")
)
OUTPUT_DIR = os.environ.get(
    "LANDMAP_OUTPUT_DIR",
    str(_REPO_ROOT / "output")
)

boundary_service = BoundaryService(SHAPEFILE_DIR)
tile_fetcher = TileFetcher()
gis_processor = GISProcessor(OUTPUT_DIR)

# Create MCP server
server = Server("landmap-mcp-server")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="list_provinces",
            description="แสดงรายชื่อจังหวัดทั้งหมดในประเทศไทย (List all provinces in Thailand)",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="list_districts",
            description="แสดงรายชื่ออำเภอ/เขต ในจังหวัดที่ระบุ (List all districts in a province)",
            inputSchema={
                "type": "object",
                "properties": {
                    "province": {
                        "type": "string",
                        "description": "ชื่อจังหวัด (ภาษาไทยหรืออังกฤษ) เช่น 'กรุงเทพมหานคร' หรือ 'Bangkok'"
                    }
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
                    "province": {
                        "type": "string",
                        "description": "ชื่อจังหวัด (ภาษาไทยหรืออังกฤษ)"
                    },
                    "district": {
                        "type": "string",
                        "description": "ชื่ออำเภอ/เขต (ภาษาไทยหรืออังกฤษ) เช่น 'บางนา' หรือ 'Bang Na'"
                    }
                },
                "required": ["province", "district"]
            }
        ),
        Tool(
            name="get_boundary_bbox",
            description="หาขอบเขตพิกัด (BBOX) ของตำบล/อำเภอ/จังหวัด เพื่อใช้ดึงแผนที่ (Get bounding box coordinates)",
            inputSchema={
                "type": "object",
                "properties": {
                    "province": {
                        "type": "string",
                        "description": "ชื่อจังหวัด (ภาษาไทยหรืออังกฤษ)"
                    },
                    "district": {
                        "type": "string",
                        "description": "ชื่ออำเภอ/เขต (optional)"
                    },
                    "subdistrict": {
                        "type": "string",
                        "description": "ชื่อตำบล/แขวง (optional)"
                    }
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
                    "query": {
                        "type": "string",
                        "description": "ชื่อที่ต้องการค้นหา เช่น 'บางนา', 'ลาดกระบัง'"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="fetch_landmap_tiles",
            description="ดึงแผนที่ที่ดินจากกรมที่ดิน (Fetch land map tiles from DOL website)",
            inputSchema={
                "type": "object",
                "properties": {
                    "bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Bounding box [min_lon, min_lat, max_lon, max_lat] เช่น [100.5, 13.7, 100.6, 13.8]"
                    },
                    "province": {
                        "type": "string",
                        "description": "ชื่อจังหวัด (ใช้แทน bbox ได้)"
                    },
                    "district": {
                        "type": "string",
                        "description": "ชื่ออำเภอ (optional, ใช้คู่กับ province)"
                    },
                    "subdistrict": {
                        "type": "string",
                        "description": "ชื่อตำบล (optional, ใช้คู่กับ province และ district)"
                    },
                    "session_name": {
                        "type": "string",
                        "description": "ชื่อ session สำหรับบันทึกผลลัพธ์"
                    },
                    "zoom_level": {
                        "type": "integer",
                        "description": "ระดับ zoom (15-19) default: 17",
                        "default": 17
                    }
                },
                "required": ["session_name"]
            }
        ),
        Tool(
            name="process_to_gis",
            description="แปลง tiles ที่ดึงมาเป็นไฟล์ GIS (PNG + PGW + QLR) สำหรับ QGIS",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "ชื่อ session ที่ต้องการแปลง"
                    }
                },
                "required": ["session_name"]
            }
        ),
        Tool(
            name="list_sessions",
            description="แสดงรายการ sessions ที่ดึงข้อมูลไว้แล้ว",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="process_to_shapefiles",
            description="แปลงข้อมูล WFS ที่ดึงมาเป็น Shapefiles + QGIS project (.qgs) แบบ vector จริง",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "ชื่อ session ที่ต้องการแปลง"
                    }
                },
                "required": ["session_name"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "list_provinces":
            result = boundary_service.list_provinces()
            return [TextContent(
                type="text",
                text=f"จังหวัดทั้งหมด {len(result)} จังหวัด:\n\n" +
                     "\n".join([f"- {p['name_th']} ({p['name_en']})" for p in result])
            )]

        elif name == "list_districts":
            province = arguments.get("province", "")
            result = boundary_service.list_districts(province)
            if not result:
                return [TextContent(type="text", text=f"ไม่พบจังหวัด '{province}'")]
            return [TextContent(
                type="text",
                text=f"อำเภอ/เขต ใน {province} ({len(result)} แห่ง):\n\n" +
                     "\n".join([f"- {d['name_th']} ({d['name_en']})" for d in result])
            )]

        elif name == "list_subdistricts":
            province = arguments.get("province", "")
            district = arguments.get("district", "")
            result = boundary_service.list_subdistricts(province, district)
            if not result:
                return [TextContent(type="text", text=f"ไม่พบอำเภอ '{district}' ในจังหวัด '{province}'")]
            return [TextContent(
                type="text",
                text=f"ตำบล/แขวง ใน {district}, {province} ({len(result)} แห่ง):\n\n" +
                     "\n".join([f"- {s['name_th']} ({s['name_en']})" for s in result])
            )]

        elif name == "get_boundary_bbox":
            province = arguments.get("province", "")
            district = arguments.get("district")
            subdistrict = arguments.get("subdistrict")

            result = boundary_service.get_bbox(province, district, subdistrict)
            if not result:
                return [TextContent(type="text", text="ไม่พบพื้นที่ที่ระบุ")]

            location_name = subdistrict or district or province
            return [TextContent(
                type="text",
                text=f"ขอบเขตพิกัดของ {location_name}:\n\n" +
                     f"BBOX: [{result['bbox'][0]:.6f}, {result['bbox'][1]:.6f}, {result['bbox'][2]:.6f}, {result['bbox'][3]:.6f}]\n" +
                     f"- Min Longitude: {result['bbox'][0]:.6f}\n" +
                     f"- Min Latitude: {result['bbox'][1]:.6f}\n" +
                     f"- Max Longitude: {result['bbox'][2]:.6f}\n" +
                     f"- Max Latitude: {result['bbox'][3]:.6f}\n\n" +
                     f"พื้นที่โดยประมาณ: {result.get('area_km2', 'N/A'):.2f} ตร.กม."
            )]

        elif name == "search_location":
            query = arguments.get("query", "")
            result = boundary_service.search(query)
            if not result:
                return [TextContent(type="text", text=f"ไม่พบผลลัพธ์สำหรับ '{query}'")]

            text_lines = [f"ผลการค้นหา '{query}' ({len(result)} รายการ):\n"]
            for item in result[:20]:  # Limit to 20 results
                text_lines.append(
                    f"- {item['subdistrict_th']} ({item['subdistrict_en']}), "
                    f"{item['district_th']}, {item['province_th']}"
                )
            return [TextContent(type="text", text="\n".join(text_lines))]

        elif name == "fetch_landmap_tiles":
            session_name = arguments.get("session_name", "default")
            zoom_level = arguments.get("zoom_level", 17)

            # Get bbox from arguments or from location
            bbox = arguments.get("bbox")
            location_info = None

            if not bbox:
                province = arguments.get("province")
                district = arguments.get("district")
                subdistrict = arguments.get("subdistrict")

                if not province:
                    return [TextContent(type="text", text="กรุณาระบุ bbox หรือ province")]

                bbox_result = boundary_service.get_bbox(province, district, subdistrict)
                if not bbox_result:
                    return [TextContent(type="text", text="ไม่พบพื้นที่ที่ระบุ")]
                bbox = bbox_result["bbox"]

                # Save location info for geometry retrieval later
                location_info = {
                    "province": province,
                    "district": district,
                    "subdistrict": subdistrict
                }

            # Fetch tiles using Playwright
            result = await tile_fetcher.fetch_tiles(
                bbox=bbox,
                session_name=session_name,
                zoom_level=zoom_level,
                output_dir=OUTPUT_DIR,
                location_info=location_info
            )

            return [TextContent(
                type="text",
                text=f"ดึงข้อมูลแผนที่สำเร็จ!\n\n" +
                     f"Session: {session_name}\n" +
                     f"จำนวน tiles: {result['tile_count']}\n" +
                     f"BBOX: {bbox}\n" +
                     f"บันทึกที่: {result['output_path']}\n\n" +
                     f"ใช้คำสั่ง process_to_gis เพื่อแปลงเป็นไฟล์ GIS"
            )]

        elif name == "process_to_gis":
            session_name = arguments.get("session_name", "")
            result = await gis_processor.process_session(session_name)

            if not result["success"]:
                return [TextContent(type="text", text=f"เกิดข้อผิดพลาด: {result['error']}")]

            return [TextContent(
                type="text",
                text=f"แปลงไฟล์ GIS สำเร็จ!\n\n" +
                     f"Session: {session_name}\n" +
                     f"จำนวน tiles: {result['tile_count']}\n" +
                     f"ไฟล์ที่สร้าง:\n" +
                     f"- PNG images: {result['tile_count']} ไฟล์\n" +
                     f"- PGW world files: {result['tile_count']} ไฟล์\n" +
                     f"- QLR layer definition: 1 ไฟล์\n\n" +
                     f"ZIP file: {result['zip_path']}\n\n" +
                     f"วิธีใช้: ลากไฟล์ landmap.qlr เข้า QGIS"
            )]

        elif name == "list_sessions":
            sessions = gis_processor.list_sessions()
            if not sessions:
                return [TextContent(type="text", text="ยังไม่มี session ที่บันทึกไว้")]

            text_lines = [f"Sessions ที่มี ({len(sessions)} รายการ):\n"]
            for s in sessions:
                text_lines.append(f"- {s['name']}: {s['tile_count']} tiles ({s['created_at']})")
            return [TextContent(type="text", text="\n".join(text_lines))]

        elif name == "process_to_shapefiles":
            session_name = arguments.get("session_name", "")
            result = await gis_processor.process_to_shapefiles(session_name)

            if not result["success"]:
                return [TextContent(type="text", text=f"เกิดข้อผิดพลาด: {result['error']}")]

            return [TextContent(
                type="text",
                text=f"สร้าง Shapefiles สำเร็จ!\n\n"
                     f"Session: {session_name}\n"
                     f"Parcel features: {result['parcel_count']}\n"
                     f"Layers: {', '.join(result['layers'])}\n\n"
                     f"ZIP: {result['zip_path']}\n\n"
                     f"วิธีใช้: แตก zip แล้วเปิดไฟล์ .qgs ใน QGIS"
            )]

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
