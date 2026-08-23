"""Method A - Pixel Difference (분광 변화)."""

from __future__ import annotations

import numpy as np


def pixel_diff(t1: np.ndarray, t2: np.ndarray) -> np.ndarray:
    """다중밴드 배열 간 유클리드 거리 기반 분광 변화량을 계산한다.

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
