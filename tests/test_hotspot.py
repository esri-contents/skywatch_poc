"""hotspot.py 단위 테스트.

TEST DATA / SYNTHETIC DATA: 아래 polygon/연도는 이 테스트에서 만든 합성 데이터이며 실제 단양 매포
분석 결과가 아니다.
"""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, box

from src.hotspot import (
    HOTSPOT_COLUMNS, POLYGON_COLUMNS, ROI_COLUMNS, build_hotspots, build_roi, empty_polygons,
    pair_change_summary, pair_list, rank_hotspots, recommend_acquisition_years,
)

CRS = "EPSG:5186"


def _polys(rows):
    """rows: (geom, year_from, year_to, kind, area, score)"""
    return gpd.GeoDataFrame(
        [{"pair": f"{a}_{b}", "year_from": a, "year_to": b, "pair_kind": k,
          "change_area_m2": ar, "mean_change_score": sc, "geometry": g} for g, a, b, k, ar, sc in rows],
        geometry="geometry", crs=CRS,
    )


def test_pair_list_step_and_long_term():
    pairs = pair_list([2016, 2018, 2020, 2026], True, [[2016, 2026], [2018, 2020], [2000, 2026]])
    labels = [(p["pair"], p["kind"]) for p in pairs]
    assert labels == [("2016_2018", "step"), ("2018_2020", "step"), ("2020_2026", "step"), ("2016_2026", "long_term")]


def test_pair_list_unsorted_and_no_consecutive():
    pairs = pair_list([2026, 2016], consecutive=False, long_term=[[2016, 2026]])
    assert [p["pair"] for p in pairs] == ["2016_2026"]


def test_build_hotspots_clusters_nearby_polygons():
    a, b = box(0, 0, 100, 100), box(120, 0, 220, 100)      # 20m 간격 -> 군집
    far = box(1000, 1000, 1100, 1100)
    polys = _polys([
        (a, 2016, 2018, "step", 10000, 0.8), (b, 2018, 2020, "step", 10000, 0.6), (far, 2018, 2020, "step", 10000, 0.9),
    ])
    hs = build_hotspots(polys, cluster_distance_m=60)
    assert len(hs) == 2
    big = hs.loc[hs["change_area_m2"].idxmax()]
    assert big["change_area_m2"] == pytest.approx(20000.0)
    assert big["first_year"] == 2018 and big["last_year"] == 2020 and big["change_frequency"] == 2
    assert big["change_score"] == pytest.approx(0.7)
    assert big["change_pairs"] == "2016_2018,2018_2020"
    assert list(hs.columns) == HOTSPOT_COLUMNS


def test_hotspot_area_is_union_not_double_counted():
    g = box(0, 0, 100, 100)
    polys = _polys([(g, 2016, 2018, "step", 10000, 0.7), (g, 2016, 2026, "long_term", 10000, 0.7)])
    hs = build_hotspots(polys, 60)
    assert len(hs) == 1 and hs["change_area_m2"].iloc[0] == pytest.approx(10000.0)
    assert hs["change_frequency"].iloc[0] == 1  # step 쌍만 빈도에 반영


def test_hotspot_long_term_only_falls_back():
    polys = _polys([(box(0, 0, 100, 100), 2016, 2026, "long_term", 10000, 0.7)])
    hs = build_hotspots(polys, 60)
    assert hs["first_year"].iloc[0] == 2026 and hs["change_frequency"].iloc[0] == 1


def test_min_hotspot_area_filters_small():
    polys = _polys([(box(0, 0, 10, 10), 2016, 2018, "step", 100, 0.7)])
    assert build_hotspots(polys, 60, min_hotspot_area_m2=1000).empty


def test_distance_to_railway():
    polys = _polys([(box(0, 0, 100, 100), 2016, 2018, "step", 10000, 0.7)])
    rail = gpd.GeoDataFrame(geometry=[LineString([(300, -50), (300, 200)])], crs=CRS)
    assert build_hotspots(polys, 60, railway=rail)["distance_to_railway_m"].iloc[0] == pytest.approx(200.0)
    assert build_hotspots(polys, 60)["distance_to_railway_m"].iloc[0] is None


def test_zero_polygons_everywhere_is_not_an_error():
    polys = empty_polygons(CRS)
    assert list(polys.columns) == POLYGON_COLUMNS
    hs = rank_hotspots(build_hotspots(polys, 60), 5, 0, 0)
    roi = build_roi(hs, 300, 500)
    assert hs.empty and roi.empty and list(roi.columns) == ROI_COLUMNS
    pairs = pair_list([2016, 2018, 2020])
    summary = pair_change_summary(polys, pairs, hs)
    assert (summary["polygon_count"] == 0).all() and (summary["change_area_m2"] == 0).all()
    rec = recommend_acquisition_years(summary, [2016, 2018, 2020])
    assert rec["active_pairs"] == []
    assert [y["year"] for y in rec["years"]] == [2016, 2020]
    assert "유의미한 변화가 검출되지 않음" in rec["years"][0]["reason"]


