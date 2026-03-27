"""
Test script to verify GetParcelByDouleClick API works.

This is a simpler test that just checks if we can:
1. Establish a session with DOL website
2. Call the GetParcelByDouleClick API
3. Get valid utm data

Usage:
    python test_api_call.py
"""

import asyncio
from playwright.async_api import async_playwright


async def main():
    # Test coordinates in Bang Na (known to have parcel data)
    test_lat = 13.6642918
    test_lon = 100.6133455

    print("=" * 60)
    print("Testing GetParcelByDouleClick API")
    print("=" * 60)
    print(f"Test location: lat={test_lat}, lon={test_lon}")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled']
        )

        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        page = await context.new_page()

        try:
            # Step 1: Navigate to DOL website and wait for Incapsula challenge
            print("Step 1: Navigating to DOL website...")
            print("  - This may take a while as Incapsula challenge needs to resolve...")

            await page.goto("https://landsmaps.dol.go.th", wait_until='domcontentloaded', timeout=90000)

            # Wait for Incapsula challenge to potentially resolve
            # Look for elements that indicate the real page has loaded
            print("  - Waiting for page to fully load...")

            # Try multiple times to wait for the page
            for attempt in range(12):  # 12 attempts x 5 seconds = 60 seconds max
                await asyncio.sleep(5)

                # Check if we're still on Incapsula challenge
                content = await page.content()
                if "_Incapsula_Resource" in content:
                    print(f"  - Still on Incapsula challenge (attempt {attempt + 1}/12)...")
                else:
                    print("  - Incapsula challenge resolved!")
                    break
            else:
                print("  - Warning: Incapsula challenge may not have fully resolved")

            print("  - Website loaded")

            # Step 2: Call API
            print("\nStep 2: Calling GetParcelByDouleClick API...")
            api_url = f"https://landsmaps.dol.go.th/apiService/LandsMaps/GetParcelByDouleClick/{test_lat}/{test_lon}"
            print(f"  - URL: {api_url}")

            result = await page.evaluate(f"""
                async () => {{
                    try {{
                        const response = await fetch('{api_url}');
                        const text = await response.text();
                        if (!response.ok) {{
                            return {{ error: 'HTTP ' + response.status, status: response.status, text: text.substring(0, 500) }};
                        }}
                        // Check if it looks like JSON
                        if (text.trim().startsWith('[') || text.trim().startsWith('{{')) {{
                            const data = JSON.parse(text);
                            return {{ success: true, data: data }};
                        }} else {{
                            return {{ error: 'Not JSON', text: text.substring(0, 500) }};
                        }}
                    }} catch (e) {{
                        return {{ error: e.toString() }};
                    }}
                }}
            """)

            print(f"\nResult: {result}")

            if result and result.get('success') and result.get('data'):
                data = result['data']
                if isinstance(data, list) and len(data) > 0:
                    parcel = data[0]
                    print("\n" + "=" * 60)
                    print("SUCCESS! API returned parcel data:")
                    print("=" * 60)
                    print(f"  utm: {parcel.get('utm', 'N/A')}")
                    print(f"  utm1: {parcel.get('utm1', 'N/A')}")
                    print(f"  utm2: {parcel.get('utm2', 'N/A')}")
                    print(f"  utm3: {parcel.get('utm3', 'N/A')}")
                    print(f"  utm4: {parcel.get('utm4', 'N/A')}")

                    utm1 = parcel.get('utm1', '')
                    utm3 = parcel.get('utm3', '')
                    utm4 = parcel.get('utm4', '')
                    if utm1 and utm3 and utm4:
                        utmmap = f"{utm1}{utm3}{utm4}"
                        print(f"\n  Constructed utmmap: {utmmap}")
                        print(f"  viewparams: utmmap:{utmmap}")
                else:
                    print("\nAPI returned empty array - no parcel at this location")
            else:
                print(f"\nAPI call failed: {result}")

        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
