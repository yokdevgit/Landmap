"""
Test automated tile capture with double-click for parcel layer loading.
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from tile_fetcher import TileFetcher


async def main():
    print("=" * 60)
    print("Testing Automated Tile Capture")
    print("=" * 60)
    print()
    print("This test will:")
    print("1. Open browser to landsmaps.dol.go.th")
    print("2. Wait for you to solve CAPTCHA if needed")
    print("3. Navigate to target area")
    print("4. Double-click to load parcel layer")
    print("5. Pan around and capture tiles")
    print()
    print("Target area: Lop Buri (14.71-14.72, 100.465-100.475)")
    print()

    fetcher = TileFetcher()

    # Test with Lop Buri coordinates
    result = await fetcher.fetch_tiles(
        bbox=[100.465, 14.71, 100.475, 14.72],
        session_name="test_auto_capture",
        zoom_level=17,
        output_dir=str(Path(__file__).parent.parent.parent / "output"),
        timeout_seconds=180
    )

    print()
    print("=" * 60)
    print(f"Result: {result['tile_count']} tiles captured")
    print(f"Output: {result['output_path']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
