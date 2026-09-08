"""현장조사 우선순위 점수 산정.

administrative_uncertainty: STEP 13(`src/buildings/validation.py`의
compute_administrative_uncertainty)이 건축물대장 매칭 여부와 사용승인일로
이미 계산해 넘겨준 값을 그대로 쓴다 - 대장 미매칭이면 1.0(완전 불확실),
매칭+사용승인일이 T1~T2 사이면 0.1(인허가로 설명됨), 매칭+구간 밖이면 0.6.
이 컬럼이 없는 입력(건축물대장을 아예 넘기지 않은 실행)에 한해서만
1.0으로 fallback한다 - 그 fallback 경로는 아래 compute_priority_score의
if/else에서 처리한다.
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

    # buildings/validation.py에서 미리 계산된 값이 있으면 그대로 사용하고,
    # 없으면(건축물대장 미확보/미매칭) "행정적으로 완전 미설명"으로 취급한다.
    if "administrative_uncertainty" not in out.columns:
        out["administrative_uncertainty"] = 1.0
    else:
        out["administrative_uncertainty"] = out["administrative_uncertainty"].fillna(1.0)

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
