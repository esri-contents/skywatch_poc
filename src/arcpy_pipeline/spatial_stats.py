"""공간통계 (arcpy.stats 네이티브) - Global Moran's I / Getis-Ord Gi*.

Baseline은 esda/libpysal(파이썬 오픈소스 공간통계 라이브러리)을 썼다.
ArcGIS Pro 파이썬 환경에는 그 라이브러리가 없지만, **arcpy.stats에 동일한
통계량을 계산하는 네이티브 도구가 이미 있다**:

- `SpatialAutocorrelation` (Moran's I) - Global Moran's I, z-score, p-value
- `HotSpots` / `OptimizedHotSpotAnalysis` (Getis-Ord Gi*) - 지점별 hotspot

즉 이 부분은 "재구현"이 아니라 "같은 통계량을 계산하는 Esri 정식 도구로
교체"다. Baseline이 permutation 기반 p_sim을 쓴 반면 arcpy는 정규근사
기반 p-value를 쓰는 차이가 있어, 결과가 근사적으로는 같아도 완전히
같은 숫자가 나오지는 않는다 - 이는 방법론 차이이지 구현 버그가 아니다.
"""

from __future__ import annotations

import logging

import arcpy

from .env import add_field_if_missing, delete_if_exists

logger = logging.getLogger("arcpy_pipeline.spatial_stats")


def compute_global_moran(
    fc: str,
    value_field: str = "priority_score",
    k: int = 8,
) -> dict:
    """Global Moran's I (KNN 공간가중치, row-standardized).

    Args:
        fc: point/polygon Feature Class.
        value_field: 공간적 자기상관을 검정할 수치 필드.
        k: KNN 이웃 수.

    Returns:
        {"I", "z_score", "p_value", "n", "k", "conceptualization"}.
        표본이 부족하면 None 값으로 채워 반환한다.
    """
    n = int(arcpy.management.GetCount(fc)[0])
    if n < k + 1:
        logger.warning("[SPATIAL] Moran's I 계산에 표본이 부족합니다 (n=%d, k=%d) - 건너뜀", n, k)
        return {"I": None, "z_score": None, "p_value": None, "n": n, "k": k}

    # 주의: arcpy.stats 도구는 대부분 Title_Case 키워드를 쓰지만
    # number_of_neighbors만 소문자다 (arcpy 3.7 실측 확인, inspect.signature로 검증).
    result = arcpy.stats.SpatialAutocorrelation(
        Input_Feature_Class=fc,
        Input_Field=value_field,
        Generate_Report="NO_REPORT",
        Conceptualization_of_Spatial_Relationships="K_NEAREST_NEIGHBORS",
        Distance_Method="EUCLIDEAN_DISTANCE",
        Standardization="ROW",
        number_of_neighbors=k,
    )
    out = {
        "I": float(result.getOutput(0)),
        "z_score": float(result.getOutput(1)),
        "p_value": float(result.getOutput(2)),
        "n": n,
        "k": k,
        "conceptualization": "K_NEAREST_NEIGHBORS",
    }
    logger.info(
        "[SPATIAL] Global Moran's I=%.4f z=%.4f p=%.4f (n=%d, k=%d) - %s",
        out["I"], out["z_score"], out["p_value"], n, k,
        "공간적 군집 유의(p<0.05)" if out["p_value"] < 0.05 else "유의하지 않음",
    )
    return out


def compute_hotspots(
    fc: str,
    value_field: str = "priority_score",
    out_fc: str | None = None,
    k: int = 8,
) -> str:
    """Getis-Ord Gi* hotspot 분류 (KNN 공간가중치).

    Args:
        fc: point/polygon Feature Class.
        value_field: hotspot을 계산할 수치 필드.
        out_fc: 결과 저장 경로. None이면 fc 옆에 "_hotspots" 접미사로 만든다.
        k: KNN 이웃 수.

    Returns:
        GiZScore/GiPValue/Gi_Bin(-3~+3, HOT_99..COLD_99) + 이 프로젝트
        스키마의 gi_zscore/gi_pvalue/gi_class 필드가 추가된 out_fc.
    """
    n = int(arcpy.management.GetCount(fc)[0])
    if n < k + 1:
        logger.warning("[SPATIAL] Gi* 계산에 표본이 부족합니다 (n=%d, k=%d) - 건너뜀", n, k)
        add_field_if_missing(fc, "gi_zscore", "DOUBLE")
        add_field_if_missing(fc, "gi_pvalue", "DOUBLE")
        add_field_if_missing(fc, "gi_class", "TEXT", field_length=10)
        return fc

    out_fc = out_fc or f"{fc}_hotspots"
    delete_if_exists(out_fc)
    arcpy.stats.HotSpots(
        Input_Feature_Class=fc,
        Input_Field=value_field,
        Output_Feature_Class=out_fc,
        Conceptualization_of_Spatial_Relationships="K_NEAREST_NEIGHBORS",
        Distance_Method="EUCLIDEAN_DISTANCE",
        number_of_neighbors=k,
    )

    add_field_if_missing(out_fc, "gi_zscore", "DOUBLE")
    add_field_if_missing(out_fc, "gi_pvalue", "DOUBLE")
    add_field_if_missing(out_fc, "gi_class", "TEXT", field_length=10)
    with arcpy.da.UpdateCursor(out_fc, ["GiZScore", "GiPValue", "gi_zscore", "gi_pvalue", "gi_class"]) as cur:
        for row in cur:
            z, p = row[0], row[1]
            row[2], row[3] = z, p
            row[4] = _classify_gi(z, p)
            cur.updateRow(row)

    n_hot = sum(1 for (c,) in arcpy.da.SearchCursor(out_fc, ["gi_class"]) if str(c).startswith("HOT"))
    logger.info("[SPATIAL] Gi* 완료: hotspot(HOT_90 이상) %d개 / 전체 %d개", n_hot, n)
    return out_fc


def _classify_gi(z: float, p: float) -> str:
    if z is None or p is None or p >= 0.10:
        return "NOT_SIG"
    level = "99" if p < 0.01 else ("95" if p < 0.05 else "90")
    return f"{'HOT' if z > 0 else 'COLD'}_{level}"
