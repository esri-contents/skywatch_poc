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
        "mean_change_score": 0.8,
        "change_area_m2": 100,
    }])
    buildings = gpd.GeoDataFrame([{"geometry": Point(0, 0).buffer(1)}])
    out = classify_unmatched_changes(change_polys, buildings, demolition_score_min=0.6, min_area_m2=50)
    assert out["change_type"].iloc[0] == DEMOLITION


def test_unmatched_low_score_is_other_change():
    change_polys = gpd.GeoDataFrame([{
        "geometry": Point(10, 10).buffer(1),
        "mean_change_score": 0.1,
        "change_area_m2": 100,
    }])
    buildings = gpd.GeoDataFrame([{"geometry": Point(0, 0).buffer(1)}])
    out = classify_unmatched_changes(change_polys, buildings, demolition_score_min=0.6, min_area_m2=50)
    assert out["change_type"].iloc[0] == OTHER_CHANGE
