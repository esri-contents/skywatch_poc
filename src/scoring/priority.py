"""현장조사 우선순위 점수 산정.

administrative_uncertainty: 건축물대장/인허가 정보가 아직 없어(README 참고)
모든 변화에 대해 "행정적으로 설명 가능한지" 확인이 불가능한 상태다. 따라서
현재는 모든 후보에 대해 1.0(완전 불확실)로 고정한다. 건축물대장 확보 후
사용승인일/허가일이 T1~T2 사이에 있으면 0에 가깝게(행정적으로 설명됨)
낮춰야 한다.
"""

from __future__ import annotations

import geopandas as gpd

DEFAULT_WEIGHTS = {
    "change_confidence": 0.40,
    "change_ratio": 0.30,
    "administrative_uncertainty": 0.20,
    "building_relevance": 0.10,
}


def compute_priority_score(
    classified: gpd.GeoDataFrame,
    weights: dict[str, float] = DEFAULT_WEIGHTS,
    high_threshold: float = 0.7,
    medium_threshold: float = 0.4,
) -> gpd.GeoDataFrame:
    """규칙 기반 priority_score 및 HIGH/MEDIUM/LOW 등급을 계산한다.

    Args:
        classified: classify.classify_building_changes() 또는
            classify_unmatched_changes() 결과 (change_type 컬럼 포함).
        weights: priority_score 가중치.
        high_threshold: 이 이상이면 HIGH.
        medium_threshold: 이 이상이면 MEDIUM, 미만이면 LOW.

    Returns:
        confidence, administrative_uncertainty, building_relevance,
        priority_score, inspection_priority 컬럼이 추가된 GeoDataFrame.
    """
    out = classified.copy()

    score_col = "max_change_score" if "max_change_score" in out.columns else "mean_change_score"
    out["confidence"] = out[score_col].fillna(0.0).clip(0, 1)

    if "change_ratio" not in out.columns:
        out["change_ratio"] = 0.0
    out["change_ratio"] = out["change_ratio"].fillna(0.0).clip(0, 1)

    # 건축물대장 미확보 상태 - 모든 후보를 "행정적으로 미설명"으로 취급 (README 참고)
    out["administrative_uncertainty"] = 1.0

    out["building_relevance"] = out["change_type"].apply(
        lambda t: 1.0 if t in ("NEW_BUILDING", "EXPANSION_OR_RECONSTRUCTION", "DEMOLITION") else 0.3
    )

    out["priority_score"] = (
        weights["change_confidence"] * out["confidence"]
        + weights["change_ratio"] * out["change_ratio"]
        + weights["administrative_uncertainty"] * out["administrative_uncertainty"]
        + weights["building_relevance"] * out["building_relevance"]
    ).round(4)

    def _tier(score: float) -> str:
        if score >= high_threshold:
            return "HIGH"
        if score >= medium_threshold:
            return "MEDIUM"
        return "LOW"

    out["inspection_priority"] = out["priority_score"].apply(_tier)
    return out
