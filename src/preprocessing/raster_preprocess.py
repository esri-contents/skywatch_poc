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


def _native_resolution_in_crs(path: str | Path, dst_crs: str) -> float:
    """원본 CRS 그대로의 해상도가 아니라, dst_crs로 재투영했을 때의 픽셀 크기(m)를 구한다."""
    with rasterio.open(path) as src:
        transform, _, _ = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
    return abs(transform.a)


def _reproject_to_grid(
    src_path: Path,
    dst_crs: str,
    transform,
    width: int,
    height: int,
    resampling: Resampling,
    out_path: Path,
) -> None:
    with rasterio.open(src_path) as src:
        profile = src.profile.copy()
        profile.update(crs=dst_crs, transform=transform, width=width, height=height)
        dst_arr = np.zeros((src.count, height, width), dtype=src.dtypes[0])
        for b in range(1, src.count + 1):
            reproject(
                source=rasterio.band(src, b),
                destination=dst_arr[b - 1],
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=dst_crs,
                resampling=resampling,
                src_nodata=src.nodata,
                dst_nodata=src.nodata,
            )
        descriptions = src.descriptions if src.descriptions and any(src.descriptions) else None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(dst_arr)
        if descriptions:
            dst.descriptions = descriptions


def align_imagery_pair(
    t1_path: str | Path,
    t2_path: str | Path,
    aoi_path: str | Path,
    out_t1_path: str | Path,
    out_t2_path: str | Path,
    dst_crs: str = "EPSG:5186",
    resolution: float | None = None,
    resampling: Resampling = Resampling.bilinear,
) -> dict:
    """이미 완성된(단일 다중밴드) T1/T2 GeoTIFF를 동일 CRS/해상도/extent/grid로 정렬한다.

    build_stacked_scene()은 밴드별 개별 GeoTIFF(Sentinel-2 등)를 스택으로
    합치는 용도라 국가철도공단 Demo처럼 이미 다중밴드로 완성된 T1/T2 GeoTIFF
    (CRS/해상도/extent가 서로 다를 수 있음)를 입력받는 경량 파이프라인에는
    맞지 않는다. 이 함수는 그 경우를 위해 다음을 수행한다:

    1) AOI를 dst_crs로 변환해 분석 범위(extent)를 정한다.
    2) 해상도가 지정되지 않으면 T1/T2를 각각 dst_crs로 재투영했을 때의
       픽셀 크기 중 더 낮은(coarser) 해상도를 분석 해상도로 쓴다(#13 원칙).
    3) AOI bounds + 분석 해상도로 정확히 동일한 transform/width/height를
       가진 공통 grid를 만들고, T1/T2를 각각 그 grid로 직접 재투영한다
       (중간 clip 단계를 거치지 않아 두 결과의 shape/transform이 항상
       일치한다).
    4) AOI 폴리곤 바깥 픽셀은 nodata로 마스킹한다(AOI가 사각형이 아닐 수
       있으므로).

    영상값(연속값)이므로 resampling 기본값은 bilinear를 쓴다(#13 원칙).

    Returns:
        {"crs": str, "resolution_m": float, "width": int, "height": int,
         "transform": Affine} 형태의 grid 정보.
    """
    aoi = gpd.read_file(aoi_path).to_crs(dst_crs)
    if aoi.empty:
        raise ValueError(f"[RASTER] AOI가 비어 있습니다: {aoi_path}")

    if resolution is None:
        res1 = _native_resolution_in_crs(t1_path, dst_crs)
        res2 = _native_resolution_in_crs(t2_path, dst_crs)
        resolution = max(res1, res2)
        logger.info(
            "[RASTER] 해상도 미지정 - T1/T2 중 더 낮은 해상도를 사용: T1=%.3fm T2=%.3fm -> %.3fm",
            res1, res2, resolution,
        )

    minx, miny, maxx, maxy = aoi.total_bounds
    width = max(1, int(np.ceil((maxx - minx) / resolution)))
    height = max(1, int(np.ceil((maxy - miny) / resolution)))
    transform = rasterio.transform.from_origin(minx, maxy, resolution, resolution)

    out_t1_path = Path(out_t1_path)
    out_t2_path = Path(out_t2_path)
    logger.info("[RASTER] T1 재투영: %s -> %s (grid=%dx%d, res=%.3fm)", t1_path, out_t1_path, width, height, resolution)
    _reproject_to_grid(Path(t1_path), dst_crs, transform, width, height, resampling, out_t1_path)
    logger.info("[RASTER] T2 재투영: %s -> %s (grid=%dx%d, res=%.3fm)", t2_path, out_t2_path, width, height, resolution)
    _reproject_to_grid(Path(t2_path), dst_crs, transform, width, height, resampling, out_t2_path)

    aoi_geom = [aoi.geometry.union_all()]
    for p in (out_t1_path, out_t2_path):
        with rasterio.open(p) as src:
            profile = src.profile.copy()
            nodata = src.nodata if src.nodata is not None else 0
            out_image, _ = mask(src, aoi_geom, crop=False, nodata=nodata)
            descriptions = src.descriptions if src.descriptions and any(src.descriptions) else None
        profile.update(nodata=nodata)
        with rasterio.open(p, "w", **profile) as dst:
            dst.write(out_image)
            if descriptions:
                dst.descriptions = descriptions

    logger.info(
        "[RASTER] T1/T2 정렬 완료: crs=%s grid=%dx%d res=%.3fm",
        dst_crs, width, height, resolution,
    )
    return {
        "crs": str(dst_crs), "resolution_m": resolution,
        "width": width, "height": height, "transform": transform,
    }
