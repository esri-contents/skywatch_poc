"""REQ03 보상 기준일 판정 로직 (순수 함수, arcpy Feature Class 불필요)."""

from datetime import date

from src.arcpy_pipeline.compensation import (
    POST_BASELINE_PERMITTED,
    POST_BASELINE_UNVERIFIED,
    PRE_BASELINE,
    STRADDLES_BASELINE,
    UNKNOWN,
    evaluate_compensation,
    parse_date,
    refine_with_epochs,
)

BASELINE = date(2023, 6, 1)
T1, T2 = date(2022, 5, 17), date(2024, 5, 31)


def test_use_apr_before_baseline_is_pre_baseline():
    r = evaluate_compensation(
        {"has_register_match": True, "useAprDay": "20220301", "change_type": "NEW_BUILDING"}, BASELINE, T1, T2
    )
    assert r["compensation_status"] == PRE_BASELINE
    assert r["compensation_risk"] == 0.0


def test_use_apr_after_but_permit_before_is_permitted():
    r = evaluate_compensation(
        {"has_register_match": True, "useAprDay": "20231001", "pmsDay": "20230101", "change_type": "NEW_BUILDING"},
        BASELINE, T1, T2,
    )
    assert r["compensation_status"] == POST_BASELINE_PERMITTED


def test_use_apr_and_permit_both_after_is_unverified_highest_risk():
    r = evaluate_compensation(
        {"has_register_match": True, "useAprDay": "20231001", "pmsDay": "20230801", "change_type": "NEW_BUILDING"},
        BASELINE, T1, T2,
    )
    assert r["compensation_status"] == POST_BASELINE_UNVERIFIED
    assert r["compensation_risk"] == 1.0


def test_use_apr_on_expansion_is_not_used_as_evidence():
    """증축/개축은 건물 전체의 오래된 사용승인일로 소급 판정하지 않는다 (REQ03 관련 요구사항)."""
    r = evaluate_compensation(
        {"has_register_match": True, "useAprDay": "20220301", "change_type": "EXPANSION_OR_RECONSTRUCTION"},
        BASELINE, T1, T2,
    )
    assert r["compensation_status"] == STRADDLES_BASELINE


def test_unmatched_window_entirely_after_baseline_is_unverified():
    r = evaluate_compensation({"has_register_match": False}, BASELINE, date(2023, 7, 1), date(2024, 5, 31))
    assert r["compensation_status"] == POST_BASELINE_UNVERIFIED


def test_unmatched_window_entirely_before_baseline_is_pre_baseline():
    r = evaluate_compensation({"has_register_match": False}, BASELINE, date(2021, 1, 1), date(2022, 12, 31))
    assert r["compensation_status"] == PRE_BASELINE


def test_unmatched_window_straddling_baseline_is_straddles():
    r = evaluate_compensation({"has_register_match": False}, BASELINE, T1, T2)
    assert r["compensation_status"] == STRADDLES_BASELINE


def test_matched_without_use_apr_is_unknown():
    r = evaluate_compensation({"has_register_match": True, "useAprDay": None}, BASELINE, T1, T2)
    assert r["compensation_status"] == UNKNOWN


def test_refine_narrows_straddling_case_to_pre_baseline():
    base = evaluate_compensation({"has_register_match": False}, BASELINE, T1, T2)
    refined = refine_with_epochs(base, [(T1, date(2023, 1, 1), True)], BASELINE)
    assert refined["compensation_status"] == PRE_BASELINE


def test_refine_narrows_straddling_case_to_unverified():
    base = evaluate_compensation({"has_register_match": False}, BASELINE, T1, T2)
    refined = refine_with_epochs(base, [(date(2023, 7, 1), T2, True)], BASELINE)
    assert refined["compensation_status"] == POST_BASELINE_UNVERIFIED


def test_refine_with_no_hits_returns_base_unchanged():
    base = evaluate_compensation({"has_register_match": False}, BASELINE, T1, T2)
    refined = refine_with_epochs(base, [(T1, T2, False)], BASELINE)
    assert refined == base


def test_parse_date_formats():
    assert parse_date("20230601") == date(2023, 6, 1)
    assert parse_date("2023-06-01") == date(2023, 6, 1)
    assert parse_date(None) is None
    assert parse_date("") is None
