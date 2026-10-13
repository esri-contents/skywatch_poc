"""필지 분석 품질 플래그와 변화 유형 분류.

변화 유형은 토지피복·식생 변화 패턴이며 건축물/지장물/보상 대상 여부를 뜻하지 않는다.

분류 규칙(임계값은 config의 ndvi.classification / ndvi.parcel_quality, 초기 탐색용 가설):
  1. INSUFFICIENT_DATA : NOT_EVALUABLE, 유효 연도 수 < min_valid_years, 평가가능 면적 < min_effective_pixels 픽셀
  2. 감소 면적(m2) >= decline_area_min_m2 이고 감소 비율 >= decline_ratio_min 인 클래스를 아래 순서로 판정
       PERSISTENT_DECLINE  : 지속(감소 이후 관측에서 회복 기준 미만 유지)
       TEMPORARY_DECLINE   : 감소 후 회복 기준 이상으로 회복
       DECLINE_UNCONFIRMED : 후속 관측 부족으로 지속성 미확인 (6개 기본 유형 외 확장 유형)
     세 클래스가 각각 기준에 못 미치나 합계가 기준 이상이면 면적이 가장 큰 클래스를 쓴다.
  3. 감소 기준 미달이면 필지 평균 NDVI 시계열로 판정
       RECOVERY            : (마지막 - 처음) >= recovery_delta_min
       FLUCTUATING         : 연도별 평균 NDVI 표준편차 >= fluctuation_std_min
       GRADUAL_DECLINE     : (마지막 - 처음) <= -stable_delta_abs_max (픽셀 감소 기준 미달의 완만한 감소, 확장 유형)
       STABLE_VEGETATION   : 위 어디에도 해당하지 않고 시계열 평균 NDVI >= vegetation_mean_ndvi_min
       STABLE_NON_VEGETATION: 위와 같으나 평균 NDVI가 낮은 안정 비식생 필지(확장 유형)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CHANGE_TYPE_KO = {
    "STABLE_VEGETATION": "식생 안정",
    "PERSISTENT_DECLINE": "지속적 식생 감소",
    "TEMPORARY_DECLINE": "일시적 식생 감소 후 회복",
    "RECOVERY": "식생 회복",
    "FLUCTUATING": "변동성 큼",
    "INSUFFICIENT_DATA": "자료 부족(판단 보류)",
    "DECLINE_UNCONFIRMED": "식생 감소(지속성 미확인)",
    "GRADUAL_DECLINE": "완만한 NDVI 감소",
    "STABLE_NON_VEGETATION": "비식생 안정",
}

QUALITY_ORDER = ["NOT_EVALUABLE", "LOW_PIXEL_COUNT", "LOW_COVERAGE", "CLOUD_CONTAMINATION", "GEOMETRY_WARNING"]


def assess_quality(df: pd.DataFrame, years: list[int], q: dict, pixel_area_m2: float) -> pd.DataFrame:
    """필지별 품질 플래그(복수 가능)와 대표 상태. df에는 year_stats/decline_stats 결과가 병합되어 있어야 한다."""
    out = df.copy()
    n = len(out)
    area = out["area_in_aoi_m2"].to_numpy(float)
    yfrac = np.stack([out[f"valid_area_{y}"].to_numpy(float) / np.where(area > 0, area, np.nan) for y in years], axis=1)
    out["n_valid_years"] = (yfrac >= q["min_year_valid_fraction"]).sum(1)
    out["evaluable_fraction"] = out["evaluable_area_m2"] / out["area_in_aoi_m2"]
    flags = [[] for _ in range(n)]
    for i in range(n):
        if out["evaluable_area_m2"].iat[i] <= 0 or np.all(~(yfrac[i] > 0)):
            flags[i].append("NOT_EVALUABLE")
        if area[i] / pixel_area_m2 < q["min_effective_pixels"]:
            flags[i].append("LOW_PIXEL_COUNT")
        if out["evaluable_fraction"].iat[i] < q["min_evaluable_fraction"]:
            flags[i].append("LOW_COVERAGE")
        if np.any(yfrac[i] < q["min_year_valid_fraction"]):
            flags[i].append("CLOUD_CONTAMINATION")
        if bool(out["geom_repaired"].iat[i]) or bool(out["pnu_missing"].iat[i]) or not bool(out["pnu_len_ok"].iat[i]):
            flags[i].append("GEOMETRY_WARNING")
    out["quality_flags"] = [";".join(f) if f else "VALID" for f in flags]
    out["quality_status"] = [next((k for k in QUALITY_ORDER if k in f), "VALID") for f in flags]
    out["null_reason"] = np.where(out["quality_flags"].str.contains("NOT_EVALUABLE"),
                                  "유효 NDVI 관측 없음(전 연도 무효/마스크)", None)
    return out


def classify_parcel_types(df: pd.DataFrame, years: list[int], c: dict, q: dict, pixel_area_m2: float,
                          aoi_means: dict | None = None) -> pd.DataFrame:
    """aoi_means(연도 -> AOI 평균 NDVI)가 있으면 평균 시계열 기반 유형(RECOVERY/FLUCTUATING/GRADUAL_DECLINE)은
    'AOI 평균 대비 상대 NDVI'로 판정한다. 촬영일(DOY)/센서 차이로 AOI 전체가 같이 오르내리는 계절 위상 효과를
    줄이기 위한 단순 정규화이며, 픽셀 단위 감소 판정(식생->비식생)은 절대 NDVI를 그대로 쓴다.
    """
    out = df.copy()
    means = out[[f"ndvi_mean_{y}" for y in years]].to_numpy(float)
    if aoi_means:
        base = np.array([aoi_means[y] for y in years], dtype=float)
        rel = means - base[None, :]
    else:
        rel = means
    out["ndvi_rel_delta_first_last"] = [
        (r[np.isfinite(r)][-1] - r[np.isfinite(r)][0]) if np.isfinite(r).sum() >= 2 else np.nan for r in rel]
    types, persist, conf = [], [], []
    for i in range(len(out)):
        r = out.iloc[i]
        ev = float(r["evaluable_area_m2"])
        areas = {"PERSISTENT": float(r["persistent_decline_area_m2"]),
                 "TEMPORARY": float(r["temporary_decline_area_m2"]),
                 "UNCONFIRMED": float(r["unconfirmed_decline_area_m2"])}
        tot = float(r["decline_area_m2"])
        m = means[i][np.isfinite(means[i])]
        mr = rel[i][np.isfinite(rel[i])]
        insufficient = ("NOT_EVALUABLE" in r["quality_flags"] or r["n_valid_years"] < q["min_valid_years"]
                        or ev < q["min_effective_pixels"] * pixel_area_m2 or len(m) < 2)
        dom = max(areas, key=areas.get) if tot > 0 else "NONE"
        persist.append("NOT_EVALUABLE" if insufficient else dom)
        if insufficient:
            types.append("INSUFFICIENT_DATA")
            conf.append("NONE")
            continue

        def ok(a: float) -> bool:
            return a >= c["decline_area_min_m2"] and a / ev >= c["decline_ratio_min"]

        if ok(areas["PERSISTENT"]):
            t = "PERSISTENT_DECLINE"
        elif ok(areas["TEMPORARY"]):
            t = "TEMPORARY_DECLINE"
        elif ok(areas["UNCONFIRMED"]):
            t = "DECLINE_UNCONFIRMED"
        elif ok(tot):
            t = {"PERSISTENT": "PERSISTENT_DECLINE", "TEMPORARY": "TEMPORARY_DECLINE",
                 "UNCONFIRMED": "DECLINE_UNCONFIRMED"}[dom]
        else:
            d = float(mr[-1] - mr[0])
            sd = float(np.std(mr))
            if d >= c["recovery_delta_min"]:
                t = "RECOVERY"
            elif sd >= c["fluctuation_std_min"]:
                t = "FLUCTUATING"
            elif d <= -c["stable_delta_abs_max"]:
                t = "GRADUAL_DECLINE"
            elif float(np.mean(m)) >= c["vegetation_mean_ndvi_min"]:
                t = "STABLE_VEGETATION"
            else:
                t = "STABLE_NON_VEGETATION"
        types.append(t)
        status = r["quality_status"]
        conf.append("LOW" if status in ("LOW_PIXEL_COUNT", "LOW_COVERAGE", "CLOUD_CONTAMINATION")
                    else ("MEDIUM" if status == "GEOMETRY_WARNING" or r["n_valid_years"] < len(years) else "HIGH"))
    out["change_type"] = types
    out["change_type_ko"] = [CHANGE_TYPE_KO[t] for t in types]
    out["persistence_status"] = persist
    out["analysis_confidence"] = conf
    out["decline_ratio"] = np.where(out["evaluable_area_m2"] > 0,
                                    out["decline_area_m2"] / out["evaluable_area_m2"].where(out["evaluable_area_m2"] > 0), np.nan)
    return out
