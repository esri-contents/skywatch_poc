"""STEP 8 - 영상 정합 검증.

T1/T2가 동일 grid(shape/transform)로 전처리되어 있어도, 서로 다른 촬영
시기의 원본 영상을 각각 독립적으로 재투영한 것이므로 sub-pixel 수준의
잔여 오차가 있을 수 있다. ECC(Enhanced Correlation Coefficient) 기반으로
두 그레이스케일 영상 간 평행이동을 추정해 정합 오차를 정량화한다.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger("alignment")


def verify_alignment(
    gray1: np.ndarray,
    gray2: np.ndarray,
    pixel_size_m: float,
    max_iterations: int = 200,
    epsilon: float = 1e-6,
) -> dict:
    """ECC로 T1->T2 평행이동을 추정하고 정합 오차를 정량화한다.

    Args:
        gray1: (H, W) T1 그레이스케일.
        gray2: (H, W) T2 그레이스케일.
        pixel_size_m: 픽셀 한 변의 길이(m).
        max_iterations: ECC 최대 반복 횟수.
        epsilon: ECC 수렴 기준.

    Returns:
        dx_px, dy_px(추정 평행이동, 픽셀), displacement_px, displacement_m,
        ecc_score(상관계수, 1에 가까울수록 잘 맞음), converged(bool).
    """
    g1 = cv2.normalize(gray1, None, 0, 255, cv2.NORM_MINMAX).astype(np.float32)
    g2 = cv2.normalize(gray2, None, 0, 255, cv2.NORM_MINMAX).astype(np.float32)

    warp_matrix = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, max_iterations, epsilon)

    try:
        ecc_score, warp_matrix = cv2.findTransformECC(
            g1, g2, warp_matrix, cv2.MOTION_TRANSLATION, criteria
        )
        converged = True
    except cv2.error as e:
        logger.warning("[ALIGN] ECC 수렴 실패: %s", e)
        return {
            "dx_px": None, "dy_px": None, "displacement_px": None,
            "displacement_m": None, "ecc_score": None, "converged": False,
        }

    dx_px = float(warp_matrix[0, 2])
    dy_px = float(warp_matrix[1, 2])
    displacement_px = float(np.hypot(dx_px, dy_px))
    displacement_m = displacement_px * pixel_size_m

    logger.info(
        "[ALIGN] dx=%.3fpx dy=%.3fpx displacement=%.3fpx(%.2fm) ecc=%.4f",
        dx_px, dy_px, displacement_px, displacement_m, ecc_score,
    )
    return {
        "dx_px": dx_px, "dy_px": dy_px,
        "displacement_px": displacement_px, "displacement_m": displacement_m,
        "ecc_score": float(ecc_score), "converged": converged,
    }
