"""픽셀 단위 NDVI 시계열 변화 분석(식생 감소, 지속성, 일시적 감소, 변화 빈도).

관측시점 사이에 약 2년의 간격이 있으므로 변화 '발생 시점'은 추정하지 않고 최초로 관측된
구간(first_change_interval)만 기록한다.

픽셀 상태 코드(persistence_class):
    0 NONE          감소 이벤트 없음
    1 PERSISTENT    마지막 감소 이후 유효 관측이 min_followup_obs 이상이고 모두 회복 기준 미만
    2 TEMPORARY     마지막 감소 이후 회복 기준(recovery_ndvi) 이상으로 돌아온 관측이 있음
    3 UNCONFIRMED   마지막 감소 이후 유효 관측이 부족해 지속성을 판정할 수 없음(지속성 미확인)
"""

from __future__ import annotations

import numpy as np

PERSISTENCE_CODES = {"NONE": 0, "PERSISTENT": 1, "TEMPORARY": 2, "UNCONFIRMED": 3}


def delta_ndvi(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """delta = after - before. 한쪽이라도 무효이면 NaN(0/변화없음으로 대체하지 않음)."""
    d = after.astype("float32") - before.astype("float32")
    d[~(np.isfinite(before) & np.isfinite(after))] = np.nan
    return d


def pair_decline(before: np.ndarray, after: np.ndarray, th: dict) -> np.ndarray:
    """식생 -> 비식생 감소 후보(bool). 세 조건을 모두 만족하고 양쪽 관측이 유효한 픽셀."""
    d = delta_ndvi(before, after)
    with np.errstate(invalid="ignore"):
        m = (before >= th["before_min"]) & (after <= th["after_max"]) & (d <= th["delta_max"])
    return m & np.isfinite(d)


def analyze_pixels(ndvi: np.ndarray, th: dict, min_followup_obs: int = 1,
                   min_valid_obs: int = 4) -> dict[str, np.ndarray]:
    """(T,H,W) NDVI 스택에서 연속 연도쌍 감소 이벤트와 지속성 클래스를 계산한다."""
    t, h, w = ndvi.shape
    valid = np.isfinite(ndvi)
    n_valid = valid.sum(0).astype("uint8")
    evaluable = n_valid >= min_valid_obs

    events = np.zeros((t - 1, h, w), dtype=bool)
    for k in range(t - 1):
        events[k] = pair_decline(ndvi[k], ndvi[k + 1], th) & evaluable
    freq = events.sum(0).astype("uint8")
    any_event = freq > 0

    # 최초 관측 구간 index(0..T-2), 없으면 255
    first_k = np.where(any_event, events.argmax(0), 255).astype("uint8")
    # 마지막 이벤트 구간: 뒤에서부터 argmax
    last_k = np.where(any_event, (t - 2) - events[::-1].argmax(0), 255).astype("int16")
    decl_obs = np.where(any_event, last_k + 1, 0)  # 마지막 감소가 처음 관측된 시점 index

    tidx = np.arange(t, dtype="int16")[:, None, None]
    later = valid & (tidx > decl_obs[None])
    n_follow = later.sum(0)
    with np.errstate(invalid="ignore"):
        recovered = (later & (ndvi >= th["recovery_ndvi"])).any(0)

    cls = np.zeros((h, w), dtype="uint8")
    cls[any_event & recovered] = PERSISTENCE_CODES["TEMPORARY"]
    cls[any_event & ~recovered & (n_follow >= min_followup_obs)] = PERSISTENCE_CODES["PERSISTENT"]
    cls[any_event & ~recovered & (n_follow < min_followup_obs)] = PERSISTENCE_CODES["UNCONFIRMED"]

    return {
        "valid": valid, "n_valid": n_valid, "evaluable": evaluable,
        "events": events, "frequency": freq, "any_decline": any_event,
        "first_interval_idx": first_k, "persistence_class": cls,
    }


def sensitivity_summary(ndvi: np.ndarray, profiles: dict[str, dict], pixel_area_m2: float,
                        min_followup_obs: int, min_valid_obs: int, reference: str = "default") -> dict:
    """임계값 profile별 감소 픽셀 면적과 default 대비 중첩(Jaccard)을 요약한다."""
    res, masks = {}, {}
    for name, th in profiles.items():
        r = analyze_pixels(ndvi, th, min_followup_obs, min_valid_obs)
        cls = r["persistence_class"]
        masks[name] = r["any_decline"]
        res[name] = {
            "thresholds": th,
            "decline_pixels": int(r["any_decline"].sum()),
            "decline_area_m2": float(r["any_decline"].sum() * pixel_area_m2),
            "persistent_area_m2": float((cls == 1).sum() * pixel_area_m2),
            "temporary_area_m2": float((cls == 2).sum() * pixel_area_m2),
            "unconfirmed_area_m2": float((cls == 3).sum() * pixel_area_m2),
            "repeated_decline_pixels": int((r["frequency"] >= 2).sum()),
        }
    ref = masks.get(reference)
    for name, m in masks.items():
        if ref is not None:
            u = (m | ref).sum()
            res[name]["jaccard_vs_" + reference] = float((m & ref).sum() / u) if u else None
    return res
