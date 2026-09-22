"""국가철도공단 노하리 T1/T2 Sentinel-2 밴드를 AOI 인근만 windowed로 받는다.

기존 `src/arcpy_pipeline/imagery_tasking.py::download_bands()`는 타일 전체
COG를 통째로 내려받는다 - 노하리 AOI가 254m^2(2~3픽셀)짜리 필지 하나뿐이라
100km x 100km 타일 전체(밴드당 수십~백MB)를 받는 것은 낭비다. 이 스크립트는
동일한 STAC 조회/서명(sign_href) 로직은 그대로 재사용하되, rasterio의
windowed read(HTTP range request, COG 특성상 가능)로 AOI 주변 일부만 받는다.

주의: 이렇게 받은 로컬 GeoTIFF는 원본 타일과 동일한 원본 CRS(EPSG:32652)/
해상도(10m)를 유지한다 - 재투영/AOI 최종 clip은 기존
`raster_preprocess.build_stacked_scene()`이 담당한다(중복 구현 금지).
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.windows import from_bounds, transform as window_transform

from src.arcpy_pipeline.imagery_tasking import sign_href

logger = logging.getLogger("download_kr_rail_nohari_imagery")

BANDS = ["B02", "B03", "B04", "B08"]


def _aoi_bounds_in_scene_crs(aoi_path: str | Path, scene_crs, buffer_m: float) -> tuple[float, float, float, float]:
    aoi = gpd.read_file(aoi_path).to_crs(scene_crs)
    minx, miny, maxx, maxy = aoi.total_bounds
    return (minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m)


def download_windowed_bands(
    scene: dict,
    aoi_path: str | Path,
    out_dir: str | Path,
    bands: list[str] = BANDS,
    buffer_m: float = 300.0,
) -> list[Path]:
    """scene(search_archive 결과 dict)의 지정 밴드를 AOI 주변만 잘라 로컬에 저장한다.

    Args:
        scene: search_archive()가 반환한 장면 dict ({"id", "assets": {band: href}, ...}).
        aoi_path: AOI 벡터 경로 (밴드의 원본 CRS로 자동 변환해 사용).
        out_dir: 저장 디렉터리.
        bands: 받을 밴드 목록.
        buffer_m: AOI 주변 여유 폭(m) - 다운스트림에서 AOI를 넓혀야 할 경우를
            대비해 정확한 필지 경계보다 조금 넓게 받아둔다(분석 AOI 자체를
            넓히는 것이 아니라 "원본 다운로드 범위"만 여유를 두는 것).

    Returns:
        저장된 로컬 GeoTIFF 경로 목록 (bands 순서와 동일).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for band in bands:
        href = scene["assets"].get(band)
        if not href:
            raise KeyError(f"[IMAGERY] '{band}' 밴드가 장면에 없습니다: {scene['id']}")
        out_path = out_dir / f"{band}.tif"
        signed = sign_href(href)
        logger.info("[IMAGERY] windowed read: %s %s -> %s", scene["id"], band, out_path)
        with rasterio.open(signed) as src:
            bounds = _aoi_bounds_in_scene_crs(aoi_path, src.crs, buffer_m)
            window = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
            data = src.read(1, window=window)
            out_transform = window_transform(window, src.transform)
            profile = src.profile.copy()
            profile.update(
                height=data.shape[0], width=data.shape[1], transform=out_transform,
                driver="GTiff",
            )
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(data, 1)
        saved.append(out_path)
        logger.info(
            "[IMAGERY] 저장 완료: %s (shape=%s, valid_pixels=%d/%d)",
            out_path, data.shape, int(np.count_nonzero(data)), data.size,
        )
    return saved
