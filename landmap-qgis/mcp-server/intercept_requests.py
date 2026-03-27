"""
Request Interceptor - See what the website sends when you interact with the map

This script:
1. Opens browser to landsmaps.dol.go.th
2. Logs all API requests when you double-click on the map
3. Shows what headers/tokens are being used

Usage:
    python intercept_requests.py
    Then double-click on the map to see the API calls
"""

import asyncio
from playwright.async_api import async_playwright


async def main():
    print("=" * 60)
    print("Request Interceptor for DOL Website")
    print("=" * 60)
    print()
    print("Instructions:")
    print("1. Wait for the map to load")
    print("2. Double-click anywhere on the map")
    print("3. Watch the console for API request details")
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

        # Set up request logging
        async def log_request(request):
            if "apiService" in request.url or "GetParcel" in request.url:
                print("\n" + "=" * 60)
                print(f"API REQUEST DETECTED!")
                print(f"URL: {request.url}")
                print(f"Method: {request.method}")
                print(f"Headers:")
                for key, value in request.headers.items():
                    print(f"  {key}: {value}")
                print("=" * 60)

        async def log_response(response):
            if "apiService" in response.url or "GetParcel" in response.url:
                print("\n" + "-" * 60)
                print(f"API RESPONSE!")
                print(f"URL: {response.url}")
                print(f"Status: {response.status}")
                try:
                    body = await response.text()
                    print(f"Body: {body[:500]}")
                except:
                    print("Could not read body")
                print("-" * 60)

        page.on("request", log_request)
        page.on("response", log_response)

        try:
            print("Opening DOL website...")
            await page.goto("https://landsmaps.dol.go.th", timeout=120000)

            print("\nMap should be loading...")
            print("Please double-click on the map when ready.")
            print("Press Ctrl+C to exit.\n")

            # Keep running
            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            print("\nExiting...")
        except Exception as e:
            print(f"\nError: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
