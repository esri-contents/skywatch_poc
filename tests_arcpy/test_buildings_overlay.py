"""건물 Overlay/건축물대장 조인/분류/우선순위 점수화 통합 테스트 (arcpy 필요).

change_detect의 래스터 산출물 대신 손으로 만든 change_polygons Feature
Class를 입력으로 써서 raster 단계 없이 빠르게(수 초) 검증한다 - 이 모듈들의
책임은 "주어진 변화 폴리곤과 건물을 어떻게 연계·분류하는가"이지 변화탐지
알고리즘 자체가 아니기 때문이다 (알고리즘 검증은 test_change_detect_algorithms.py).
"""

import json
from datetime import date

import arcpy
import pytest

from src.arcpy_pipeline import buildings
from src.arcpy_pipeline.env import ensure_gdb, fc_path

SR = arcpy.SpatialReference(5186)
ORIGIN_X, ORIGIN_Y = 189000.0, 557000.0


def _box(x0, y0, x1, y1):
    return arcpy.Polygon(arcpy.Array([
        arcpy.Point(ORIGIN_X + x0, ORIGIN_Y + y0),
        arcpy.Point(ORIGIN_X + x1, ORIGIN_Y + y0),
        arcpy.Point(ORIGIN_X + x1, ORIGIN_Y + y1),
        arcpy.Point(ORIGIN_X + x0, ORIGIN_Y + y1),
    ]), SR)


@pytest.fixture
def gdb(tmp_path):
    return ensure_gdb(str(tmp_path / "test.gdb"))


@pytest.fixture
def change_fc(gdb):
    """change polygon 2개: CHG_1(신축 후보, 밝아짐), CHG_2(철거 후보, 어두워짐)."""
    fc = fc_path(gdb, "change_polygons")
    arcpy.management.CreateFeatureclass(str(gdb), "change_polygons", "POLYGON", spatial_reference=SR)
    for name, ftype, length in [
        ("change_id", "TEXT", 20), ("change_area_m2", "DOUBLE", None),
        ("mean_change_score", "DOUBLE", None), ("max_change_score", "DOUBLE", None),
        ("t1_date", "TEXT", 12), ("t2_date", "TEXT", 12), ("method", "TEXT", 20),
        ("brightness_delta", "DOUBLE", None),
    ]:
        arcpy.management.AddField(fc, name, ftype, field_length=length)

    rows = [
        (_box(0, 0, 100, 100), "CHG_1", 10000.0, 0.9, 0.95, "2022-05-17", "2024-05-31", "ensemble", 500.0),
        (_box(500, 500, 560, 560), "CHG_2", 3600.0, 0.8, 0.85, "2022-05-17", "2024-05-31", "ensemble", -400.0),
    ]
    with arcpy.da.InsertCursor(
        fc, ["SHAPE@", "change_id", "change_area_m2", "mean_change_score", "max_change_score",
             "t1_date", "t2_date", "method", "brightness_delta"]
    ) as cur:
        for r in rows:
            cur.insertRow(r)
    return fc


@pytest.fixture
def buildings_fc(gdb):
    """건물 3개: b1(CHG_1 안, 대장 매칭 예정), b2(CHG_1 안, 미매칭),
    b3(어느 change와도 무관, 미매칭)."""
    fc = fc_path(gdb, "buildings")
    arcpy.management.CreateFeatureclass(str(gdb), "buildings", "POLYGON", spatial_reference=SR)
    for name, ftype, length in [("pnu", "TEXT", 19), ("rn_cd", "TEXT", 12), ("buld_no", "TEXT", 20)]:
        arcpy.management.AddField(fc, name, ftype, field_length=length)

    rows = [
        (_box(10, 10, 30, 30), "4128010100108150000", "417401234567", "10-2"),
        (_box(40, 40, 60, 60), "4128010100108160000", "417401234567", "12"),
        (_box(900, 900, 920, 920), "4128010100108180000", "417401234567", "7"),
    ]
    with arcpy.da.InsertCursor(fc, ["SHAPE@", "pnu", "rn_cd", "buld_no"]) as cur:
        for r in rows:
            cur.insertRow(r)
    return fc


@pytest.fixture
def register_json(tmp_path):
    items = [{
        "pnu": "4128010100208150000", "naRoadCd": "0034017401234",
        "naMainBun": "10", "naSubBun": "2",
        "mainPurpsCdNm": "단독주택", "archArea": 80.0, "totArea": 80.0,
        "grndFlrCnt": 2, "ugrndFlrCnt": 0,
        "pmsDay": "20230101", "stcnsDay": "20230201", "useAprDay": "20230601",
    }]
    path = tmp_path / "register.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return str(path)


