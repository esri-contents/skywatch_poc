"""Method C(Edge/Texture) + Method D(Ensemble) + 전체 Baseline Change Detection 실행.

T1/T2 스택 GeoTIFF(raster_preprocess.build_stacked_scene 결과물)를 입력받아
Change Probability Raster와 Change Mask를 생성한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import rasterio

from .spectral import robust_cva
from .structural import ssim_change
from .thresholding import compute_otsu_threshold

logger = logging.getLogger("baseline_change")


def edge_texture_change(gray1: np.ndarray, gray2: np.ndarray) -> np.ndarray:
    """Canny 엣지맵의 절대차 기반 구조/텍스처 변화 스코어.

    건축물 외곽선처럼 엣지가 새로 생기거나 사라지는 변화를 잡기 위한 방법.
    """
    g1 = cv2.normalize(gray1, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    g2 = cv2.normalize(gray2, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    edges1 = cv2.Canny(g1, 50, 150)
    edges2 = cv2.Canny(g2, 50, 150)

    # 엣지 유무 차이를 국소적으로 누적(엣지가 1px만 어긋나도 변화로 잡히는 걸 완화)
    kernel = np.ones((5, 5), np.uint8)
    dil1 = cv2.dilate(edges1, kernel)
    dil2 = cv2.dilate(edges2, kernel)
    diff = cv2.bitwise_xor(dil1, dil2).astype(np.float32) / 255.0
    return diff


def to_grayscale(stack: np.ndarray, band_order: list[str]) -> np.ndarray:
    """RGB 밴드 평균으로 그레이스케일을 만든다 (band_order: [B02,B03,B04,B08])."""
    idx = {b: i for i, b in enumerate(band_order)}
    rgb = stack[[idx["B04"], idx["B03"], idx["B02"]]].astype(np.float32)
    return rgb.mean(axis=0)


def run_baseline_change_detection(
    t1_path: str | Path,
    t2_path: str | Path,
    out_prob_path: str | Path,
    out_mask_path: str | Path,
    band_order: list[str] = ("B02", "B03", "B04", "B08"),
    ensemble_weights: dict[str, float] = None,
    threshold_method: str = "fixed",
    mask_threshold: float = 0.5,
) -> tuple[Path, Path, float]:
    """Baseline Change Detection 전체 실행: 3개 방법 -> 앙상블 -> 확률/마스크 raster 저장.

    Args:
        t1_path: T1 스택 GeoTIFF.
        t2_path: T2 스택 GeoTIFF (T1과 동일 grid/CRS/shape 가정).
        out_prob_path: 저장할 change_probability.tif 경로.
        out_mask_path: 저장할 change_mask.tif 경로.
        band_order: 스택의 밴드 순서.
        ensemble_weights: {"spectral":.., "structural":.., "edge_texture":..} 가중치.
        threshold_method: "fixed"(기본, mask_threshold 고정값 사용) 또는
            "otsu"(AOI 유효 픽셀 분포에서 자동 산정 - 이 AOI에서는 훨씬
            공격적인 임계값을 골라 변화 후보가 급증하므로 육안 QA 전에는
            기본값으로 쓰지 않는다).
        mask_threshold: threshold_method="fixed"일 때 쓰는 고정 임계값. "otsu"가
            유효 픽셀 부족 등으로 계산 불가할 때의 fallback으로도 쓰인다.

    Returns:
        (change_probability 경로, change_mask 경로, 실제 사용된 임계값)
    """
    band_order = list(band_order)
    weights = ensemble_weights or {"spectral": 1 / 3, "structural": 1 / 3, "edge_texture": 1 / 3}

    with rasterio.open(t1_path) as s1, rasterio.open(t2_path) as s2:
        if s1.shape != s2.shape or s1.transform != s2.transform:
            raise ValueError(
                f"[CHANGE] T1/T2 grid가 다릅니다 (shape {s1.shape} vs {s2.shape}). "
                "먼저 동일 grid로 재정렬해야 합니다."
            )
        t1 = s1.read()
        t2 = s2.read()
        profile = s1.profile.copy()
        nodata1, nodata2 = s1.nodata, s2.nodata

    # AOI clip 바깥(NoData) 픽셀은 배경/변화 분포를 왜곡시키므로 Otsu 계산에서 제외.
    # T1/T2가 서로 다른 소스일 경우 nodata 값이 다를 수 있어 각자의 값으로 검사한다.
    valid1 = np.all(t1 != nodata1, axis=0) if nodata1 is not None else np.ones(t1.shape[1:], dtype=bool)
    valid2 = np.all(t2 != nodata2, axis=0) if nodata2 is not None else np.ones(t2.shape[1:], dtype=bool)
    valid_mask = valid1 & valid2

    logger.info("[CHANGE] Method A: robust CVA (median/MAD)")
    spectral_score = robust_cva(t1, t2)

    gray1 = to_grayscale(t1, band_order)
    gray2 = to_grayscale(t2, band_order)

    logger.info("[CHANGE] Method B: SSIM")
    structural_score = ssim_change(gray1, gray2)

    logger.info("[CHANGE] Method C: edge/texture")
    edge_score = edge_texture_change(gray1, gray2)

    logger.info("[CHANGE] Method D: ensemble")
    change_prob = (
        weights["spectral"] * spectral_score
        + weights["structural"] * structural_score
        + weights["edge_texture"] * edge_score
    ).astype(np.float32)

    if threshold_method == "otsu":
        used_threshold = compute_otsu_threshold(change_prob, valid_mask, fallback=mask_threshold)
    elif threshold_method == "fixed":
        used_threshold = mask_threshold
    else:
        raise ValueError(f"[CHANGE] 알 수 없는 threshold_method: {threshold_method}")

    change_mask = ((change_prob >= used_threshold) & valid_mask).astype(np.uint8)

    prob_profile = profile.copy()
    prob_profile.update(count=1, dtype="float32", nodata=None)
    out_prob_path = Path(out_prob_path)
    out_prob_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_prob_path, "w", **prob_profile) as dst:
        dst.write(change_prob, 1)

    mask_profile = profile.copy()
    mask_profile.update(count=1, dtype="uint8", nodata=0)
    out_mask_path = Path(out_mask_path)
    out_mask_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_mask_path, "w", **mask_profile) as dst:
        dst.write(change_mask, 1)

    logger.info(
        "[CHANGE] 저장 완료: prob=%s mask=%s (threshold=%.4f[%s], 변화 픽셀 비율=%.2f%%)",
        out_prob_path, out_mask_path, used_threshold, threshold_method, 100 * change_mask.mean(),
    )
    return out_prob_path, out_mask_path, used_threshold
