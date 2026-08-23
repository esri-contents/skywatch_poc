"""Raster 전처리: 재투영 -> AOI Clip -> 밴드 스택.

Sentinel-2 개별 밴드(B02/B03/B04/B08, EPSG:32652 원본)를 분석 좌표계
(EPSG:5186)로 재투영하고, AOI로 clip한 뒤 하나의 다중밴드 GeoTIFF로
합친다. band_order는 항상 [Blue, Green, Red, NIR] 순서를 유지해
Change Detection 단계에서 밴드 인덱스를 고정적으로 참조할 수 있게 한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import mask
from rasterio.warp import Resampling, calculate_default_transform, reproject

logger = logging.getLogger("raster_preprocess")

BAND_ORDER = ["B02", "B03", "B04", "B08"]  # Blue, Green, Red, NIR


def _reproject_band(src_path: Path, dst_crs: str, dst_path: Path) -> None:
    with rasterio.open(src_path) as src:
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
        profile = src.profile.copy()
        profile.update(crs=dst_crs, transform=transform, width=width, height=height)
        with rasterio.open(dst_path, "w", **profile) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )


def build_stacked_scene(
    band_paths: dict[str, str | Path],
    aoi_path: str | Path,
    out_path: str | Path,
    dst_crs: str = "EPSG:5186",
    band_order: list[str] = BAND_ORDER,
) -> Path:
    """밴드별 GeoTIFF를 재투영 -> AOI clip -> 스택하여 하나의 파일로 저장한다.

    Args:
        band_paths: {"B02": path, "B03": path, ...} 형태.
        aoi_path: AOI GeoPackage 경로.
        out_path: 저장할 스택 GeoTIFF 경로.
        dst_crs: 목표 좌표계.
        band_order: 최종 스택의 밴드 순서.

    Returns:
        저장된 파일 경로.
    """
    missing = [b for b in band_order if b not in band_paths]
    if missing:
        raise ValueError(f"[RASTER] 다음 밴드가 없습니다: {missing}")

    aoi = gpd.read_file(aoi_path).to_crs(dst_crs)
    aoi_geom = [aoi.geometry.union_all()]

    tmp_dir = Path(out_path).parent / "_tmp_reproject"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    clipped_arrays = []
    ref_profile = None
    for band in band_order:
        reproj_path = tmp_dir / f"{band}_reproj.tif"
        logger.info("[RASTER] 재투영: %s -> %s", band, dst_crs)
        _reproject_band(Path(band_paths[band]), dst_crs, reproj_path)

        with rasterio.open(reproj_path) as src:
            out_image, out_transform = mask(src, aoi_geom, crop=True)
            if ref_profile is None:
                ref_profile = src.profile.copy()
                ref_profile.update(
                    height=out_image.shape[1], width=out_image.shape[2],
                    transform=out_transform, count=len(band_order),
                )
            clipped_arrays.append(out_image[0])

    stacked = np.stack(clipped_arrays, axis=0)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **ref_profile) as dst:
        dst.write(stacked)
        dst.descriptions = tuple(band_order)

    logger.info(
        "[RASTER] 스택 저장 완료: %s (밴드=%s, shape=%s)",
        out_path, band_order, stacked.shape,
    )
    return out_path