def test_overlay_computes_change_ratio_and_shared_site_id(gdb, change_fc, buildings_fc):
    out = fc_path(gdb, "overlaid")
    buildings.overlay_buildings_with_changes(buildings_fc, change_fc, out, buffer_m=3.0)
    rows = {r[0]: r for r in arcpy.da.SearchCursor(out, ["pnu", "change_ratio", "site_id", "brightness_delta"])}

    b1 = rows["4128010100108150000"]
    b2 = rows["4128010100108160000"]
    b3 = rows["4128010100108180000"]

    assert b1[1] == pytest.approx(1.0)  # 건물 전체가 CHG_1 안에 있음
    assert b2[1] == pytest.approx(1.0)
    assert b3[1] == 0.0  # 어느 change와도 무관

    assert b1[2] == b2[2] == "CHG_1"  # 같은 site로 묶여야 한다
    assert b1[3] == 500.0  # CHG_1의 brightness_delta가 그대로 전달됨


def test_register_join_and_admin_uncertainty(gdb, change_fc, buildings_fc, register_json):
    out = fc_path(gdb, "overlaid")
    buildings.overlay_buildings_with_changes(buildings_fc, change_fc, out, buffer_m=3.0)
    stats = buildings.join_building_register(out, register_json)
    assert stats["matched"] == 1
    assert stats["pnu"] == 1

    buildings.compute_administrative_uncertainty(out, date(2022, 5, 17), date(2024, 5, 31))
    au = {r[0]: r[1] for r in arcpy.da.SearchCursor(out, ["pnu", "administrative_uncertainty"])}
    assert au["4128010100108150000"] == pytest.approx(0.1)   # 사용승인일이 T1~T2 사이
    assert au["4128010100108160000"] == 1.0                  # 미매칭


def test_classification_uses_register_evidence_over_heuristic(gdb, change_fc, buildings_fc, register_json):
    out = fc_path(gdb, "overlaid")
    buildings.overlay_buildings_with_changes(buildings_fc, change_fc, out, buffer_m=3.0)
    buildings.join_building_register(out, register_json)
    buildings.compute_administrative_uncertainty(out, date(2022, 5, 17), date(2024, 5, 31))
    buildings.classify_building_changes(
        out, new_building_ratio_min=0.5, t1_date=date(2022, 5, 17), t2_date=date(2024, 5, 31)
    )
    ctypes = {r[0]: r[1] for r in arcpy.da.SearchCursor(out, ["pnu", "change_type"])}
    assert ctypes["4128010100108150000"] == "NEW_BUILDING"  # 대장 확정
    assert ctypes["4128010100108160000"] == "NEW_BUILDING"  # 휴리스틱 (ratio>=0.5)
    assert ctypes["4128010100108180000"] is None            # 무관


def test_unmatched_changes_classified_and_merge_produces_all_candidates(
    gdb, change_fc, buildings_fc, register_json
):
    overlaid = fc_path(gdb, "overlaid")
    buildings.overlay_buildings_with_changes(buildings_fc, change_fc, overlaid, buffer_m=3.0)
    buildings.join_building_register(overlaid, register_json)
    buildings.compute_administrative_uncertainty(overlaid, date(2022, 5, 17), date(2024, 5, 31))
    buildings.classify_building_changes(
        overlaid, new_building_ratio_min=0.5, t1_date=date(2022, 5, 17), t2_date=date(2024, 5, 31)
    )

    unmatched = fc_path(gdb, "unmatched")
    buildings.classify_unmatched_changes(change_fc, buildings_fc, unmatched)
    unmatched_types = [r[0] for r in arcpy.da.SearchCursor(unmatched, ["change_type"])]
    # CHG_2는 어떤 건물과도 안 겹치고 mean_score=0.8 >= 0.6, area=3600>=50 -> DEMOLITION 후보
    # (brightness_delta=-400 < 0 이므로 방향 일치, OTHER_CHANGE로 되돌려지지 않는다)
    assert "DEMOLITION" in unmatched_types

    merged = fc_path(gdb, "results")
    buildings.merge_results(overlaid, unmatched, merged)
    n = int(arcpy.management.GetCount(merged)[0])
    # 건물연계: b1(NEW_BUILDING) + b2(NEW_BUILDING) = 2, b3는 change_type=None -> 제외
    # 미교차: CHG_2(DEMOLITION) = 1  => 총 3
    assert n == 3

    counts = buildings.compute_priority_score(
        merged,
        weights={"change_confidence": 0.4, "change_ratio": 0.3,
                 "administrative_uncertainty": 0.2, "building_relevance": 0.1},
        high_threshold=0.7, medium_threshold=0.4,
    )
    assert sum(counts.values()) == n
    tiers = {r[0] for r in arcpy.da.SearchCursor(merged, ["inspection_priority"])}
    assert tiers <= {"HIGH", "MEDIUM", "LOW"}
