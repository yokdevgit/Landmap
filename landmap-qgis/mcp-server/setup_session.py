"""
Session Setup Script - Human-in-the-loop for Incapsula

This script:
1. Opens a visible browser to landsmaps.dol.go.th
2. Waits for map to load and interacts with it to establish session
3. Saves the session cookies for reuse by the tile fetcher

Usage:
    python setup_session.py
"""

import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SESSION_FILE = Path(__file__).parent / "session_state.json"


async def main():
    print("=" * 60)
    print("Session Setup for DOL Website")
    print("=" * 60)
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )

        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        page = await context.new_page()

        try:
            print("Opening DOL website...")
            await page.goto("https://landsmaps.dol.go.th", timeout=120000)

            print("Waiting for map to load...")
            await asyncio.sleep(10)

            # Try to close any popups (like location permission)
            try:
                # Look for close buttons or dismiss dialogs
                await page.keyboard.press("Escape")
            except:
                pass

            # Wait for map element and interact with it
            print("Looking for map element...")
            await asyncio.sleep(5)

            # Try to navigate to Bang Na area using JavaScript
            print("Navigating to Bang Na area...")
            await page.evaluate("""
                () => {
                    if (typeof map !== 'undefined' && map.setView) {
                        map.setView([13.6642918, 100.6133455], 17);
                        return 'leaflet';
                    }
                    return 'no map found';
                }
            """)

            await asyncio.sleep(3)

            # Try double-clicking on the map to trigger API call
            print("Double-clicking on map to trigger parcel lookup...")

            # Find map container and click on it
            map_element = await page.query_selector('#map, .leaflet-container, [class*="map"]')
            if map_element:
                box = await map_element.bounding_box()
                if box:
                    # Double-click in center of map
                    center_x = box['x'] + box['width'] / 2
                    center_y = box['y'] + box['height'] / 2
                    await page.mouse.dblclick(center_x, center_y)
                    print(f"  Double-clicked at ({center_x}, {center_y})")

            await asyncio.sleep(5)

            # Now try the API call
            print("\nTesting API access...")
            result = await page.evaluate("""
                async () => {
                    try {
                        const response = await fetch('https://landsmaps.dol.go.th/apiService/LandsMaps/GetParcelByDouleClick/13.6642918/100.6133455');
                        const text = await response.text();
                        return { status: response.status, text: text.substring(0, 300) };
                    } catch (e) {
                        return { error: e.toString() };
                    }
                }
            """)

            print(f"API Response: {result}")

            if result.get('status') == 200 and '[' in result.get('text', ''):
                print("\nSUCCESS! API is working.")
                await context.storage_state(path=str(SESSION_FILE))
                print(f"Session saved to: {SESSION_FILE}")
            else:
                print("\nAPI returned non-success response.")
                print("Saving session anyway for debugging...")
                await context.storage_state(path=str(SESSION_FILE))

            print("\nKeeping browser open for 30 seconds for inspection...")
            print("You can manually interact with the map to verify it works.")
            print("Check the Network tab in DevTools for API calls.")
            await asyncio.sleep(30)

        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
