"""
Manual Session Setup - Saves tiles immediately as captured
"""

import asyncio
import base64
from pathlib import Path
from playwright.async_api import async_playwright

# Output directory - save immediately
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "manual_capture"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

tile_count = 0


async def main():
    global tile_count

    print("=" * 60)
    print("Manual Session Setup")
    print("=" * 60)
    print(f"Tiles will be saved to: {OUTPUT_DIR}")
    print()
    print("Instructions:")
    print("1. Solve the hCaptcha if it appears")
    print("2. Zoom into an area with parcels")
    print("3. Double-click on parcels to load tile data")
    print("4. Pan around to capture more tiles")
    print("5. Close browser window when done")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()

        # Save tiles immediately when captured
        async def capture_tile(response):
            global tile_count
            url = response.url
            if 'geoserver' in url and 'wms' in url.lower():
                try:
                    if response.status == 200:
                        body = await response.body()
                        if len(body) > 1000:
                            tile_count += 1
                            img_path = OUTPUT_DIR / f"tile_{tile_count}.png"
                            with open(img_path, 'wb') as f:
                                f.write(body)
                            print(f"Saved tile #{tile_count} ({len(body)} bytes) -> {img_path.name}")
                except Exception as e:
                    pass

        page.on('response', capture_tile)

        try:
            await page.goto("https://landsmaps.dol.go.th", timeout=120000)
            print("\nBrowser opened. Interact with the map...")
            print("Tiles are saved automatically as you browse.\n")

            # Wait for browser to close
            while True:
                try:
                    await asyncio.sleep(1)
                    # Check if page is still open
                    await page.title()
                except:
                    break

        except Exception as e:
            print(f"Browser closed: {e}")

        print(f"\n\nTotal tiles saved: {tile_count}")
        print(f"Location: {OUTPUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
