"""Method B - Structural Difference (SSIM 기반 구조 변화)."""

from __future__ import annotations

import numpy as np
from skimage.metrics import structural_similarity as ssim


def ssim_change(gray1: np.ndarray, gray2: np.ndarray, win_size: int = 7) -> np.ndarray:
    """SSIM 기반 구조 변화 맵을 계산한다 (1 - local SSIM).

    Args:
        gray1: (H, W) T1 그레이스케일 배열.
        gray2: (H, W) T2 그레이스케일 배열.
        win_size: SSIM 윈도우 크기 (홀수).

    Returns:
        (H, W) 0~1 구조 변화 스코어 (1에 가까울수록 구조가 많이 달라짐).
    """
    if gray1.shape != gray2.shape:
        raise ValueError(f"[CHANGE] T1/T2 shape이 다릅니다: {gray1.shape} vs {gray2.shape}")

    g1 = gray1.astype(np.float32)
    g2 = gray2.astype(np.float32)
    data_range = float(max(g1.max(), g2.max()) - min(g1.min(), g2.min()) or 1.0)

    _, ssim_map = ssim(g1, g2, win_size=win_size, data_range=data_range, full=True)
    change = 1.0 - ssim_map
    return np.clip(change, 0, 1)
