"""REQ02 불법·무허가 의심 스크리닝 로직 (순수 함수)."""

from src.arcpy_pipeline.illegal_screen import (
    GRADE_A,
    GRADE_B,
    GRADE_C,
    GRADE_NONE,
    evaluate_illegal_signals,
)


def test_unmatched_strong_change_alone_is_moderate():
    r = evaluate_illegal_signals({
        "has_register_match": False, "change_ratio": 0.8, "max_change_score": 0.9,
        "change_type": "NEW_BUILDING", "building_area_m2": 200, "archArea": None,
    })
    assert r["illegal_grade"] == GRADE_B


def test_two_independent_signals_escalate_to_strong():
    r = evaluate_illegal_signals({
        "has_register_match": False, "change_ratio": 0.8, "max_change_score": 0.9,
        "change_type": "NEW_BUILDING", "building_area_m2": 200, "archArea": 100,
        "directional_consistency_flag": 0,
    })
    assert r["illegal_grade"] == GRADE_A
    assert r["area_excess_ratio"] == 2.0


def test_matched_with_normal_area_is_explained():
    r = evaluate_illegal_signals({
        "has_register_match": True, "useAprDay": "20200101", "change_ratio": 0.1,
        "max_change_score": 0.3, "change_type": "EXPANSION_OR_RECONSTRUCTION",
        "building_area_m2": 100, "archArea": 95,
    })
    assert r["illegal_grade"] == GRADE_NONE


def test_matched_but_area_triples_still_flagged():
    """대장 매칭이 있어도 면적이 대장 건축면적을 크게 초과하면 신호로 잡혀야 한다."""
    r = evaluate_illegal_signals({
        "has_register_match": True, "useAprDay": "20200101", "change_ratio": 0.1,
        "max_change_score": 0.3, "change_type": "EXPANSION_OR_RECONSTRUCTION",
        "building_area_m2": 300, "archArea": 100,
    })
    assert r["illegal_grade"] in (GRADE_A, GRADE_B)
    assert r["area_excess_ratio"] == 3.0


def test_no_signals_is_none_or_weak():
    r = evaluate_illegal_signals({
        "has_register_match": False, "change_ratio": 0.05, "max_change_score": 0.2,
        "change_type": "OTHER_CHANGE", "building_area_m2": None, "archArea": None,
    })
    assert r["illegal_grade"] in (GRADE_C, GRADE_NONE)


def test_area_within_tolerance_does_not_trigger_excess_signal():
    r = evaluate_illegal_signals({
        "has_register_match": True, "useAprDay": "20200101", "change_ratio": 0.0,
        "max_change_score": 0.0, "change_type": "EXPANSION_OR_RECONSTRUCTION",
        "building_area_m2": 120, "archArea": 100,
    }, tolerance_ratio=1.3)
    # 120/100 = 1.2 < tolerance 1.3 -> 면적초과 신호 없음
    assert r["area_excess_ratio"] == 1.2
    assert r["illegal_grade"] == GRADE_NONE
