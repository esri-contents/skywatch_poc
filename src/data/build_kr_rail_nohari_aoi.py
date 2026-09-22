"""국가철도공단 노하리 149-2 AOI 확보 - VWorld 연속지적도 WFS 기반.

정확한 필지 경계(AOI)는 임의로 만들 수 없다(#10 원칙) - 이 스크립트는 실제
VWorld 연속지적도(lp_pa_cbnd_bonbun/bubun)를 노하리 인근 bbox로 내려받아
지번이 "149-2"인 필지를 찾는다. VWorld 속성 스키마(필드명)는 레이어/버전마다
다를 수 있어(cadastre_link.py의 PNU_CANDIDATES/JIBUN_CANDIDATES 참고),
자동으로 하나를 확정해 바로 AOI로 쓰지 않고 후보를 파일로 남겨 사람이
확인한 뒤 수동으로 승격(rename/copy)하게 한다.

bbox 기준점(37.1555200N, 126.8544600E)은 OpenStreetMap Nominatim에서 조회한
"노하리" 마을 중심(2026-09-22 확인)이다 - 149-2 필지 자체의 좌표가 아니라
"이 근방에 있을 것"이라는 탐색 범위일 뿐이다.

사용 전 준비:
1. https://www.vworld.kr 에서 무료 Open API 키 발급 (회원가입 -> Open API
   신청 -> 인증키 발급, 승인까지 보통 즉시~수 시간).
2. 저장소 루트에 `.env` 파일을 만들고 `VWORLD_API_KEY=발급받은키` 추가
   (`.env.example` 참고 - `.env`는 git에 커밋하지 않는다).

실행:
    python -m src.data.build_kr_rail_nohari_aoi
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .download import VWORLD_CADASTRE_LAYERS, download_vworld_wfs_layer

logger = logging.getLogger("build_kr_rail_nohari_aoi")

# 노하리 마을 중심(OSM Nominatim, 2026-09-22 조회) +-0.015도(~1.5km) 버퍼 -
# 149-2 필지의 실제 좌표가 아니라 탐색용 근사 범위.
NOHARI_CENTER_WGS84 = (126.8544600, 37.1555200)
_buf = 0.015
NOHARI_SEARCH_BBOX_WGS84 = (
    NOHARI_CENTER_WGS84[0] - _buf, NOHARI_CENTER_WGS84[1] - _buf,
    NOHARI_CENTER_WGS84[0] + _buf, NOHARI_CENTER_WGS84[1] + _buf,
)

TARGET_JIBUN = "149-2"
# cadastre_link.py의 JIBUN_CANDIDATES와 동일한 후보군 - 실제 응답을 받기 전에는
# 어느 필드명이 쓰이는지 확정할 수 없으므로 여러 후보를 순서대로 시도한다.
JIBUN_FIELD_CANDIDATES = ("jibun", "JIBUN", "addr", "ADDR")


def fetch_cadastre_candidates(out_dir: str | Path) -> gpd.GeoDataFrame:
    """노하리 인근 연속지적도(본번+부번)를 받아 지번이 149-2와 일치하는 후보를 찾는다.

    Returns:
        지번이 일치하는 후보 GeoDataFrame (0건일 수 있음 - 그 경우 전체 필지를
        out_dir/nohari_all_parcels.geojson에 저장하니 직접 확인해야 한다).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    for layer in VWORLD_CADASTRE_LAYERS:
        raw_path = out_dir / f"nohari_{layer}_raw.geojson"
        download_vworld_wfs_layer(layer, NOHARI_SEARCH_BBOX_WGS84, raw_path)
        gdf = gpd.read_file(raw_path)
        if not gdf.empty:
            parts.append(gdf)

    if not parts:
        raise RuntimeError(
            "[DATA] VWorld에서 노하리 인근 필지를 하나도 받지 못했습니다 - "
            "VWORLD_API_KEY 또는 bbox를 확인하세요."
        )

    all_parcels = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs)
    logger.info("[DATA] 노하리 인근 전체 필지 %d건 다운로드 완료", len(all_parcels))

    jibun_field = next((c for c in JIBUN_FIELD_CANDIDATES if c in all_parcels.columns), None)
    all_parcels_path = out_dir / "nohari_all_parcels.geojson"
    if jibun_field is None:
        all_parcels.to_file(all_parcels_path, driver="GeoJSON")
        logger.warning(
            "[DATA] 지번 필드를 자동으로 찾지 못했습니다. 실제 컬럼: %s\n"
            "%s 를 직접 열어 지번(149-2)에 해당하는 필지를 찾아 AOI로 저장하세요.",
            list(all_parcels.columns), all_parcels_path,
        )
        return all_parcels.iloc[0:0]

    candidates = all_parcels[all_parcels[jibun_field].astype(str).str.contains(TARGET_JIBUN, na=False)]
    if candidates.empty:
        all_parcels.to_file(all_parcels_path, driver="GeoJSON")
        logger.warning(
            "[DATA] '%s' 지번과 일치하는 필지를 찾지 못했습니다 (필드=%s). "
            "전체 필지를 저장했으니 직접 확인하세요: %s",
            TARGET_JIBUN, jibun_field, all_parcels_path,
        )
        return candidates

    candidates_path = out_dir / "nohari_149-2_candidates.geojson"
    candidates.to_file(candidates_path, driver="GeoJSON")
    logger.info(
        "[DATA] '%s' 지번 후보 %d건 저장: %s\n"
        "여러 건이 나오면 실제 149-2와 정확히 일치하는 것을 직접 확인한 뒤 "
        "data/projects/kr_rail_nohari/aoi/nohari_aoi.geojson으로 복사하세요 "
        "(자동으로 승격하지 않음 - #10 원칙).",
        TARGET_JIBUN, len(candidates), candidates_path,
    )
    return candidates


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    fetch_cadastre_candidates("data/projects/kr_rail_nohari/aoi/_candidates")
