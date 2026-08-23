"""건축물 변화유형 규칙기반 분류.

중요한 데이터 한계: 현재 건물 footprint(VWorld)는 "현재 시점" 단일 스냅샷이며
T1(2022) 시점의 건물 상태를 별도로 갖고 있지 않다. 따라서 NEW_BUILDING과
EXPANSION_OR_RECONSTRUCTION은 change_ratio 크기로 근사 구분하는 휴리스틱이며,
확정적 판정이 아니다. 건축물대장의 사용승인일을 확보하면 (아직 미확보,
README 참고) 사용승인일이 T1~T2 사이인지로 훨씬 정확하게 구분할 수 있다.
이 분류기는 "현장확인이 필요한 후보"를 줄이기 위한 것이지 최종 판정이 아니다.
"""

from __future__ import annotations

import logging

import geopandas as gpd

logger = logging.getLogger("classify")

NEW_BUILDING = "NEW_BUILDING"
DEMOLITION = "DEMOLITION"
EXPANSION_OR_RECONSTRUCTION = "EXPANSION_OR_RECONSTRUCTION"
OTHER_CHANGE = "OTHER_CHANGE"


def classify_building_changes(
    overlaid: gpd.GeoDataFrame,
    new_building_ratio_min: float = 0.5,
    demolition_score_min: float = 0.6,
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
        change_type, classification_note 컬럼이 추가된 GeoDataFrame.
    """
    out = overlaid.copy()
    change_types = []
    notes = []

    for _, row in out.iterrows():
        ratio = row["change_ratio"]
        near = row["near_change"]

        if ratio == 0 and near:
            change_types.append(OTHER_CHANGE)
            notes.append("건물 본체 미교차, 버퍼 내 변화만 존재")
        elif ratio == 0:
            change_types.append(None)
            notes.append("건물과 무관 (change_ratio=0)")
        elif ratio >= new_building_ratio_min:
            change_types.append(NEW_BUILDING)
            notes.append(
                f"change_ratio={ratio:.2f} >= {new_building_ratio_min} - 신축 추정 "
                "(건축물대장 사용승인일 확보 전까지는 근사 판정)"
            )
        else:
            change_types.append(EXPANSION_OR_RECONSTRUCTION)
            notes.append(f"change_ratio={ratio:.2f} - 부분 변화, 증축/개축 추정")

    out["change_type"] = change_types
    out["classification_note"] = notes

    logger.info(
        "[BUILDING] 분류 완료: %s",
        out["change_type"].value_counts(dropna=False).to_dict(),
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
    """
    building_union = buildings.geometry.union_all() if len(buildings) else None
    unmatched = change_polygons[
        ~change_polygons.geometry.intersects(building_union) if building_union is not None
        else [True] * len(change_polygons)
    ].copy()

    change_types = []
    notes = []
    for _, row in unmatched.iterrows():
        score = row.get("mean_change_score") or 0
        area = row.get("change_area_m2") or 0
        if score >= demolition_score_min and area >= min_area_m2:
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
    return unmatched
