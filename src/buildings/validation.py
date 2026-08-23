"""STEP 13 - 행정정보(건축물대장) 기반 Validation.

PNU 조인 관련 알려진 데이터 품질 이슈: VWorld 건물 layer의 'pnu' 필드는
표준 PNU(법정동10 + 산여부1 + 본번4 + 부번4=19자리) 형식이지만, 산여부
자리 값이 건축물대장 표제부 응답과 체계적으로 어긋나는 것이 실측
확인되었다(VWorld 쪽은 대부분 1/2, 건축물대장 표제부는 대부분 0).
따라서 이 모듈은 산여부를 제외한 "법정동10 + 본번4 + 부번4" 9자리를
조인 키로 사용한다. AOI 내 2,737개 건물 중 1,771개(65%)가 이 키로
매칭되었다 - 나머지는 건축물대장에 아직 없는 건물(현재 공사 중 등)이거나
지번 표기 차이로 추정된다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import geopandas as gpd
import pandas as pd

logger = logging.getLogger("validation")

REGISTER_COLUMNS = [
    "mainPurpsCdNm", "archArea", "totArea", "grndFlrCnt", "ugrndFlrCnt",
    "pmsDay", "stcnsDay", "useAprDay",
]


def _join_key9(pnu: pd.Series) -> pd.Series:
    """PNU에서 산여부(10번째 문자, 0-index 기준)를 제외한 9자리 조인 키."""
    return pnu.str[:10] + pnu.str[11:]


def join_building_register(
    buildings: gpd.GeoDataFrame,
    register_items: list[dict],
) -> gpd.GeoDataFrame:
    """건물 footprint에 건축물대장 표제부 속성을 PNU(산여부 제외) 기준으로 조인한다.

    Args:
        buildings: build_buildings.py 결과 (pnu 컬럼 포함).
        register_items: download.fetch_building_title_info() 결과 리스트.

    Returns:
        REGISTER_COLUMNS + has_register_match 컬럼이 추가된 GeoDataFrame.
        한 필지(key9)에 여러 동/구조물이 등록된 경우 사용승인일이 가장
        최근인 레코드를 사용한다(가장 최근 변화를 우선 반영).
    """
    reg = pd.DataFrame(register_items)
    if reg.empty:
        out = buildings.copy()
        for c in REGISTER_COLUMNS:
            out[c] = None
        out["has_register_match"] = False
        return out

    reg["key9"] = _join_key9(reg["pnu"])
    reg = reg.sort_values("useAprDay", ascending=False).drop_duplicates("key9", keep="first")

    out = buildings.copy()
    out["key9"] = _join_key9(out["pnu"])
    out = out.merge(reg[["key9", *REGISTER_COLUMNS]], on="key9", how="left")
    out["has_register_match"] = out["mainPurpsCdNm"].notna()
    out = out.drop(columns=["key9"])

    logger.info(
        "[VALIDATION] 건축물대장 매칭: %d / %d 건물 (%.1f%%)",
        out["has_register_match"].sum(), len(out),
        100 * out["has_register_match"].mean(),
    )
    return out


def _parse_yyyymmdd(s) -> date | None:
    if not s or not isinstance(s, str) or len(s) != 8:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def compute_administrative_uncertainty(
    row: pd.Series,
    t1_date: date,
    t2_date: date,
) -> float:
    """행정정보로 변화가 설명되는 정도에 따라 불확실성(0=완전 설명, 1=미설명)을 매긴다.

    규칙:
    - 건축물대장 매칭 없음 -> 1.0 (완전 불확실, 기존과 동일)
    - 매칭 있음 + 사용승인일이 T1~T2 사이 -> 0.1 (변화가 인허가로 설명됨)
    - 매칭 있음 + 사용승인일이 T1~T2 밖(또는 없음) -> 0.6 (등록은 되어 있으나
      이번 변화 시점과 직접 대응되지 않음 - 증축 등 재승인 이력 누락 가능성)
    """
    if not row.get("has_register_match"):
        return 1.0
    use_apr = _parse_yyyymmdd(row.get("useAprDay"))
    if use_apr is not None and t1_date <= use_apr <= t2_date:
        return 0.1
    return 0.6
