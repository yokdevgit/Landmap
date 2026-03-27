"""
Boundary Service - Thai Administrative Boundaries from Shapefiles

Provides functionality to:
- List provinces, districts, sub-districts
- Search locations by name
- Get bounding box for any administrative area
"""

import os
from pathlib import Path
from typing import Optional
import geopandas as gpd
import pandas as pd


class BoundaryService:
    """Service for querying Thai administrative boundaries."""

    def __init__(self, shapefile_dir: str):
        """
        Initialize with shapefile directory.

        Args:
            shapefile_dir: Path to directory containing province folders with shapefiles
        """
        self.shapefile_dir = Path(shapefile_dir)
        self._cache: dict[str, gpd.GeoDataFrame] = {}
        self._all_data: Optional[gpd.GeoDataFrame] = None
        self._province_folder_map: dict[str, str] = {}
        self._build_province_map()

    def _build_province_map(self):
        """Build mapping of province names to folder names."""
        if not self.shapefile_dir.exists():
            return

        for folder in self.shapefile_dir.iterdir():
            if folder.is_dir():
                # Folder name is English province name
                self._province_folder_map[folder.name.lower()] = folder.name

    def _load_province(self, province_folder: str) -> Optional[gpd.GeoDataFrame]:
        """Load shapefile for a province."""
        if province_folder in self._cache:
            return self._cache[province_folder]

        shapefile_path = self.shapefile_dir / province_folder / f"{province_folder}.shp"
        if not shapefile_path.exists():
            return None

        try:
            gdf = gpd.read_file(shapefile_path)
            self._cache[province_folder] = gdf
            return gdf
        except Exception as e:
            import sys; print(f"Error loading {shapefile_path}: {e}", file=sys.stderr)
            return None

    def _load_all_data(self) -> gpd.GeoDataFrame:
        """Load all shapefiles and combine into one GeoDataFrame."""
        if self._all_data is not None:
            return self._all_data

        all_gdfs = []
        for folder_name in self._province_folder_map.values():
            gdf = self._load_province(folder_name)
            if gdf is not None:
                all_gdfs.append(gdf)

        if all_gdfs:
            self._all_data = pd.concat(all_gdfs, ignore_index=True)
        else:
            self._all_data = gpd.GeoDataFrame()

        return self._all_data

    def _find_province_folder(self, province: str) -> Optional[str]:
        """Find folder name for a province (supports Thai and English names)."""
        province_lower = province.lower().strip()

        # Direct match with folder name
        if province_lower in self._province_folder_map:
            return self._province_folder_map[province_lower]

        # Load all data to search by Thai name
        all_data = self._load_all_data()
        if all_data.empty:
            return None

        # Search by Thai name
        match = all_data[
            (all_data['ADM1_TH'].str.lower() == province_lower) |
            (all_data['ADM1_EN'].str.lower() == province_lower)
        ]

        if not match.empty:
            folder_name = match.iloc[0]['ADM1_EN']
            # Convert to folder format (may have spaces)
            for key, value in self._province_folder_map.items():
                if key == folder_name.lower():
                    return value
            # Try direct match
            return folder_name

        return None

    def list_provinces(self) -> list[dict]:
        """List all provinces."""
        all_data = self._load_all_data()
        if all_data.empty:
            return []

        # Get unique provinces
        provinces = all_data[['ADM1_EN', 'ADM1_TH']].drop_duplicates()
        provinces = provinces.sort_values('ADM1_EN')

        return [
            {"name_en": row['ADM1_EN'], "name_th": row['ADM1_TH']}
            for _, row in provinces.iterrows()
        ]

    def list_districts(self, province: str) -> list[dict]:
        """List all districts in a province."""
        folder = self._find_province_folder(province)
        if not folder:
            return []

        gdf = self._load_province(folder)
        if gdf is None:
            return []

        # Get unique districts
        districts = gdf[['ADM2_EN', 'ADM2_TH']].drop_duplicates()
        districts = districts.sort_values('ADM2_EN')

        return [
            {"name_en": row['ADM2_EN'], "name_th": row['ADM2_TH']}
            for _, row in districts.iterrows()
        ]

    def list_subdistricts(self, province: str, district: str) -> list[dict]:
        """List all sub-districts in a district."""
        folder = self._find_province_folder(province)
        if not folder:
            return []

        gdf = self._load_province(folder)
        if gdf is None:
            return []

        district_lower = district.lower().strip()

        # Filter by district
        filtered = gdf[
            (gdf['ADM2_TH'].str.lower() == district_lower) |
            (gdf['ADM2_EN'].str.lower() == district_lower)
        ]

        if filtered.empty:
            return []

        # Get unique sub-districts
        subdistricts = filtered[['ADM3_EN', 'ADM3_TH']].drop_duplicates()
        subdistricts = subdistricts.sort_values('ADM3_EN')

        return [
            {"name_en": row['ADM3_EN'], "name_th": row['ADM3_TH']}
            for _, row in subdistricts.iterrows()
        ]

    def get_bbox(
        self,
        province: str,
        district: Optional[str] = None,
        subdistrict: Optional[str] = None
    ) -> Optional[dict]:
        """
        Get bounding box for an administrative area.

        Args:
            province: Province name (Thai or English)
            district: District name (optional)
            subdistrict: Sub-district name (optional)

        Returns:
            Dict with bbox [min_lon, min_lat, max_lon, max_lat] and area_km2
        """
        folder = self._find_province_folder(province)
        if not folder:
            return None

        gdf = self._load_province(folder)
        if gdf is None:
            return None

        filtered = gdf

        # Filter by district if provided
        if district:
            district_lower = district.lower().strip()
            filtered = filtered[
                (filtered['ADM2_TH'].str.lower() == district_lower) |
                (filtered['ADM2_EN'].str.lower() == district_lower)
            ]

        # Filter by sub-district if provided
        if subdistrict:
            subdistrict_lower = subdistrict.lower().strip()
            filtered = filtered[
                (filtered['ADM3_TH'].str.lower() == subdistrict_lower) |
                (filtered['ADM3_EN'].str.lower() == subdistrict_lower)
            ]

        if filtered.empty:
            return None

        # Calculate total bounds
        total_bounds = filtered.total_bounds  # [minx, miny, maxx, maxy]

        # Calculate approximate area (rough estimate in km2)
        # Using Shape_Area from shapefile (in square degrees) and converting
        total_area_deg2 = filtered['Shape_Area'].sum()
        # Approximate conversion at Thailand's latitude (~15 degrees)
        # 1 degree longitude ~= 110 km, 1 degree latitude ~= 111 km
        area_km2 = total_area_deg2 * 110 * 111

        return {
            "bbox": [
                float(total_bounds[0]),  # min_lon
                float(total_bounds[1]),  # min_lat
                float(total_bounds[2]),  # max_lon
                float(total_bounds[3])   # max_lat
            ],
            "area_km2": area_km2
        }

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """
        Search for locations by name.

        Args:
            query: Search query (Thai or English)
            limit: Maximum number of results

        Returns:
            List of matching locations
        """
        all_data = self._load_all_data()
        if all_data.empty:
            return []

        query_lower = query.lower().strip()

        # Search in all name columns
        mask = (
            all_data['ADM1_TH'].str.lower().str.contains(query_lower, na=False) |
            all_data['ADM1_EN'].str.lower().str.contains(query_lower, na=False) |
            all_data['ADM2_TH'].str.lower().str.contains(query_lower, na=False) |
            all_data['ADM2_EN'].str.lower().str.contains(query_lower, na=False) |
            all_data['ADM3_TH'].str.lower().str.contains(query_lower, na=False) |
            all_data['ADM3_EN'].str.lower().str.contains(query_lower, na=False)
        )

        filtered = all_data[mask].head(limit)

        return [
            {
                "province_th": row['ADM1_TH'],
                "province_en": row['ADM1_EN'],
                "district_th": row['ADM2_TH'],
                "district_en": row['ADM2_EN'],
                "subdistrict_th": row['ADM3_TH'],
                "subdistrict_en": row['ADM3_EN']
            }
            for _, row in filtered.iterrows()
        ]

    def get_geometry(
        self,
        province: str,
        district: Optional[str] = None,
        subdistrict: Optional[str] = None
    ) -> Optional[gpd.GeoDataFrame]:
        """
        Get geometry for an administrative area.

        Returns filtered GeoDataFrame with geometry for the specified area.
        """
        folder = self._find_province_folder(province)
        if not folder:
            return None

        gdf = self._load_province(folder)
        if gdf is None:
            return None

        filtered = gdf

        if district:
            district_lower = district.lower().strip()
            filtered = filtered[
                (filtered['ADM2_TH'].str.lower() == district_lower) |
                (filtered['ADM2_EN'].str.lower() == district_lower)
            ]

        if subdistrict:
            subdistrict_lower = subdistrict.lower().strip()
            filtered = filtered[
                (filtered['ADM3_TH'].str.lower() == subdistrict_lower) |
                (filtered['ADM3_EN'].str.lower() == subdistrict_lower)
            ]

        if filtered.empty:
            return None

        return filtered
