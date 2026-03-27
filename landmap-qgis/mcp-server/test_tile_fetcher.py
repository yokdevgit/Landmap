"""
Test script for the new API-based tile fetcher with JWT support.

Usage:
    python test_tile_fetcher.py

This will attempt to fetch tiles for a small area in Lop Buri (known to have parcels).
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from tile_fetcher import TileFetcher


async def main():
    # Test bbox in Lop Buri area (from the successful API call we captured)
    # Using coordinates near 14.7146238, 100.46981
    bbox = [
        100.465,   # min_lon
        14.710,    # min_lat
        100.475,   # max_lon
        14.720     # max_lat
    ]

    print("=" * 60)
    print("Testing API-based Tile Fetcher with JWT")
    print("=" * 60)
    print(f"BBOX: {bbox}")
    print(f"Area: approximately 1km x 1km in Lop Buri")
    print()
    print("A browser window will open - this is needed to get JWT token.")
    print()

    fetcher = TileFetcher()

    try:
        result = await fetcher.fetch_tiles(
            bbox=bbox,
            session_name="test_jwt_fetch",
            zoom_level=17,
            output_dir=str(Path(__file__).parent.parent.parent / "output")
        )

        print()
        print("=" * 60)
        print("Result:")
        print(f"  Tiles captured: {result['tile_count']}")
        print(f"  Output path: {result['output_path']}")
        print("=" * 60)

        if result['tile_count'] > 0:
            print("\nSUCCESS! The tile fetcher is working.")
            print("Check the output folder for mission.json and tile images.")
        else:
            print("\nNo tiles captured. Check the console output for errors.")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
