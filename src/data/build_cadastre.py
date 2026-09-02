"""고양 창릉 AOI 지적(필지) 데이터 생성 - VWorld 연속지적도 기반.

handoff.md 6번 한계표에서 "정식 지구계 완전 추출은 보류"로 남아있던 항목의
전제 조건 중 하나(VWorld 지적도 필지 조회)를 실제로 연동한다. 본번
(lp_pa_cbnd_bonbun)과 부번(lp_pa_cbnd_bubun) 레이어를 둘 다 받아 합쳐야
전체 필지가 나온다 (하나만 받으면 분할된 필지가 누락됨 - 2026-09-01 실측
확인).

build_buildings.py와 동일한 패턴: bbox로 받은 원본을 AOI 폴리곤으로 clip한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

logger = logging.getLogger("build_cadastre")

ANALYSIS_CRS = "EPSG:5186"


def build_cadastre(
    bonbun_geojson_path: str | Path,
    bubun_geojson_path: str | Path,
    aoi_path: str | Path,
    out_path: str | Path,
) -> gpd.GeoDataFrame:
    """본번/부번 필지 GeoJSON을 합쳐 AOI로 clip해 저장한다.

    Args:
        bonbun_geojson_path: download_vworld_wfs_layer("lp_pa_cbnd_bonbun", ...) 결과.
        bubun_geojson_path: download_vworld_wfs_layer("lp_pa_cbnd_bubun", ...) 결과.
        aoi_path: AOI GeoPackage 경로.
        out_path: 저장할 GeoPackage 경로.

    Returns:
        Clip된 필지 GeoDataFrame (ANALYSIS_CRS).
    """
    parts = []
    for p in (bonbun_geojson_path, bubun_geojson_path):
        gdf = gpd.read_file(p)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        parts.append(gdf.to_crs(ANALYSIS_CRS))

    parcels = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=ANALYSIS_CRS)
    # VWorld 원본 필지 중 self-intersection 등 위상 오류가 실측 확인되어
    # (TopologyException), clip 전에 make_valid로 정리한다.
    parcels["geometry"] = parcels.geometry.make_valid()

    aoi = gpd.read_file(aoi_path).to_crs(ANALYSIS_CRS)
    clipped = gpd.clip(parcels, aoi)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clipped.to_file(out_path, driver="GPKG", layer="cadastre")
    logger.info(
        "[DATA] 필지 clip 완료: %s (원본 %d건 -> AOI 내 %d건)",
        out_path, len(parcels), len(clipped),
    )
    return clipped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    build_cadastre(
        "data/raw/cadastre/changneung_lp_pa_cbnd_bonbun.geojson",
        "data/raw/cadastre/changneung_lp_pa_cbnd_bubun.geojson",
        "data/aoi/changneung_test_aoi.gpkg",
        "data/processed/cadastre/changneung_parcels_clipped.gpkg",
    )
