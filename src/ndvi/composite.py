"""NDVI + BSI 복합 픽셀 변화탐지 (20m 격자).

NDVI는 식생 감소/회복 신호, BSI는 노출지 증가/감소의 보조 신호다. 두 지표만으로 채석장·도로·농경지·건설현장·
나지를 확정 구분할 수 없으며 결과는 '후보'다. 변화 관측 간격이 약 2년이므로 변화 시점은 추정하지 않고
최초 관측 구간(first_interval_idx)만 기록한다. 무효 픽셀은 변화 없음으로 취급하지 않는다(코드 255).

이벤트 코드(구간별): 0 변화없음, 1 VEGETATION_LOSS, 2 EXPOSED_SURFACE_INCREASE, 3 VEGETATION_RECOVERY,
                    4 UNCERTAIN_CHANGE(상충/수체의심), 255 판정불가(무효 관측)
픽셀 유형 코드: 1 VEGETATION_LOSS, 2 EXPOSED_SURFACE_INCREASE, 3 PERSISTENT_EXPOSED_SURFACE,
               4 VEGETATION_RECOVERY, 5 STABLE_VEGETATION, 6 UNCERTAIN_CHANGE, 0 해당없음(안정 비식생 등), 255 판정불가
"""

from __future__ import annotations

import numpy as np

TYPE_CODES = {"VEGETATION_LOSS": 1, "EXPOSED_SURFACE_INCREASE": 2, "PERSISTENT_EXPOSED_SURFACE": 3,
              "VEGETATION_RECOVERY": 4, "STABLE_VEGETATION": 5, "UNCERTAIN_CHANGE": 6}
TYPE_NAMES = {v: k for k, v in TYPE_CODES.items()}
TYPE_KO = {"VEGETATION_LOSS": "식생 감소 후보", "EXPOSED_SURFACE_INCREASE": "노출지 증가 후보",
           "PERSISTENT_EXPOSED_SURFACE": "노출 지표 지속 후보", "VEGETATION_RECOVERY": "식생 회복 후보",
           "STABLE_VEGETATION": "식생 안정", "UNCERTAIN_CHANGE": "판단 불확실 변화"}
CONF_NAMES = {3: "HIGH", 2: "MEDIUM", 1: "LOW", 0: "NONE"}


def bias_shifts(ndvi: np.ndarray, bsi: np.ndarray, pair_idx: list[tuple[int, int]], stable_dndvi: float) -> list[float]:
    """연도쌍별 ΔBSI 계통 편이(촬영일/대기/센서): NDVI가 거의 변하지 않은 픽셀의 ΔBSI 중앙값."""
    out = []
    for a, b in pair_idx:
        dn = ndvi[b] - ndvi[a]
        db = bsi[b] - bsi[a]
        m = np.isfinite(dn) & np.isfinite(db) & (np.abs(dn) < stable_dndvi)
        out.append(float(np.median(db[m])) if m.sum() > 100 else 0.0)
    return out


def ndvi_shifts(ndvi: np.ndarray, pair_idx: list[tuple[int, int]]) -> list[float]:
    """연도쌍별 ΔNDVI 계통 편이(계절 위상/촬영일/센서): 전체 유효 픽셀 ΔNDVI 중앙값(대부분 픽셀이 불변이라는 가정)."""
    out = []
    for a, b in pair_idx:
        d = ndvi[b] - ndvi[a]
        d = d[np.isfinite(d)]
        out.append(float(np.median(d)) if d.size > 100 else 0.0)
    return out


