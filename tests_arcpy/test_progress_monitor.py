"""REQ04/REQ09 개발단계 분류 로직 (순수 함수)."""

from src.arcpy_pipeline.progress_monitor import (
    STAGE_CONSTRUCTION,
    STAGE_NEARING,
    STAGE_NOT_STARTED,
    STAGE_SITE_WORK,
    STAGE_STABLE,
    classify_development_stage,
)


def test_no_change_is_not_started():
    stage, _ = classify_development_stage(0.0, 0, 0, 0.0, False)
    assert stage == STAGE_NOT_STARTED


def test_large_area_no_new_building_is_site_work():
    stage, _ = classify_development_stage(0.15, 0, 0, 0.6, True)
    assert stage == STAGE_SITE_WORK


def test_new_buildings_with_high_persistence_is_construction():
    stage, _ = classify_development_stage(0.2, 5, 0, 0.7, True)
    assert stage == STAGE_CONSTRUCTION


def test_activity_stopped_in_latest_epoch_is_stable():
    """과거에는 변화가 활발했으나 최근 구간에서 멈췄으면 완료/중단으로 본다."""
    stage, _ = classify_development_stage(0.2, 5, 0, 0.7, False)
    assert stage == STAGE_STABLE


def test_new_building_low_persistence_is_nearing():
    stage, _ = classify_development_stage(0.06, 2, 0, 0.2, True)
    assert stage == STAGE_NEARING


def test_evidence_string_mentions_key_inputs():
    _, evidence = classify_development_stage(0.2, 5, 1, 0.7, True)
    assert "변화면적비율" in evidence
    assert "신축후보=5" in evidence
    assert "철거후보=1" in evidence
