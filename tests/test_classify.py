from datetime import date

import geopandas as gpd
from shapely.geometry import Point

from src.buildings.classify import (
    DEMOLITION,
    EXPANSION_OR_RECONSTRUCTION,
    NEW_BUILDING,
    OTHER_CHANGE,
    classify_building_changes,
    classify_unmatched_changes,
)


def _row(**kwargs):
    base = {"change_ratio": 0.0, "near_change": False, "geometry": Point(0, 0)}
    base.update(kwargs)
    return base


def test_no_change_returns_none_type():
    gdf = gpd.GeoDataFrame([_row(change_ratio=0, near_change=False)])
    out = classify_building_changes(gdf)
    assert out["change_type"].iloc[0] is None


def test_near_change_only_is_other_change():
    gdf = gpd.GeoDataFrame([_row(change_ratio=0, near_change=True)])
    out = classify_building_changes(gdf)
    assert out["change_type"].iloc[0] == OTHER_CHANGE


def test_high_ratio_heuristic_is_new_building():
    gdf = gpd.GeoDataFrame([_row(change_ratio=0.9, near_change=False)])
    out = classify_building_changes(gdf, new_building_ratio_min=0.5)
    assert out["change_type"].iloc[0] == NEW_BUILDING


def test_low_ratio_heuristic_is_expansion():
    gdf = gpd.GeoDataFrame([_row(change_ratio=0.2, near_change=False)])
    out = classify_building_changes(gdf, new_building_ratio_min=0.5)
    assert out["change_type"].iloc[0] == EXPANSION_OR_RECONSTRUCTION


def test_register_evidence_overrides_heuristic_for_new_building():
    # Even with a LOW change_ratio (would heuristically be EXPANSION), a
    # useAprDay inside the T1-T2 window should force NEW_BUILDING.
    gdf = gpd.GeoDataFrame([_row(
        change_ratio=0.2, near_change=False,
        has_register_match=True, useAprDay="20230601",
    )])
    out = classify_building_changes(
        gdf, new_building_ratio_min=0.5,
        t1_date=date(2022, 1, 1), t2_date=date(2024, 1, 1),
    )
    assert out["change_type"].iloc[0] == NEW_BUILDING
    assert "확정" in out["classification_note"].iloc[0]


def test_register_evidence_before_t1_forces_expansion():
    # High change_ratio (would heuristically be NEW_BUILDING) but the
    # register shows the building existed long before T1.
    gdf = gpd.GeoDataFrame([_row(
        change_ratio=0.9, near_change=False,
        has_register_match=True, useAprDay="19950101",
    )])
    out = classify_building_changes(
        gdf, new_building_ratio_min=0.5,
        t1_date=date(2022, 1, 1), t2_date=date(2024, 1, 1),
    )
    assert out["change_type"].iloc[0] == EXPANSION_OR_RECONSTRUCTION


def test_unmatched_high_score_is_demolition_candidate():
    change_polys = gpd.GeoDataFrame([{
        "geometry": Point(10, 10).buffer(1),
        "change_id": "CHG_00001",
        "mean_change_score": 0.8,
        "change_area_m2": 100,
    }])
    buildings = gpd.GeoDataFrame([{"geometry": Point(0, 0).buffer(1)}])
    out = classify_unmatched_changes(change_polys, buildings, demolition_score_min=0.6, min_area_m2=50)
    assert out["change_type"].iloc[0] == DEMOLITION


def test_unmatched_low_score_is_other_change():
    change_polys = gpd.GeoDataFrame([{
        "geometry": Point(10, 10).buffer(1),
        "change_id": "CHG_00001",
        "mean_change_score": 0.1,
        "change_area_m2": 100,
    }])
    buildings = gpd.GeoDataFrame([{"geometry": Point(0, 0).buffer(1)}])
    out = classify_unmatched_changes(change_polys, buildings, demolition_score_min=0.6, min_area_m2=50)
    assert out["change_type"].iloc[0] == OTHER_CHANGE


def test_unmatched_high_score_but_brighter_t2_is_not_demolition():
    # CHG_00070 실측 사례: 건물 미교차 + 고신뢰 점수지만 T2가 더 밝아짐(신축형
    # 패턴) - "철거" 방향과 정반대라 DEMOLITION이 아니라 OTHER_CHANGE여야 한다.
    change_polys = gpd.GeoDataFrame([{
        "geometry": Point(10, 10).buffer(1),
        "change_id": "CHG_00070",
        "mean_change_score": 0.7,
        "change_area_m2": 100,
        "brightness_delta": 15.0,
    }])
    buildings = gpd.GeoDataFrame([{"geometry": Point(0, 0).buffer(1)}])
    out = classify_unmatched_changes(change_polys, buildings, demolition_score_min=0.6, min_area_m2=50)
    assert out["change_type"].iloc[0] == OTHER_CHANGE
    assert "불일치" in out["classification_note"].iloc[0]


def test_unmatched_high_score_and_darker_t2_stays_demolition():
    change_polys = gpd.GeoDataFrame([{
        "geometry": Point(10, 10).buffer(1),
        "change_id": "CHG_00002",
        "mean_change_score": 0.8,
        "change_area_m2": 100,
        "brightness_delta": -10.0,
    }])
    buildings = gpd.GeoDataFrame([{"geometry": Point(0, 0).buffer(1)}])
    out = classify_unmatched_changes(change_polys, buildings, demolition_score_min=0.6, min_area_m2=50)
    assert out["change_type"].iloc[0] == DEMOLITION


def test_heuristic_new_building_with_brighter_t2_is_consistent():
    gdf = gpd.GeoDataFrame([_row(change_ratio=0.9, near_change=False, brightness_delta=20.0)])
    out = classify_building_changes(gdf, new_building_ratio_min=0.5)
    assert out["change_type"].iloc[0] == NEW_BUILDING
    assert out["directional_consistency_flag"].iloc[0] == True  # noqa: E712 (numpy bool, not python bool)


def test_heuristic_new_building_with_darker_t2_is_flagged_inconsistent():
    # CHG_00029/CHG_00068 실측 사례: 신축/증축 추정인데 T2가 오히려 어두워짐.
    # 라벨은 그대로 두되(밝기 하나로 뒤집기엔 근거 약함) 재확인 플래그를 켠다.
    gdf = gpd.GeoDataFrame([_row(change_ratio=0.9, near_change=False, brightness_delta=-8.0)])
    out = classify_building_changes(gdf, new_building_ratio_min=0.5)
    assert out["change_type"].iloc[0] == NEW_BUILDING
    assert out["directional_consistency_flag"].iloc[0] == False  # noqa: E712 (numpy bool, not python bool)
    assert "불일치" in out["classification_note"].iloc[0]


def test_register_based_classification_has_no_consistency_flag():
    # 대장 근거(useAprDay)로 확정된 건은 밝기 방향과 무관하게 flag=None(해당 없음).
    gdf = gpd.GeoDataFrame([_row(
        change_ratio=0.2, near_change=False, brightness_delta=-8.0,
        has_register_match=True, useAprDay="20230601",
    )])
    out = classify_building_changes(
        gdf, new_building_ratio_min=0.5,
        t1_date=date(2022, 1, 1), t2_date=date(2024, 1, 1),
    )
    assert out["change_type"].iloc[0] == NEW_BUILDING
    assert out["directional_consistency_flag"].iloc[0] is None