def classify_composite(ndvi: np.ndarray, bsi: np.ndarray, mndwi: np.ndarray | None, p: dict,
                       consecutive: list[tuple[int, int]] | None = None, bias: bool = True) -> dict:
    """(T,H,W) 20m NDVI/BSI(NaN=무효) -> 복합 변화 이벤트/유형/신뢰도. p는 YAML bsi 섹션(또는 변형)."""
    t, h, w = ndvi.shape
    th, tb, ex = p["ndvi"], p["bsi"], p["exposed_state"]
    if consecutive is None:
        consecutive = [(i, i + 1) for i in range(t - 1)]
    valid_obs = np.isfinite(ndvi) & np.isfinite(bsi)
    n_valid = valid_obs.sum(0)
    evaluable = n_valid >= p["min_valid_obs_pixel"]
    shifts = bias_shifts(ndvi, bsi, consecutive, p.get("stable_dndvi", 0.05)) if bias else [0.0] * len(consecutive)
    nshifts = ndvi_shifts(ndvi, consecutive) if bias else [0.0] * len(consecutive)
    water_thr = p.get("water_mndwi_min", 0.0)
    state_exposed = valid_obs & (ndvi <= ex["ndvi_max"]) & (bsi >= tb["exposed_min"])
    water = (mndwi > water_thr) if mndwi is not None else np.zeros_like(valid_obs)
    water = water & np.isfinite(mndwi if mndwi is not None else ndvi)

    K = len(consecutive)
    ev = np.full((K, h, w), 255, dtype="uint8")
    dN = np.full((K, h, w), np.nan, dtype="float32")
    dB = np.full((K, h, w), np.nan, dtype="float32")
    rec_from_exposed = np.zeros((K, h, w), dtype=bool)
    for k, (a, b) in enumerate(consecutive):
        v = valid_obs[a] & valid_obs[b] & evaluable
        dn_raw = ndvi[b] - ndvi[a]
        dn = dn_raw - nshifts[k]          # 보정된 ΔNDVI(변화량 기준 판정에만 사용; 절대 NDVI 기준은 원값)
        db = bsi[b] - bsi[a] - shifts[k]
        dN[k], dB[k] = np.where(v, dn_raw, np.nan), np.where(v, db, np.nan)
        wat = water[a] | water[b]
        loss = v & (ndvi[a] >= th["before_min"]) & (ndvi[b] <= th["after_max"]) & (dn <= th["delta_max"])
        expo = loss & (db >= tb["increase_min"]) & (bsi[b] >= tb["exposed_min"]) & ~wat
        conflict = loss & (db <= -tb["conflict_decrease_min"])
        vegloss = loss & ~expo & ~wat & ~conflict
        rec = v & (ndvi[a] < th["recovery_ndvi"]) & (dn >= th["recovery_delta_min"]) & (ndvi[b] >= th["recovery_ndvi"]) & (db <= -tb["decrease_min"]) & ~wat
        # 상충(NDVI 감소 + BSI 감소)/수체 의심, 또는 NDVI 큰 증가인데 BSI가 줄지 않은 경우(계절 위상 가능성)
        unc = v & ((loss & (wat | conflict)) | (wat & (np.abs(dn) >= th["recovery_delta_min"]))
                   | ((dn >= th["recovery_delta_min"]) & ~rec & ~wat))
        e = np.zeros((h, w), dtype="uint8")
        e[unc] = 4
        e[rec] = 3
        e[vegloss] = 1
        e[expo] = 2
        e[~v] = 255
        ev[k] = e
        rec_from_exposed[k] = rec & state_exposed[a]

    has = lambda c: (ev == c).any(0)  # noqa: E731
    expo_any, loss_any, rec_any, unc_any = has(2), has(1), has(3), has(4)
    first_idx = np.full((h, w), 255, dtype="uint8")
    chg = (ev >= 1) & (ev <= 4)
    anyc = chg.any(0)
    first_idx[anyc] = chg.argmax(0)[anyc].astype("uint8")
    first_expo = np.full((h, w), 255, dtype="uint8")
    first_expo[expo_any] = (ev == 2).argmax(0)[expo_any].astype("uint8")

    # 노출 지속성: 마지막 노출지 증가 이후 유효 관측
    last_k = np.where(expo_any, (K - 1) - (ev[::-1] == 2).argmax(0), 0)
    obs_idx = np.array([consecutive[k][1] for k in range(K)])
    decl_obs = obs_idx[last_k]
    tidx = np.arange(t)[:, None, None]
    later = valid_obs & (tidx > decl_obs[None])
    n_later = later.sum(0)
    revegetated = (later & (ndvi >= th["recovery_ndvi"])).any(0)
    still = np.where(later, state_exposed, True).all(0)
    persist = np.zeros((h, w), dtype="uint8")   # 0 해당없음 1 PERSISTENT 2 UNCONFIRMED 3 REVEGETATED 4 PARTIAL
    persist[expo_any & revegetated] = 3
    persist[expo_any & ~revegetated & (n_later >= p["min_followup_obs"]) & still] = 1
    persist[expo_any & ~revegetated & (n_later >= p["min_followup_obs"]) & ~still] = 4
    persist[expo_any & ~revegetated & (n_later < p["min_followup_obs"])] = 2

    n_exposed_state = state_exposed.sum(0)
    persistent_exposed = evaluable & (n_exposed_state >= p["min_persistent_exposed_obs"])
    stable_veg = evaluable & ((~valid_obs) | (ndvi >= th["before_min"])).all(0) & ~anyc

    ctype = np.zeros((h, w), dtype="uint8")
    ctype[unc_any] = TYPE_CODES["UNCERTAIN_CHANGE"]
    ctype[stable_veg] = TYPE_CODES["STABLE_VEGETATION"]
    ctype[persistent_exposed] = TYPE_CODES["PERSISTENT_EXPOSED_SURFACE"]
    ctype[rec_any] = TYPE_CODES["VEGETATION_RECOVERY"]
    ctype[loss_any] = TYPE_CODES["VEGETATION_LOSS"]
    ctype[expo_any] = TYPE_CODES["EXPOSED_SURFACE_INCREASE"]
    ctype[~evaluable] = 255

    # 신뢰도: 두 지표가 일치하고 후속 관측으로 상태가 유지되면 HIGH
    conf = np.zeros((h, w), dtype="uint8")
    conf[(ctype == 1)] = 1
    conf[(ctype == 6)] = 1
    conf[(ctype == 4)] = 2
    conf[(ctype == 2) & (persist == 2)] = 2
    conf[(ctype == 2) & np.isin(persist, (3, 4))] = 2
    conf[(ctype == 2) & (persist == 1)] = 3
    conf[(ctype == 3)] = 2
    conf[(ctype == 5)] = 3
    conf[~evaluable] = 0

    return {"events": ev, "d_ndvi": dN, "d_bsi": dB, "bias_shifts": shifts, "ndvi_bias_shifts": nshifts, "type": ctype, "confidence": conf,
            "first_interval_idx": first_idx, "first_exposed_idx": first_expo, "persist": persist,
            "expo_any": expo_any, "loss_any": loss_any, "rec_any": rec_any, "unc_any": unc_any,
            "rec_from_exposed": rec_from_exposed.any(0), "persistent_exposed": persistent_exposed,
            "evaluable": evaluable, "n_valid": n_valid.astype("uint8"), "water": water.any(0),
            "state_exposed": state_exposed, "consecutive": consecutive}
