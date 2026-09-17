"""고양 창릉 AOI 건물 데이터 생성 - VWorld 도로명주소건물(lt_c_spbd) 기반.

VWorld WFS는 STARTINDEX 상한이 1000으로 확인되어(페이지당 최대 1000건,
쿼리당 최대 2000건) bbox를 4분할 재귀 방식으로 나눠 전량을 받는다
(download.py::download_vworld_wfs_layer). 이 스크립트는 그 결과를
실제 AOI 폴리곤으로 clip하여 최종 건물 데이터를 만든다.

주의: lt_c_spbd는 건물 footprint + 층수(gro_flo_co/und_flo_co) + PNU 등을
제공하지만, 연면적/사용승인일/주용도 같은 상세 건축물대장 속성은 없다.
그 속성은 별도 건축물대장 API(공공데이터포털)로 PNU 기준 보강해야 한다
(아직 미검증 - README 참고).
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd

logger = logging.getLogger("build_buildings")


def build_buildings(
    bbox_geojson_path: str | Path,
    aoi_path: str | Path,
    out_path: str | Path,
) -> gpd.GeoDataFrame:
    """bbox로 받은 건물 GeoJSON을 AOI 폴리곤으로 clip해 저장한다.

    Args:
        bbox_geojson_path: download_vworld_wfs_layer()가 저장한 원본 GeoJSON.
        aoi_path: AOI GeoPackage 경로.
        out_path: 저장할 GeoPackage 경로.

    Returns:
        Clip된 건물 GeoDataFrame (AOI와 동일 CRS - 프로젝트마다 analysis_crs가
        다를 수 있어(예: hwaseong_jinan EPSG:5186 vs jeju_gimnyeong EPSG:5179),
        하드코딩 대신 이미 프로젝트 analysis_crs로 저장된 AOI 자체의 CRS를
        기준으로 삼는다 - 두 CRS가 어긋나면 이후 change_polygons와 concat할 때
        조용히 실패하지 않고 CRS mismatch로 바로 드러난다).
    """
    aoi = gpd.read_file(aoi_path)
    analysis_crs = aoi.crs

    buildings = gpd.read_file(bbox_geojson_path)
    if buildings.crs is None:
        buildings = buildings.set_crs("EPSG:4326")
    buildings = buildings.to_crs(analysis_crs)

    aoi = aoi.to_crs(analysis_crs)
    clipped = gpd.clip(buildings, aoi)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clipped.to_file(out_path, driver="GPKG", layer="buildings")
    logger.info(
        "[DATA] 건물 clip 완료: %s (원본 %d건 -> AOI 내 %d건)",
        out_path, len(buildings), len(clipped),
    )
    return clipped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    build_buildings(
        "data/raw/buildings/changneung_bbox_buildings.geojson",
        "data/aoi/changneung_test_aoi.gpkg",
        "data/processed/buildings/changneung_buildings_clipped.gpkg",
    )
