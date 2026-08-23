"""Change Mask 후처리 (형태학적 연산 + 최소 면적 필터) 및 Polygon화."""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
from rasterio.features import shapes
from shapely.geometry import shape
from skimage.morphology import closing, footprint_rectangle, opening, remove_small_objects

logger = logging.getLogger("postprocess")


def clean_mask(
    mask: np.ndarray,
    pixel_area_m2: float,
    opening_kernel: int = 3,
    closing_kernel: int = 3,
    min_component_area_m2: float = 25,
) -> np.ndarray:
    """Opening -> Closing -> 최소 면적 미만 connected component 제거.

    Args:
        mask: (H, W) 0/1 이진 마스크.
        pixel_area_m2: 픽셀 하나의 면적(m^2).
        opening_kernel: opening 커널 크기.
        closing_kernel: closing 커널 크기.
        min_component_area_m2: 이보다 작은 connected component는 제거.

    Returns:
        정리된 (H, W) 0/1 이진 마스크.
    """
    m = mask.astype(bool)
    m = opening(m, footprint_rectangle((opening_kernel, opening_kernel)))
    m = closing(m, footprint_rectangle((closing_kernel, closing_kernel)))

    min_pixels = max(1, int(round(min_component_area_m2 / pixel_area_m2)))
    m = remove_small_objects(m, min_size=min_pixels)
    return m.astype(np.uint8)


def polygonize_change(
    mask: np.ndarray,
    prob: np.ndarray,
    transform,
    crs,
    t1_date: str,
    t2_date: str,
    method: str = "ensemble",
) -> gpd.GeoDataFrame:
    """이진 change mask를 polygon화하고 필수 필드를 채운다.

    Args:
        mask: (H, W) 0/1 이진 마스크 (postprocess 완료본).
        prob: (H, W) change_probability (mean/max score 계산용).
        transform: raster affine transform.
        crs: raster CRS.
        t1_date: T1 촬영일 (ISO 문자열).
        t2_date: T2 촬영일 (ISO 문자열).
        method: 변화탐지 방법 이름.

    Returns:
        change_id, change_area_m2, mean_change_score, max_change_score,
        t1_date, t2_date, method 필드를 가진 GeoDataFrame.
    """
    records = []
    for geom, value in shapes(mask, mask=mask.astype(bool), transform=transform):
        if value != 1:
            continue
        poly = shape(geom)
        records.append({"geometry": poly})

    if not records:
        logger.warning("[CHANGE] Polygon화 결과 0건")
        return gpd.GeoDataFrame(
            columns=["change_id", "change_area_m2", "mean_change_score",
                     "max_change_score", "t1_date", "t2_date", "method", "geometry"],
            geometry="geometry", crs=crs,
        )

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=crs)

    pixel_area_m2 = abs(transform.a * transform.e)
    from rasterstats import zonal_stats
    stats = zonal_stats(gdf, prob, affine=transform, stats=["mean", "max"], nodata=np.nan)

    gdf["change_id"] = [f"CHG_{i:05d}" for i in range(1, len(gdf) + 1)]
    gdf["change_area_m2"] = gdf.geometry.area.round(2)
    gdf["mean_change_score"] = [round(s["mean"], 4) if s["mean"] is not None else None for s in stats]
    gdf["max_change_score"] = [round(s["max"], 4) if s["max"] is not None else None for s in stats]
    gdf["t1_date"] = t1_date
    gdf["t2_date"] = t2_date
    gdf["method"] = method

    cols = ["change_id", "change_area_m2", "mean_change_score",
            "max_change_score", "t1_date", "t2_date", "method", "geometry"]
    logger.info("[CHANGE] Polygon화 완료: %d개 (pixel_area=%.1fm^2)", len(gdf), pixel_area_m2)
    return gdf[cols]
