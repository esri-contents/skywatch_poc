"""STEP 13 - 행정정보(건축물대장) 기반 Validation.

두 개의 독립적인 조인 키를 함께 사용한다(하나만으로는 커버리지가 낮음이
실측으로 확인됨):

1. PNU 키(key9): 법정동10 + 본번4 + 부번4 (표준 PNU 19자리 중 산여부
   1자리는 제외). VWorld 건물 layer의 산여부 자리가 건축물대장 표제부와
   체계적으로 어긋나는 것이 실측 확인되어(VWorld는 대부분 1/2, 표제부는
   대부분 0) 제외했다. 단독으로는 1,771/2,737(64.7%) 매칭.
2. 도로명주소 키(road_key): 도로명코드 7자리(표제부 naRoadCd의 뒤 7자리 -
   앞 5자리는 시군구코드라 VWorld의 rn_cd와 자릿수가 안 맞았던 것을
   실측으로 확인) + 건물본번4 + 건물부번4. 단독으로 1,818/2,737(66.4%) 매칭.

두 키를 OR로 합치면 1,854/2,737(67.7%)로 개선된다. 그래도 약 32%는
여전히 매칭되지 않는데, 창릉이 조성 중인 신도시라 건축물대장이 아직
없는 건물(공사 중)이 실제로 섞여 있을 가능성과, 두 조인 키 모두에서
설명 안 되는 표기 차이가 남아있을 가능성을 배제할 수 없다 - 이 잔여
32%는 "행정정보 없음"으로 보수적으로 처리한다(administrative_uncertainty=1.0).
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


def _parse_buld_no(buld_no: str) -> tuple[str, str]:
    """VWorld 'buld_no'("307-16" 또는 "315")를 (본번, 부번)으로 분리."""
    if not buld_no:
        return ("0", "0")
    parts = str(buld_no).split("-")
    main = parts[0].strip() or "0"
    sub = parts[1].strip() if len(parts) > 1 else "0"
    return main, sub


def _road_key_from_register(reg: pd.DataFrame) -> pd.Series:
    road7 = reg["naRoadCd"].astype(str).str.strip().str[-7:]
    return (
        road7 + "_"
        + reg["naMainBun"].astype(str).str.zfill(4) + "_"
        + reg["naSubBun"].astype(str).str.zfill(4)
    )


def _road_key_from_buildings(buildings: gpd.GeoDataFrame) -> pd.Series:
    mains, subs = zip(*buildings["buld_no"].map(_parse_buld_no))
    return (
        buildings["rn_cd"].astype(str).str.strip() + "_"
        + pd.Series(mains, index=buildings.index).str.zfill(4) + "_"
        + pd.Series(subs, index=buildings.index).str.zfill(4)
    )


def join_building_register(
    buildings: gpd.GeoDataFrame,
    register_items: list[dict],
) -> gpd.GeoDataFrame:
    """건물 footprint에 건축물대장 표제부 속성을 PNU + 도로명주소 이중 조인한다.

    Args:
        buildings: build_buildings.py 결과 (pnu, rn_cd, buld_no 컬럼 포함).
        register_items: download.fetch_building_title_info() 결과 리스트.

    Returns:
        REGISTER_COLUMNS + has_register_match + match_method 컬럼이 추가된
        GeoDataFrame. 한 키에 여러 레코드가 걸리면 사용승인일이 가장 최근인
        레코드를 사용한다.
    """
    reg = pd.DataFrame(register_items)
    if reg.empty:
        out = buildings.copy()
        for c in REGISTER_COLUMNS:
            out[c] = None
        out["has_register_match"] = False
        out["match_method"] = None
        return out

    reg["key9"] = _join_key9(reg["pnu"])
    reg["road_key"] = _road_key_from_register(reg)
    reg_by_pnu = reg.sort_values("useAprDay", ascending=False).drop_duplicates("key9", keep="first")
    reg_by_road = reg.sort_values("useAprDay", ascending=False).drop_duplicates("road_key", keep="first")

    out = buildings.copy()
    key9 = _join_key9(out["pnu"])
    road_key = _road_key_from_buildings(out)

    # 두 조인을 buildings와 별개인 단일-컬럼 프레임에서 각각 수행해
    # REGISTER_COLUMNS 이름 충돌(suffix 혼동)을 원천적으로 피한다.
    pnu_join = pd.DataFrame({"key9": key9}).merge(
        reg_by_pnu[["key9", *REGISTER_COLUMNS]], on="key9", how="left"
    )
    road_join = pd.DataFrame({"road_key": road_key}).merge(
        reg_by_road[["road_key", *REGISTER_COLUMNS]], on="road_key", how="left"
    )
    pnu_matched = pnu_join["mainPurpsCdNm"].notna().to_numpy()

    for c in REGISTER_COLUMNS:
        out[c] = pd.Series(
            pnu_join[c].to_numpy(), index=out.index
        ).where(pnu_matched, pd.Series(road_join[c].to_numpy(), index=out.index))

    out["has_register_match"] = out["mainPurpsCdNm"].notna()
    out["match_method"] = None
    out.loc[pnu_matched, "match_method"] = "pnu"
    out.loc[(~pnu_matched) & out["has_register_match"], "match_method"] = "road_address"

    logger.info(
        "[VALIDATION] 건축물대장 매칭: %d / %d 건물 (%.1f%%) - pnu=%d, road_address=%d",
        out["has_register_match"].sum(), len(out),
        100 * out["has_register_match"].mean(),
        (out["match_method"] == "pnu").sum(), (out["match_method"] == "road_address").sum(),
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
