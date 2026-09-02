"""건축물 변화유형 규칙기반 분류.

건축물대장 사용승인일(useAprDay)이 있는 건물은 그것을 1차 근거로 사용한다
(사용승인일이 T1~T2 사이면 확정적으로 NEW_BUILDING, 그 밖이면 기존 건물이므로
EXPANSION_OR_RECONSTRUCTION). 사용승인일을 못 구한 건물(현재 PNU 매칭
64.7%, validation.py 참고)만 change_ratio 크기 기반 휴리스틱으로
근사 판정한다. 즉 이 분류기의 신뢰도는 건물마다 다르며, classification_note에
어떤 근거로 판정했는지 항상 기록한다.

휴리스틱(건축물대장 미매칭) 판정에는 brightness_delta(overlay.py가 채운,
대표 change polygon의 T1->T2 밝기 변화 - postprocess.compute_brightness_delta
참고)를 보조 근거로 함께 확인한다. NEW_BUILDING/EXPANSION은 새 구조물이
나타나 밝아지는 방향을 기대하는데, 실제로는 어두워진 경우가 육안검수에서
발견됐다(outputs/reports/high_priority_visual_qa.md CHG_00029/CHG_00068 등).
이런 방향 불일치는 라벨을 강제로 뒤집지 않고 directional_consistency_flag=False로
표시만 한다 - 밝기 하나만으로 반대로 확정하기엔 근거가 약하고(계절/식생 등
다른 요인일 수 있음), 실제 판단은 현장조사자 몫이기 때문이다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import geopandas as gpd

logger = logging.getLogger("classify")

NEW_BUILDING = "NEW_BUILDING"
DEMOLITION = "DEMOLITION"
EXPANSION_OR_RECONSTRUCTION = "EXPANSION_OR_RECONSTRUCTION"
OTHER_CHANGE = "OTHER_CHANGE"


def _parse_yyyymmdd(s) -> date | None:
    if not s or not isinstance(s, str) or len(s) != 8:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def classify_building_changes(
    overlaid: gpd.GeoDataFrame,
    new_building_ratio_min: float = 0.5,
    demolition_score_min: float = 0.6,
    t1_date: date | None = None,
    t2_date: date | None = None,
) -> gpd.GeoDataFrame:
    """change_ratio/near_change/max_change_score 기반 규칙 분류.

    규칙 (근거는 모듈 docstring의 한계 설명 참고):
    1. change_ratio == 0 이고 near_change == True
       -> OTHER_CHANGE (건물 본체는 그대로, 주변 토지/도로 등 변화)
    2. change_ratio == 0 이고 near_change == False
       -> 이 함수 호출 대상에서 제외(건물과 무관한 변화는 change_polygons
          자체 레벨에서 별도 처리)
    3. change_ratio >= new_building_ratio_min
       -> NEW_BUILDING (건물 footprint 거의 전체가 새 변화로 덮임 -> 신축 추정)
    4. 0 < change_ratio < new_building_ratio_min
       -> EXPANSION_OR_RECONSTRUCTION (건물 일부만 변화 -> 증축/개축 추정)
    5. (向후 확장) T1 이미지에 구조가 있었는데 현재 건물 layer에 없는 필지는
       DEMOLITION 후보이나, 현재 건물 layer가 "현재 시점" 스냅샷이라
       이 함수의 입력(buildings)에는 애초에 존재하지 않는다 -> change_polygons
       중 건물과 매칭되지 않는 고신뢰(max_change_score>=demolition_score_min)
       영역을 DEMOLITION 후보로 별도 플래그해야 한다 (change_polygons 레벨에서
       처리, classify_unmatched_changes 참고).

    Args:
        overlaid: overlay.overlay_buildings_with_changes() 결과.
        new_building_ratio_min: 이 이상이면 NEW_BUILDING으로 분류.
        demolition_score_min: (참고용, change_polygons 레벨 분류에서 사용)

    Returns:
        change_type, classification_note, directional_consistency_flag 컬럼이
        추가된 GeoDataFrame. directional_consistency_flag는 건축물대장 미매칭
        휴리스틱 판정에만 True/False로 채워지고(밝기 방향이 change_type 기대와
        일치하는지), 그 외(대장 근거 확정, OTHER_CHANGE, 무관)는 None이다.
    """
    out = overlaid.copy()
    has_register = "has_register_match" in out.columns
    has_brightness = "brightness_delta" in out.columns
    change_types = []
    notes = []
    consistency_flags = []

    for _, row in out.iterrows():
        ratio = row["change_ratio"]
        near = row["near_change"]
        use_apr = _parse_yyyymmdd(row.get("useAprDay")) if has_register else None
        matched = bool(row.get("has_register_match")) if has_register else False
        brightness_delta = row.get("brightness_delta") if has_brightness else None

        if ratio == 0 and near:
            change_types.append(OTHER_CHANGE)
            notes.append("건물 본체 미교차, 버퍼 내 변화만 존재")
            consistency_flags.append(None)
        elif ratio == 0:
            change_types.append(None)
            notes.append("건물과 무관 (change_ratio=0)")
            consistency_flags.append(None)
        elif matched and use_apr is not None and t1_date and t2_date and t1_date <= use_apr <= t2_date:
            change_types.append(NEW_BUILDING)
            notes.append(f"사용승인일={use_apr.isoformat()}이 T1~T2 사이 - 신축 확정(건축물대장 근거)")
            consistency_flags.append(None)
        elif matched and use_apr is not None:
            change_types.append(EXPANSION_OR_RECONSTRUCTION)
            notes.append(
                f"사용승인일={use_apr.isoformat()}로 T1 이전부터 존재 - 증축/개축 확정(건축물대장 근거)"
            )
            consistency_flags.append(None)
        elif ratio >= new_building_ratio_min:
            change_types.append(NEW_BUILDING)
            consistent = brightness_delta is None or bool(brightness_delta >= 0)
            note = (
                f"change_ratio={ratio:.2f} >= {new_building_ratio_min} - 신축 추정 "
                "(건축물대장 미매칭, change_ratio 휴리스틱)"
            )
            if not consistent:
                note += (
                    f" - brightness_delta={brightness_delta:+.1f}(T2가 더 어두움)로 "
                    "신축 기대 방향과 불일치, 재확인 권장"
                )
            notes.append(note)
            consistency_flags.append(consistent)
        else:
            change_types.append(EXPANSION_OR_RECONSTRUCTION)
            consistent = brightness_delta is None or bool(brightness_delta >= 0)
            note = (
                f"change_ratio={ratio:.2f} - 부분 변화, 증축/개축 추정 "
                "(건축물대장 미매칭, change_ratio 휴리스틱)"
            )
            if not consistent:
                note += (
                    f" - brightness_delta={brightness_delta:+.1f}(T2가 더 어두움)로 "
                    "증축 기대 방향과 불일치, 재확인 권장"
                )
            notes.append(note)
            consistency_flags.append(consistent)

    out["change_type"] = change_types
    out["classification_note"] = notes
    out["directional_consistency_flag"] = consistency_flags

    logger.info(
        "[BUILDING] 분류 완료: %s (방향 불일치 플래그 %d건)",
        out["change_type"].value_counts(dropna=False).to_dict(),
        sum(1 for f in consistency_flags if f is False),
    )
    return out


def classify_unmatched_changes(
    change_polygons: gpd.GeoDataFrame,
    buildings: gpd.GeoDataFrame,
    demolition_score_min: float = 0.6,
    min_area_m2: float = 50,
) -> gpd.GeoDataFrame:
    """어떤 건물과도 교차하지 않는 change polygon을 DEMOLITION/OTHER_CHANGE로 분류.

    건물 footprint과 교차하지 않는데 변화 강도가 높고 면적이 일정 이상이면
    "무언가 있던 구조물이 사라졌을 가능성"으로 보고 DEMOLITION 후보로 플래그한다.
    단, 현재 건물 layer는 "현재 시점" 스냅샷이라 T1에 실제 건물이 있었는지는
    이미지 변화 강도로만 추정하는 것이며, 확정적 근거가 아니다.

    이 DEMOLITION 판정은 change_polygons에 brightness_delta(postprocess.
    compute_brightness_delta 결과)가 있으면 방향까지 확인한다. 철거라면
    T2가 T1보다 어두워지거나 최소한 밝아지지는 않아야 하는데, 실제로 T2가
    뚜렷하게 더 밝아진(brightness_delta > 0) 경우가 육안검수에서 발견됐다
    (outputs/reports/high_priority_visual_qa.md CHG_00070 - T2에 밝은 신축형
    구조물이 새로 등장, "철거"와 정반대 패턴). 이런 경우는 DEMOLITION이 아니라
    OTHER_CHANGE로 두고 재확인이 필요하다는 근거를 남긴다(건물 footprint에
    아직 반영 안 된 신축일 가능성이 높지만, 밝기 하나로 NEW_BUILDING을
    확정하기엔 근거가 약해 OTHER_CHANGE에 머문다).
    """
    building_union = buildings.geometry.union_all() if len(buildings) else None
    unmatched = change_polygons[
        ~change_polygons.geometry.intersects(building_union) if building_union is not None
        else [True] * len(change_polygons)
    ].copy()

    has_brightness = "brightness_delta" in unmatched.columns
    change_types = []
    notes = []
    for _, row in unmatched.iterrows():
        score = row.get("mean_change_score") or 0
        area = row.get("change_area_m2") or 0
        brightness_delta = row.get("brightness_delta") if has_brightness else None

        if score >= demolition_score_min and area >= min_area_m2:
            if brightness_delta is not None and brightness_delta > 0:
                change_types.append(OTHER_CHANGE)
                notes.append(
                    f"건물과 미교차 + mean_change_score={score:.2f} >= {demolition_score_min} 이지만 "
                    f"brightness_delta={brightness_delta:+.1f}(T2가 더 밝음)로 철거(어두워짐/무변화 예상) "
                    "방향과 불일치 - 건물 미등록 신축 등 다른 원인 가능성, 재확인 필요"
                )
            else:
                change_types.append(DEMOLITION)
                notes.append(
                    f"건물과 미교차 + mean_change_score={score:.2f} >= {demolition_score_min} "
                    "- 철거 추정 (T1 건물 유무 미확인, 이미지 변화 강도 기반 추정)"
                )
        else:
            change_types.append(OTHER_CHANGE)
            notes.append("건물과 미교차, 변화강도/면적 기준 미달 - 토지조성/기타 추정")

    unmatched["change_type"] = change_types
    unmatched["classification_note"] = notes
    unmatched["site_id"] = unmatched["change_id"]
    unmatched["directional_consistency_flag"] = None
    return unmatched
