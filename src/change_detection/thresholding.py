"""Change Probability -> Change Mask 임계값 산정 (solafune-sentinel2-change 차용).

기존 baseline.py는 mask_threshold=0.5 고정값을 썼다. AOI/시기마다 변화의
분포가 다르므로 고정값은 과탐지/과소탐지를 오갈 수 있다. Otsu는 change_prob
분포 자체(bimodal이면 배경/변화 두 봉우리 사이)에서 임계값을 자동으로
잡아준다 - 유효 픽셀(AOI 내부)만 대상으로 계산해야 한다.
"""

from __future__ import annotations

import logging

import numpy as np
from skimage.filters import threshold_otsu

logger = logging.getLogger("thresholding")


def compute_otsu_threshold(
    prob: np.ndarray,
    valid_mask: np.ndarray | None = None,
    fallback: float = 0.5,
) -> float:
    """change_probability 배열에서 Otsu 임계값을 계산한다.

    Args:
        prob: (H, W) change_probability (0~1).
        valid_mask: (H, W) bool, True인 픽셀만 대상으로 계산(AOI/NoData 제외용).
            None이면 전체 배열 사용.
        fallback: 유효 픽셀이 없거나 분포가 단일값이라 Otsu가 정의되지 않을 때
            사용할 값.

    Returns:
        0~1 사이 임계값.
    """
    values = prob[valid_mask] if valid_mask is not None else prob.ravel()
    values = values[np.isfinite(values)]

    if values.size == 0 or np.allclose(values.min(), values.max()):
        logger.warning(
            "[THRESHOLD] Otsu 계산 불가(유효 픽셀 없음 또는 단일값) - fallback=%.2f 사용", fallback
        )
        return fallback

    threshold = float(threshold_otsu(values))
    logger.info("[THRESHOLD] Otsu 임계값=%.4f (유효 픽셀 %d개)", threshold, values.size)
    return threshold
