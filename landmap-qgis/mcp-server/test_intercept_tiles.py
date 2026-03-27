"""
Test intercepting actual WMS tiles from the DOL website.
This captures the exact tiles the website displays.
"""

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from playwright.async_api import async_playwright


async def main():
    # Test coordinates in Lop Buri (known to have parcel data)
    test_lat = 14.7146
    test_lon = 100.4698

    print("=" * 60)
    print("Intercepting WMS Tiles from DOL Website")
    print("=" * 60)

    captured_tiles = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()

        # Intercept WMS tile responses
        async def handle_response(response):
            url = response.url
            if 'geoserver/LANDSMAPS/wms' in url and 'GetMap' in url:
                try:
                    body = await response.body()
                    if len(body) > 500:  # Has actual data
                        parsed = urlparse(url)
                        params = parse_qs(parsed.query)
                        bbox = params.get('BBOX', params.get('bbox', ['']))[0]

                        tile_data = {
                            'url': url,
                            'bbox': bbox,
                            'size': len(body),
                            'imageData': base64.b64encode(body).decode('utf-8')
                        }
                        captured_tiles.append(tile_data)
                        print(f"Captured tile {len(captured_tiles)}: {len(body)} bytes, bbox={bbox[:50]}...")
                except Exception as e:
                    print(f"Error capturing: {e}")

        page.on('response', handle_response)

        try:
            print("Opening DOL website...")
            await page.goto("https://landsmaps.dol.go.th", timeout=120000)
            await asyncio.sleep(5)

            # Navigate to test location
            print(f"Navigating to {test_lat}, {test_lon}...")
            await page.evaluate(f"""
                () => {{
                    if (typeof map !== 'undefined' && map.setView) {{
                        map.setView([{test_lat}, {test_lon}], 17);
                        return 'success';
                    }}
                    return 'no map';
                }}
            """)

            # Wait for tiles to load
            print("Waiting for tiles to load...")
            await asyncio.sleep(10)

            # Pan around to capture more tiles
            print("Panning to capture more tiles...")
            for dx, dy in [(0.005, 0), (-0.005, 0), (0, 0.005), (0, -0.005)]:
                await page.evaluate(f"""
                    () => {{
                        if (typeof map !== 'undefined' && map.panBy) {{
                            map.panBy([{dx * 10000}, {dy * 10000}]);
                        }}
                    }}
                """)
                await asyncio.sleep(3)

            print(f"\nCaptured {len(captured_tiles)} tiles total")

            if captured_tiles:
                # Save tiles
                output_dir = Path(__file__).parent.parent.parent / "output" / "intercepted_tiles"
                output_dir.mkdir(parents=True, exist_ok=True)
                images_dir = output_dir / "images"
                images_dir.mkdir(exist_ok=True)

                for i, tile in enumerate(captured_tiles[:10]):  # Save up to 10
                    img_path = images_dir / f"tile_{i}.png"
                    with open(img_path, 'wb') as f:
                        f.write(base64.b64decode(tile['imageData']))
                    print(f"Saved {img_path}")

                print(f"\nSaved to {output_dir}")

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
