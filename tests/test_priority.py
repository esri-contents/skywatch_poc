import geopandas as gpd
import pytest
from shapely.geometry import Point

from src.scoring.priority import compute_priority_score


def _gdf(**kwargs):
    row = {
        "change_type": "NEW_BUILDING", "max_change_score": 0.5,
        "change_ratio": 0.5, "geometry": Point(0, 0),
    }
    row.update(kwargs)
    return gpd.GeoDataFrame([row])


def test_high_confidence_high_ratio_is_high_priority():
    gdf = _gdf(max_change_score=1.0, change_ratio=1.0, change_type="NEW_BUILDING")
    out = compute_priority_score(gdf)
    assert out["inspection_priority"].iloc[0] == "HIGH"


def test_low_confidence_low_ratio_other_change_is_low_priority():
    gdf = _gdf(max_change_score=0.0, change_ratio=0.0, change_type="OTHER_CHANGE")
    out = compute_priority_score(gdf)
    # administrative_uncertainty defaults to 1.0 (no register info), which
    # alone contributes weights['administrative_uncertainty'] to the score.
    assert out["priority_score"].iloc[0] < 0.5
    assert out["inspection_priority"].iloc[0] in ("LOW", "MEDIUM")


def test_existing_administrative_uncertainty_is_respected():
    gdf = _gdf(max_change_score=1.0, change_ratio=1.0)
    gdf["administrative_uncertainty"] = 0.1  # already explained administratively
    out = compute_priority_score(gdf)
    assert out["administrative_uncertainty"].iloc[0] == 0.1
    # Score should be lower than the fully-unexplained case with identical other inputs.
    gdf_unexplained = _gdf(max_change_score=1.0, change_ratio=1.0)
    out_unexplained = compute_priority_score(gdf_unexplained)
    assert out["priority_score"].iloc[0] < out_unexplained["priority_score"].iloc[0]


def test_weights_sum_produces_expected_score():
    gdf = _gdf(max_change_score=1.0, change_ratio=1.0, change_type="NEW_BUILDING")
    weights = {"change_confidence": 0.4, "change_ratio": 0.3, "administrative_uncertainty": 0.2, "building_relevance": 0.1}
    out = compute_priority_score(gdf, weights=weights)
    # confidence=1.0, change_ratio=1.0, admin_uncertainty=1.0 (no register), building_relevance=1.0 (NEW_BUILDING)
    expected = 0.4 * 1.0 + 0.3 * 1.0 + 0.2 * 1.0 + 0.1 * 1.0
    assert out["priority_score"].iloc[0] == pytest.approx(expected)
