"""Raster/Vector 메타데이터 추출 - Data Inventory 생성용."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import rasterio


def inspect_raster(path: str | Path) -> dict[str, Any]:
    """GeoTIFF 등 raster 파일의 핵심 메타데이터를 읽는다."""
    with rasterio.open(path) as src:
        return {
            "file_path": str(path),
            "data_type": "raster",
            "crs": str(src.crs),
            "bounds": str(tuple(round(float(v), 3) for v in src.bounds)),
            "width_or_feature_count": src.width,
            "height": src.height,
            "bands_or_geometry_type": src.count,
            "dtype": src.dtypes[0],
            "nodata": src.nodata,
            "columns": "",
            "notes": "",
        }


def inspect_vector(path: str | Path, layer: str | None = None) -> dict[str, Any]:
    """SHP/GPKG/GeoJSON 등 vector 파일의 핵심 메타데이터를 읽는다."""
    gdf = gpd.read_file(path, layer=layer)
    geom_types = sorted(gdf.geometry.geom_type.dropna().unique().tolist())
    return {
        "file_path": str(path),
        "data_type": "vector",
        "crs": str(gdf.crs),
        "bounds": str(tuple(round(float(v), 3) for v in gdf.total_bounds)),
        "width_or_feature_count": len(gdf),
        "height": "",
        "bands_or_geometry_type": ",".join(geom_types),
        "dtype": "",
        "nodata": "",
        "columns": ",".join(c for c in gdf.columns if c != "geometry"),
        "notes": "",
    }