def test_rank_and_recommend_thresholds():
    polys = _polys([
        (box(0, 0, 100, 100), 2016, 2018, "step", 10000, 0.8),
        (box(1000, 0, 1050, 50), 2016, 2018, "step", 2500, 0.8),
        (box(2000, 0, 2100, 100), 2016, 2018, "step", 10000, 0.3),
    ])
    hs = rank_hotspots(build_hotspots(polys, 60), top_n=5, recommend_min_area_m2=5000, recommend_min_score=0.5)
    assert hs["hotspot_id"].tolist() == ["HS_01", "HS_02", "HS_03"]
    assert hs["recommended_for_highres"].tolist() == [True, False, False]
    top1 = rank_hotspots(build_hotspots(polys, 60), top_n=1, recommend_min_area_m2=0, recommend_min_score=0)
    assert int(top1["recommended_for_highres"].sum()) == 1


def test_roi_buffer_min_max_and_overlap():
    polys = _polys([
        (box(0, 0, 100, 100), 2016, 2018, "step", 10000, 0.8),
        (box(150, 0, 250, 100), 2016, 2018, "step", 8000, 0.8),
        (box(5000, 0, 5100, 100), 2016, 2018, "step", 10000, 0.8),
        (box(9000, 0, 11000, 100), 2016, 2018, "step", 200000, 0.8),
    ])
    hs = rank_hotspots(build_hotspots(polys, 10), 10, 0, 0)
    roi = build_roi(hs, buffer_m=300, min_size_m=500, overlap_merge=0.5, max_size_m=1000)
    assert (roi["roi_size_m"] <= 1000).all()
    small = roi[roi["roi_size_m"] < 1000].iloc[0]
    assert small["roi_size_m"] == pytest.approx(700.0)         # 100m hotspot + 2*300m buffer
    assert roi["roi_clipped"].sum() == 1                        # 2km 길이 hotspot은 상한으로 잘림
    assert len(roi) == len(set(roi["hotspot_id"]))
    # 150m 떨어진 인접 hotspot ROI는 겹침이 커서 생략되지 않거나(동일 hotspot으로 군집) 중복 ROI가 없어야 한다
    assert roi.geometry.union_all().area <= roi.geometry.area.sum() + 1e-6


def test_roi_min_size_expands_small_hotspot():
    polys = _polys([(box(0, 0, 50, 50), 2016, 2018, "step", 2500, 0.8)])
    hs = rank_hotspots(build_hotspots(polys, 10), 5, 0, 0)
    roi = build_roi(hs, buffer_m=10, min_size_m=500)
    assert roi["roi_size_m"].iloc[0] == pytest.approx(500.0)
    assert roi["roi_area_km2"].iloc[0] == pytest.approx(0.25)


def test_acquisition_years_bracket_change():
    summary = pd.DataFrame([
        {"year_from": 2016, "year_to": 2018, "kind": "step", "change_area_m2": 0.0, "polygon_count": 0, "hotspot_count": 0},
        {"year_from": 2018, "year_to": 2020, "kind": "step", "change_area_m2": 1000.0, "polygon_count": 3, "hotspot_count": 1},
        {"year_from": 2020, "year_to": 2022, "kind": "step", "change_area_m2": 9000.0, "polygon_count": 9, "hotspot_count": 2},
        {"year_from": 2022, "year_to": 2024, "kind": "step", "change_area_m2": 300.0, "polygon_count": 1, "hotspot_count": 0},
        {"year_from": 2016, "year_to": 2024, "kind": "long_term", "change_area_m2": 20000.0, "polygon_count": 5, "hotspot_count": 2},
    ])
    rec = recommend_acquisition_years(summary, [2016, 2018, 2020, 2022, 2024], active_fraction_of_max=0.25)
    years = {y["year"]: y["role"] for y in rec["years"]}
    assert rec["active_pairs"] == ["2020_2022"]                 # long_term 쌍/소규모 변화는 active 아님
    assert years[2020] == "변화 전 baseline" and years[2022] == "변화 검출/진행"
    assert years[2021].startswith("중간 연도") and years[2024] == "변화 후 안정화 확인"
    assert any("2015" in n for n in rec["notes"])               # Sentinel-2 2015 이전 한계 명시


def test_acquisition_years_without_step_pairs():
    summary = pd.DataFrame([
        {"year_from": 2016, "year_to": 2026, "kind": "long_term", "change_area_m2": 5.0, "polygon_count": 1, "hotspot_count": 1},
    ])
    rec = recommend_acquisition_years(summary, [2016, 2026])
    assert rec["years"] == [] and any("Phase B" in n for n in rec["notes"])
