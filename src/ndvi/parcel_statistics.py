"""필지 x NDVI 래스터 면적가중 공간통계 (exactextract 미설치 환경용 순수 shapely/numpy 구현).

각 필지(AOI 내 부분)와 10m 픽셀 사각형의 실제 교차 면적을 정확히 계산해 가중치로 쓴다.
- 경계에 걸친 픽셀은 필지와 겹친 면적만큼만 기여한다(전부 귀속하지 않음).
- 무효(NaN) 픽셀은 분모/분자에서 모두 제외한다.
- 한계: 픽셀 값 자체가 10m 혼합 반응이므로 소형 필지의 NDVI는 인접 피복의 영향을 받는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely
from rasterio.transform import Affine


def pixel_weights(geoms, transform: Affine, shape: tuple[int, int]) -> dict[str, np.ndarray]:
    """필지별 (flat_pixel_index, 교차면적 m^2) sparse 가중치. 반환 배열은 parcel 순서대로 정렬된다."""
    h, w = shape
    res_x, res_y = transform.a, -transform.e
    x0, y0 = transform.c, transform.f
    P, F, A = [], [], []
    for i, g in enumerate(geoms):
        if g is None or g.is_empty:
            continue
        minx, miny, maxx, maxy = g.bounds
        c0 = max(int(np.floor((minx - x0) / res_x)), 0)
        c1 = min(int(np.ceil((maxx - x0) / res_x)), w)
        r0 = max(int(np.floor((y0 - maxy) / res_y)), 0)
        r1 = min(int(np.ceil((y0 - miny) / res_y)), h)
        if c1 <= c0 or r1 <= r0:
            continue
        rr, cc = np.meshgrid(np.arange(r0, r1), np.arange(c0, c1), indexing="ij")
        rr, cc = rr.ravel(), cc.ravel()
        boxes = shapely.box(x0 + cc * res_x, y0 - (rr + 1) * res_y, x0 + (cc + 1) * res_x, y0 - rr * res_y)
        shapely.prepare(g)
        inter = shapely.intersects(g, boxes)
        if not inter.any():
            continue
        rr, cc, boxes = rr[inter], cc[inter], boxes[inter]
        inside = shapely.contains(g, boxes)
        area = np.full(len(boxes), res_x * res_y, dtype="float64")
        if (~inside).any():
            area[~inside] = shapely.area(shapely.intersection(boxes[~inside], g))
        keep = area > 1e-6
        P.append(np.full(int(keep.sum()), i, dtype="int32"))
        F.append((rr[keep] * w + cc[keep]).astype("int64"))
        A.append(area[keep])
    if not P:
        z = np.array([], dtype="int32")
        return {"p": z, "f": z.astype("int64"), "a": z.astype("float64")}
    return {"p": np.concatenate(P), "f": np.concatenate(F), "a": np.concatenate(A)}


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    if values.size == 0 or weights.sum() <= 0:
        return float("nan")
    o = np.argsort(values)
    v, w = values[o], weights[o]
    cw = np.cumsum(w)
    return float(v[np.searchsorted(cw, 0.5 * cw[-1])])


def _bc(p: np.ndarray, x: np.ndarray, n: int) -> np.ndarray:
    return np.bincount(p, weights=x, minlength=n)


def _group_slices(p: np.ndarray, n: int) -> np.ndarray:
    return np.searchsorted(p, np.arange(n + 1))


def year_stats(wt: dict, n: int, ndvi_flat: np.ndarray, label: str) -> pd.DataFrame:
    """단일 연도: 유효면적, 면적가중 평균, 가중 중앙값, 유효 픽셀 수."""
    p, f, a = wt["p"], wt["f"], wt["a"]
    v = ndvi_flat[f]
    ok = np.isfinite(v)
    va = _bc(p, a * ok, n)
    s = _bc(p, np.where(ok, a * v, 0.0), n)
    mean = np.where(va > 0, s / np.where(va > 0, va, 1), np.nan)
    med = np.full(n, np.nan)
    sl = _group_slices(p, n)
    for i in range(n):
        lo, hi = sl[i], sl[i + 1]
        if hi > lo:
            m = ok[lo:hi]
            if m.any():
                med[i] = weighted_median(v[lo:hi][m], a[lo:hi][m])
    return pd.DataFrame({
        f"valid_area_{label}": va, f"ndvi_mean_{label}": mean, f"ndvi_median_{label}": med,
        f"valid_px_{label}": _bc(p, ok.astype(float), n).astype(int),
    })


def pair_delta_stats(wt: dict, n: int, before_flat: np.ndarray, after_flat: np.ndarray, label: str) -> pd.DataFrame:
    p, f, a = wt["p"], wt["f"], wt["a"]
    b, c = before_flat[f], after_flat[f]
    ok = np.isfinite(b) & np.isfinite(c)
    va = _bc(p, a * ok, n)
    s = _bc(p, np.where(ok, a * (c - b), 0.0), n)
    return pd.DataFrame({f"delta_mean_{label}": np.where(va > 0, s / np.where(va > 0, va, 1), np.nan)})


def decline_stats(wt: dict, n: int, px: dict, pair_labels: list[str], min_first_area: float) -> pd.DataFrame:
    """감소/지속성/빈도/최초 관측 구간 통계(면적 m^2). px = temporal.analyze_pixels 결과."""
    p, f, a = wt["p"], wt["f"], wt["a"]
    evalm = px["evaluable"].ravel()[f]
    anyd = px["any_decline"].ravel()[f]
    cls = px["persistence_class"].ravel()[f]
    freq = px["frequency"].ravel()[f].astype(float)
    first = px["first_interval_idx"].ravel()[f]
    out = {
        "evaluable_area_m2": _bc(p, a * evalm, n),
        "decline_area_m2": _bc(p, a * anyd, n),
        "persistent_decline_area_m2": _bc(p, a * (cls == 1), n),
        "temporary_decline_area_m2": _bc(p, a * (cls == 2), n),
        "unconfirmed_decline_area_m2": _bc(p, a * (cls == 3), n),
        "repeated_decline_area_m2": _bc(p, a * (freq >= 2), n),
    }
    ea = out["evaluable_area_m2"]
    out["change_frequency_mean"] = np.where(ea > 0, _bc(p, a * evalm * freq, n) / np.where(ea > 0, ea, 1), np.nan)
    first_area = np.stack([_bc(p, a * (anyd & (first == k)), n) for k in range(len(pair_labels))], axis=1)
    first_lab = np.full(n, None, dtype=object)
    for i in range(n):
        ok = np.where(first_area[i] >= min_first_area)[0]
        if ok.size:
            first_lab[i] = pair_labels[ok[0]]
    out["first_change_interval"] = first_lab
    df = pd.DataFrame(out)
    for k, lab in enumerate(pair_labels):
        df[f"first_decline_area_{lab}_m2"] = first_area[:, k]  # 해당 구간이 '최초' 관측 구간인 면적
    return df
