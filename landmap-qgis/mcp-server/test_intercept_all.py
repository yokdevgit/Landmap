"""
Test intercepting ALL network requests to find tile sources.
"""

import asyncio
from playwright.async_api import async_playwright


async def main():
    test_lat = 14.7146
    test_lon = 100.4698

    print("=" * 60)
    print("Intercepting ALL Network Requests")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()

        # Log all requests
        def log_request(request):
            url = request.url
            if any(x in url.lower() for x in ['wms', 'tile', 'geoserver', 'parcel', 'landsmaps']):
                if 'png' in url or 'GetMap' in url or 'image' in url:
                    print(f"REQUEST: {url[:150]}...")

        async def log_response(response):
            url = response.url
            content_type = response.headers.get('content-type', '')
            if 'image' in content_type or 'png' in url:
                size = len(await response.body()) if response.ok else 0
                if size > 100:
                    print(f"IMAGE RESPONSE ({size} bytes): {url[:100]}...")

        page.on('request', log_request)
        page.on('response', log_response)

        try:
            print("\nOpening DOL website...")
            await page.goto("https://landsmaps.dol.go.th", timeout=120000)
            await asyncio.sleep(5)

            print(f"\nNavigating to {test_lat}, {test_lon} at zoom 17...")
            result = await page.evaluate(f"""
                () => {{
                    if (typeof map !== 'undefined' && map.setView) {{
                        map.setView([{test_lat}, {test_lon}], 17);
                        return 'navigated';
                    }}
                    return 'no map found';
                }}
            """)
            print(f"Navigation result: {result}")

            print("\nWaiting for tiles to load (15 seconds)...")
            await asyncio.sleep(15)

            print("\nDone. Check output above for image requests.")

        except Exception as e:
            print(f"Error: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
