"""객체 기반 구조물 판독(OBIA): 영상 조각화와 조각별 판독 요소 계산.

판독_코드(중앙선 매포 구간 Wayback 판독)의 방법 - Felzenszwalb 그래프 분할로 조각을 만들고
조각마다 색조·형태·크기·그림자를 계산 - 을 0.25m 항공사진에 맞게 옮겼다. 바뀐 점:

- 조각마다 전체 크기 마스크를 만들던 계산을 라벨 영상 이동(shift)과 bincount로 바꿔
  지구 전체(수십만 조각)를 처리할 수 있게 했다.
- 그림자는 둘레 전체가 아니라 4방향 띠로 나눠 가장 어두운 쪽의 비율을 본다. 건물 그림자는
  태양 반대쪽 한 면에만 생기므로 둘레 전체 평균은 그림자를 희석한다(Irvin & McKeown, 1989).
- 질감(조각 안 기울기 크기)과 경계 강도(조각 경계의 기울기)를 더했다. 지붕은 경계가 뚜렷하고
  안쪽이 매끈하며, 수목·적치물은 안쪽 질감이 거칠다.
- 판정은 손으로 정한 임계값 대신 이 특징으로 학습한 분류기가 한다(classifier.py).
"""

from __future__ import annotations

import cv2
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from scipy import ndimage
from skimage.color import rgb2lab
from skimage.measure import regionprops
from skimage.segmentation import felzenszwalb

FEATURES = [
    "area_m2", "rect_fill", "solidity", "elong", "compact",
    "r", "g", "b", "L", "a", "bb", "sat", "exg", "L_std", "ab_std",
    "texture", "edge", "contrast", "shadow_max", "shadow_mean", "bright_max",
]

# 방향별 띠: (행 이동, 열 이동) - 북, 동, 남, 서
_DIRS = [(-1, 0), (0, 1), (1, 0), (0, -1)]


def read_tile(path, bounds: tuple[float, float, float, float], res: float) -> np.ndarray:
    """영상에서 bounds 범위를 res(m) 격자로 평균 재표본해 0~1 RGB(H, W, 3)로 읽는다."""
    w = int(round((bounds[2] - bounds[0]) / res))
    h = int(round((bounds[3] - bounds[1]) / res))
    with rasterio.open(path) as ds:
        a = ds.read([1, 2, 3], window=from_bounds(*bounds, ds.transform), boundless=True,
                    out_shape=(3, h, w), resampling=Resampling.average)
    return a.transpose(1, 2, 0).astype(np.float32) / 255


def segment(rgb: np.ndarray, scale: float, sigma: float, min_px: int) -> np.ndarray:
    """Felzenszwalb 분할. 라벨은 1부터 시작한다(0은 쓰지 않음)."""
    return felzenszwalb(rgb, scale=scale, sigma=sigma, min_size=min_px, channel_axis=-1) + 1


def _shift(a: np.ndarray, dr: int, dc: int, fill) -> np.ndarray:
    """a를 (dr, dc)만큼 민 배열. out[i, j] = a[i + dr, j + dc], 범위 밖은 fill."""
    out = np.full_like(a, fill)
    h, w = a.shape
    rs, re = max(0, -dr), min(h, h - dr)
    cs, ce = max(0, -dc), min(w, w - dc)
    out[rs:re, cs:ce] = a[rs + dr:re + dr, cs + dc:ce + dc]
    return out


