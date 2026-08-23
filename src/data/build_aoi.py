"""고양 창릉 AOI 생성 - 행정동 경계 기반.

사업지구의 정식 지구계(지형도면고시)를 아직 확보하지 못한 상태에서,
사용자 요청에 따라 "창릉지구가 소속된 행정동" 전체를 AOI로 사용한다.

출처: vuski/admdongkor (통계청 SGIS 행정동 경계 기반 오픈 데이터)
https://github.com/vuski/admdongkor - ver20240701, EPSG:4326 GeoJSON

검증: 경기도 고양시덕양구 창릉동의 면적은 약 10.98km^2로, 3기신도시.kr에
공시된 창릉지구 공식 사업면적(8,119,006m^2 = 8.12km^2)과 같은 자릿수로
합리적인 초과분(행정동이 사업구역보다 넓은 것은 정상)을 보여 창릉지구가
창릉동(행정동) 안에 포함된다는 정황과 일치한다. 임의로 그린 폴리곤이
아니라 실제 통계청 기반 행정경계 데이터임을 명시한다.

주의: 이 AOI는 사업지구 정식 지구계(고시 기준)보다 넓은 근사치다.
정식 지구계 PDF/SHP를 확보하면 이 AOI를 교체해야 한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd

logger = logging.getLogger("build_aoi")

SOURCE_URL = (
    "https://raw.githubusercontent.com/vuski/admdongkor/master/"
    "ver20240701/HangJeongDong_ver20240701.geojson"
)
TARGET_ADM_NM = ["경기도 고양시덕양구 창릉동"]
ANALYSIS_CRS = "EPSG:5186"  # Korea 2000 / Central Belt 2010 - 국토지리정보원 표준 중부원점, 서울/경기 지역 통용


def build_aoi(
    admdong_geojson_path: str | Path,
    out_path: str | Path,
    adm_names: list[str] = TARGET_ADM_NM,
) -> gpd.GeoDataFrame:
    """행정동 경계에서 지정한 adm_nm(들)을 골라 AOI GeoPackage로 저장한다.

    Args:
        admdong_geojson_path: 전국 행정동 경계 GeoJSON 경로 (admdongkor).
        out_path: 저장할 GeoPackage 경로.
        adm_names: 포함할 행정동 전체 이름(adm_nm) 목록.

    Returns:
        저장된 AOI GeoDataFrame (단일 폴리곤으로 dissolve됨).
    """
    gdf = gpd.read_file(admdong_geojson_path)
    selected = gdf[gdf["adm_nm"].isin(adm_names)].copy()
    if selected.empty:
        raise ValueError(f"[DATA] 지정한 행정동을 찾지 못했습니다: {adm_names}")
    if len(selected) != len(adm_names):
        found = selected["adm_nm"].tolist()
        missing = [n for n in adm_names if n not in found]
        raise ValueError(f"[DATA] 일부 행정동을 찾지 못했습니다: {missing}")

    selected_proj = selected.to_crs(ANALYSIS_CRS)
    area_km2 = float(selected_proj.geometry.area.sum() / 1e6)

    dissolved = selected_proj.dissolve()
    aoi = gpd.GeoDataFrame(
        {
            "aoi_name": ["changneung_test_aoi"],
            "adm_nm_included": [", ".join(adm_names)],
            "area_km2": [area_km2],
            "source": [SOURCE_URL],
            "note": ["행정동 경계 기반 근사 AOI - 정식 지구계 아님"],
        },
        geometry=dissolved.geometry.values,
        crs=ANALYSIS_CRS,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    aoi.to_file(out_path, driver="GPKG", layer="changneung_test_aoi")
    logger.info("[DATA] AOI 저장 완료: %s (면적 %.2f km^2, CRS=%s)", out_path, area_km2, ANALYSIS_CRS)
    return aoi


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    build_aoi(
        "data/interim/admdong_raw/HangJeongDong_ver20240701.geojson",
        "data/aoi/changneung_test_aoi.gpkg",
    )
