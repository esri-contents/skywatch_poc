"""건물 단위 변화의 공간적 군집성 검증 (Global Moran's I, Getis-Ord Gi*).

solafune-sentinel2-change 프로젝트(robust CVA + Moran's I/Gi* 공간통계
파이프라인)에서 방법론을 차용해 이 PoC의 building_change_results에 적용한다.

목적: `outputs/reports/poc_summary.md`에서 육안 검수로 발견한 "HIGH 32건이
실제로는 11개 현장에 몰려 있다"는 관찰(site_id 기준 group-by)을 통계적으로도
뒷받침한다. 변화가 실제로 공간적으로 구조화된 패턴(대규모 공사장 등)인지,
아니면 산발적 노이즈인지 Global Moran's I로 먼저 확인하고, Getis-Ord Gi*로
어느 건물들이 통계적으로 유의한 hotspot(변화 집중 구역)에 속하는지 표시한다.

Gi*는 solafune README의 관례를 따라 binary weights를, Moran's I는
row-standardized weights를 쓴다(두 통계량의 정의가 다르기 때문 - 서로
바꿔 쓰면 안 된다).
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
from esda.getisord import G_Local
from esda.moran import Moran
from libpysal.weights import KNN

logger = logging.getLogger("spatial_statistics")


def compute_global_moran(
    gdf: gpd.GeoDataFrame,
    value_col: str = "priority_score",
    k: int = 8,
    permutations: int = 999,
    seed: int = 42,
) -> dict:
    """value_col에 대한 Global Moran's I (row-standardized KNN weights).

    Args:
        gdf: point/polygon geometry를 가진 GeoDataFrame (건물 change 결과 등).
        value_col: 공간적 자기상관을 검정할 수치 컬럼.
        k: KNN 이웃 수.
        permutations: permutation 검정 반복 횟수.
        seed: 재현성을 위한 시드 (esda Moran은 자체 seed 인자가 없어 전역
            np.random 시드를 잠시 고정한다).

    Returns:
        {"I", "p_sim", "z_sim", "n", "k"} - n이 k+1 미만이면 계산하지 않고
        None 값으로 채워 반환한다.
    """
    pts = gdf[gdf[value_col].notna()].copy()
    if len(pts) < k + 1:
        logger.warning(
            "[SPATIAL] Moran's I 계산에 표본이 부족합니다 (n=%d, k=%d) - 건너뜀", len(pts), k
        )
        return {"I": None, "p_sim": None, "z_sim": None, "n": len(pts), "k": k}

    w = KNN.from_dataframe(pts, k=k)
    w.transform = "r"
    values = pts[value_col].to_numpy()

    rng_state = np.random.get_state()
    np.random.seed(seed)
    try:
        moran = Moran(values, w, permutations=permutations)
    finally:
        np.random.set_state(rng_state)

    result = {
        "I": float(moran.I), "p_sim": float(moran.p_sim), "z_sim": float(moran.z_sim),
        "n": len(pts), "k": k,
    }
    logger.info(
        "[SPATIAL] Global Moran's I=%.4f p_sim=%.4f (n=%d, k=%d) - %s",
        result["I"], result["p_sim"], result["n"], k,
        "공간적 군집 유의(p<0.05)" if result["p_sim"] < 0.05 else "유의하지 않음",
    )
    return result


def compute_gi_star(
    gdf: gpd.GeoDataFrame,
    value_col: str = "priority_score",
    k: int = 8,
    permutations: int = 999,
    seed: int = 42,
) -> gpd.GeoDataFrame:
    """건물별 Getis-Ord Gi* hotspot 분류 (binary KNN weights, 90/95/99% 신뢰수준).

    Args:
        gdf: 건물 change 결과 GeoDataFrame.
        value_col: hotspot을 계산할 수치 컬럼.
        k: KNN 이웃 수.
        permutations: permutation 검정 반복 횟수.
        seed: 재현성을 위한 시드.

    Returns:
        gi_zscore, gi_pvalue, gi_class 컬럼이 추가된 gdf 사본. 표본이 부족하면
        세 컬럼 모두 None으로 채워 반환한다.
        gi_class: HOT_99/HOT_95/HOT_90/NOT_SIG/COLD_90/COLD_95/COLD_99.
    """
    out = gdf.copy()
    pts = out[out[value_col].notna()].copy()
    if len(pts) < k + 1:
        logger.warning(
            "[SPATIAL] Gi* 계산에 표본이 부족합니다 (n=%d, k=%d) - 건너뜀", len(pts), k
        )
        out["gi_zscore"] = None
        out["gi_pvalue"] = None
        out["gi_class"] = None
        return out

    w = KNN.from_dataframe(pts, k=k)
    w.transform = "b"
    values = pts[value_col].to_numpy()
    gi = G_Local(values, w, star=True, permutations=permutations, seed=seed, alternative="two-sided")

    pts["gi_zscore"] = gi.Zs
    pts["gi_pvalue"] = gi.p_sim
    pts["gi_class"] = [_classify_gi(z, p) for z, p in zip(gi.Zs, gi.p_sim)]

    out["gi_zscore"] = pts["gi_zscore"]
    out["gi_pvalue"] = pts["gi_pvalue"]
    out["gi_class"] = pts["gi_class"]

    n_hot = pts["gi_class"].astype(str).str.startswith("HOT").sum()
    logger.info(
        "[SPATIAL] Gi* 완료: hotspot(HOT_90 이상) %d개 / 계산 대상 %d개 (전체 %d개)",
        n_hot, len(pts), len(out),
    )
    return out


def _classify_gi(z: float, p: float) -> str:
    if p >= 0.10:
        return "NOT_SIG"
    level = "99" if p < 0.01 else ("95" if p < 0.05 else "90")
    return f"{'HOT' if z > 0 else 'COLD'}_{level}"