def segment_features(rgb: np.ndarray, labels: np.ndarray, valid: np.ndarray, res: float,
                     ring_m: float = 3.0, shadow_dl: float = 20.0, shadow_l_max: float = 30.0) -> pd.DataFrame:
    """조각별 판독 요소를 계산한다. 영상이 없는 화소(valid=False)가 섞인 조각은 valid_frac으로 표시."""
    n = int(labels.max()) + 1
    idx = labels.ravel()
    cnt = np.bincount(idx, minlength=n).astype(np.float64)
    cnt_safe = np.maximum(cnt, 1)

    def mean(v):
        return np.bincount(idx, weights=v.ravel().astype(np.float64), minlength=n) / cnt_safe

    def std(v):
        m2 = np.bincount(idx, weights=(v.ravel().astype(np.float64)) ** 2, minlength=n) / cnt_safe
        return np.sqrt(np.maximum(m2 - mean(v) ** 2, 0))

    lab = rgb2lab(rgb)
    L = lab[..., 0]
    s3 = rgb.sum(axis=2) + 1e-6
    exg = (2 * rgb[..., 1] - rgb[..., 0] - rgb[..., 2]) / s3  # Woebbecke et al.(1995)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    grad = np.hypot(ndimage.sobel(L, 0), ndimage.sobel(L, 1)) / 8
    boundary = np.zeros(labels.shape, bool)
    for dr, dc in _DIRS:
        boundary |= _shift(labels, dr, dc, 0) != labels

    f = pd.DataFrame({
        "label": np.arange(n), "px": cnt,
        "r": mean(rgb[..., 0]), "g": mean(rgb[..., 1]), "b": mean(rgb[..., 2]),
        "L": mean(L), "a": mean(lab[..., 1]), "bb": mean(lab[..., 2]),
        "sat": mean(sat), "exg": mean(exg), "L_std": std(L),
        "ab_std": np.sqrt(std(lab[..., 1]) ** 2 + std(lab[..., 2]) ** 2),
        "valid_frac": mean(valid.astype(np.float32)),
    })
    interior = ~boundary
    f["texture"] = np.bincount(idx, weights=(grad * interior).ravel(), minlength=n) / np.maximum(
        np.bincount(idx, weights=interior.ravel().astype(float), minlength=n), 1)
    f["edge"] = np.bincount(idx, weights=(grad * boundary).ravel(), minlength=n) / np.maximum(
        np.bincount(idx, weights=boundary.ravel().astype(float), minlength=n), 1)

    # 방향별 바깥 띠: 각 화소에서 한 방향으로 1..k 화소 떨어진 곳이 다른 조각이면 그 화소는
    # 이 조각의 그 방향 띠에 속한다(중복 계수 허용). 띠 화소가 조각 평균보다 shadow_dl 이상
    # 어둡고 절대 밝기도 shadow_l_max 미만이면 그림자 화소로 센다. 판독_코드는 상대 조건만 써서
    # 밝은 지붕 옆의 평범한 회색 바닥도 그림자로 셌다.
    k = max(1, int(round(ring_m / res)))
    Lm = f["L"].to_numpy()[labels]
    ring_sum = np.zeros(n); ring_cnt = np.zeros(n)
    side_dark, side_bright = [], []
    for dr, dc in _DIRS:
        dark = np.zeros(n); bright = np.zeros(n); tot = np.zeros(n)
        for j in range(1, k + 1):
            lab_s = _shift(labels, dr * j, dc * j, 0)
            L_s = _shift(L, dr * j, dc * j, np.nan)
            m = (lab_s != labels) & (lab_s != 0) & ~np.isnan(L_s)
            li = idx[m.ravel()]
            ls = L_s[m]
            tot += np.bincount(li, minlength=n)
            ring_sum += np.bincount(li, weights=ls, minlength=n)
            dark += np.bincount(li, weights=((ls < Lm[m] - shadow_dl) & (ls < shadow_l_max)).astype(float), minlength=n)
            bright += np.bincount(li, weights=(ls > Lm[m] + shadow_dl).astype(float), minlength=n)
        ring_cnt += tot
        side_dark.append(dark / np.maximum(tot, 1))
        side_bright.append(bright / np.maximum(tot, 1))
    side_dark = np.vstack(side_dark)
    f["contrast"] = f["L"] - ring_sum / np.maximum(ring_cnt, 1)
    f["shadow_max"] = side_dark.max(axis=0)
    f["shadow_mean"] = side_dark.mean(axis=0)
    f["bright_max"] = np.vstack(side_bright).max(axis=0)

    shape = {"label": [], "rect_fill": [], "solidity": [], "elong": [], "compact": [], "cy": [], "cx": []}
    for r in regionprops(labels):
        (_, _), (w, h), _ = cv2.minAreaRect(r.coords[:, ::-1].astype(np.float32))
        w, h = w + 1, h + 1  # 화소 중심 기준 사각형을 화소 외곽 기준으로
        shape["label"].append(r.label)
        shape["rect_fill"].append(r.area / (w * h))
        shape["solidity"].append(r.solidity)
        shape["elong"].append(max(w, h) / min(w, h))
        shape["compact"].append(4 * np.pi * r.area / max(r.perimeter, 1) ** 2)
        shape["cy"].append(r.centroid[0]); shape["cx"].append(r.centroid[1])
    f = f.merge(pd.DataFrame(shape), on="label")
    f["area_m2"] = f["px"] * res * res
    return f
