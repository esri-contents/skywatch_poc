"""Method A - Pixel Difference (분광 변화)."""

from __future__ import annotations

import numpy as np


def pixel_diff(t1: np.ndarray, t2: np.ndarray) -> np.ndarray:
    """다중밴드 배열 간 유클리드 거리 기반 분광 변화량을 계산한다.

    극단 픽셀 하나가 전체 스케일(max_val)을 왜곡시킬 수 있는 단순 baseline이다
    (의도적으로 남겨둠 - robust_cva()와 비교하기 위한 참고용). 실제 앙상블에는
    robust_cva()를 쓴다.

    Args:
        t1: (bands, H, W) T1 배열.
        t2: (bands, H, W) T2 배열 (t1과 동일 shape).

    Returns:
        (H, W) 0~1로 정규화된 변화 스코어.
    """
    if t1.shape != t2.shape:
        raise ValueError(f"[CHANGE] T1/T2 shape이 다릅니다: {t1.shape} vs {t2.shape}")
    diff = t1.astype(np.float32) - t2.astype(np.float32)
    magnitude = np.sqrt(np.sum(diff ** 2, axis=0))
    max_val = magnitude.max()
    if max_val <= 0:
        return np.zeros_like(magnitude)
    return magnitude / max_val


def robust_cva(
    t1: np.ndarray,
    t2: np.ndarray,
    normalize_percentile: float = 99.0,
) -> np.ndarray:
    """Robust Change Vector Analysis (median/MAD 표준화 기반, solafune-sentinel2-change 차용).

    pixel_diff()는 밴드별 raw 차분을 전역 max로 정규화하기 때문에 극단 픽셀
    하나(구름 잔여물, sensor artifact 등)가 전체 스케일을 눌러버려 나머지
    변화가 상대적으로 묻힌다. 이를 두 단계로 완화한다:
    1) 밴드별로 median/MAD(1.4826*MAD ~= std)로 표준화한 뒤 유클리드 결합
       (MAD가 0에 가까우면 표준편차로 fallback).
    2) 최종 정규화도 max가 아니라 percentile(기본 99)로 clip - 소수의
       극단값이 아니라 분포 대부분을 기준으로 0~1 스케일을 잡는다.

    Args:
        t1: (bands, H, W) T1 배열.
        t2: (bands, H, W) T2 배열 (t1과 동일 shape).
        normalize_percentile: 0~1 정규화에 사용할 percentile.

    Returns:
        (H, W) 0~1로 clip된 변화 스코어.
    """
    if t1.shape != t2.shape:
        raise ValueError(f"[CHANGE] T1/T2 shape이 다릅니다: {t1.shape} vs {t2.shape}")

    diff = t2.astype(np.float64) - t1.astype(np.float64)
    z_bands = np.empty_like(diff)
    for b in range(diff.shape[0]):
        band = diff[b]
        med = np.median(band)
        mad = np.median(np.abs(band - med))
        scale = 1.4826 * mad
        if scale < 1e-6:
            scale = band.std()
        if scale < 1e-6:
            z_bands[b] = 0.0
        else:
            z_bands[b] = (band - med) / scale

    magnitude = np.sqrt(np.sum(z_bands ** 2, axis=0))
    ref = np.percentile(magnitude, normalize_percentile)
    if ref <= 0:
        return np.zeros_like(magnitude, dtype=np.float32)
    return np.clip(magnitude / ref, 0, 1).astype(np.float32)
