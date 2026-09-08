"""Change Detection (arcpy + Spatial Analyst) - REQ01 토지·건축물 변화 확인.

Baseline(`src/change_detection/*`)과 **알고리즘은 동일**하다 (robust CVA +
SSIM + edge/texture 앙상블). 두 경로의 결과를 대조할 수 있어야 하므로
수식을 바꾸지 않았다. 바뀐 것은 의존성과 후처리 구현이다:

| 단계 | Baseline | arcpy 경로 | 바꾼 이유 |
|---|---|---|---|
| SSIM | skimage.metrics | scipy.ndimage.uniform_filter | Pro 환경에 skimage 없음 |
| Edge | cv2.Canny | scipy Sobel 그래디언트 | Pro 환경에 OpenCV 없음 |
| Otsu | skimage.filters | numpy 구현 | 동일 알고리즘, 의존성 제거 |
| 형태학 후처리 | skimage.morphology | arcpy.sa.Shrink/Expand | ArcGIS Pro에서 중간산출물을 눈으로 검토 가능 |
| 최소면적 제거 | remove_small_objects | arcpy.sa.RegionGroup + Con | 〃 |
| Polygon화 | rasterio.features.shapes | arcpy.conversion.RasterToPolygon | GDB에 바로 산출 |

**SSIM 구현 주의**: skimage의 기본값은 gaussian_weights=False에 uniform
7x7 윈도우이므로, uniform_filter로 동일하게 재현된다. data_range도
Baseline과 같은 방식(두 영상 전체의 max-min)으로 계산해 값이 맞도록 했다.
"""

from __future__ import annotations

import logging

import arcpy
import numpy as np
from scipy.ndimage import sobel, uniform_filter

from .env import delete_if_exists
from .raster_ops import read_stack, to_grayscale, write_raster

logger = logging.getLogger("arcpy_pipeline.change_detect")


# --------------------------------------------------------------------------
# Method A - Robust Change Vector Analysis
# --------------------------------------------------------------------------
def robust_cva(t1: np.ndarray, t2: np.ndarray, normalize_percentile: float = 99.0) -> np.ndarray:
    """밴드별 median/MAD 표준화 후 유클리드 결합 (Baseline `spectral.robust_cva`와 동일).

    단순 차분을 전역 max로 정규화하면 극단 픽셀 하나(구름 잔여물 등)가
    전체 스케일을 눌러 나머지 변화가 묻힌다. median/MAD로 밴드를 표준화하고
    최종 정규화도 percentile로 clip해 그 문제를 완화한다.

    NaN(NoData)은 통계 계산에서 제외하고, 결과에서도 NaN으로 남긴다.
    """
    if t1.shape != t2.shape:
        raise ValueError(f"[CHANGE] T1/T2 shape이 다릅니다: {t1.shape} vs {t2.shape}")

    diff = t2.astype(np.float64) - t1.astype(np.float64)
    z_bands = np.empty_like(diff)
    for b in range(diff.shape[0]):
        band = diff[b]
        finite = band[np.isfinite(band)]
        if finite.size == 0:
            z_bands[b] = 0.0
            continue
        med = np.median(finite)
        mad = np.median(np.abs(finite - med))
        scale = 1.4826 * mad
        if scale < 1e-6:
            scale = float(finite.std())
        z_bands[b] = 0.0 if scale < 1e-6 else (band - med) / scale

    magnitude = np.sqrt(np.nansum(z_bands ** 2, axis=0))
    finite_mag = magnitude[np.isfinite(magnitude)]
    if finite_mag.size == 0:
        return np.zeros_like(magnitude, dtype=np.float32)
    ref = np.percentile(finite_mag, normalize_percentile)
    if ref <= 0:
        return np.zeros_like(magnitude, dtype=np.float32)
    return np.clip(magnitude / ref, 0, 1).astype(np.float32)


# --------------------------------------------------------------------------
# Method B - SSIM (구조 변화)
# --------------------------------------------------------------------------
def ssim_change(gray1: np.ndarray, gray2: np.ndarray, win_size: int = 7) -> np.ndarray:
    """1 - local SSIM. skimage 없이 uniform_filter로 동일 정의를 계산한다.

    SSIM(x,y) = ((2*mu_x*mu_y + C1)(2*cov + C2)) /
                ((mu_x^2 + mu_y^2 + C1)(var_x + var_y + C2))
    C1=(0.01*L)^2, C2=(0.03*L)^2, L=data_range.

    skimage는 표본분산(ddof=1) 보정을 쓰므로 여기서도 N/(N-1)을 곱해 맞춘다
    - 이걸 빠뜨리면 Baseline 대비 값이 미세하게 낮게 나온다.
    """
    if gray1.shape != gray2.shape:
        raise ValueError(f"[CHANGE] T1/T2 shape이 다릅니다: {gray1.shape} vs {gray2.shape}")

    g1 = np.nan_to_num(gray1.astype(np.float64))
    g2 = np.nan_to_num(gray2.astype(np.float64))

    data_range = float(max(g1.max(), g2.max()) - min(g1.min(), g2.min())) or 1.0
    c1 = (0.01 * data_range) ** 2
    c2 = (0.03 * data_range) ** 2

    size = (win_size, win_size)
    n = win_size * win_size
    cov_norm = n / (n - 1)

    mu1 = uniform_filter(g1, size=size)
    mu2 = uniform_filter(g2, size=size)
    mu1_sq, mu2_sq, mu1_mu2 = mu1 * mu1, mu2 * mu2, mu1 * mu2

    sigma1_sq = cov_norm * (uniform_filter(g1 * g1, size=size) - mu1_sq)
    sigma2_sq = cov_norm * (uniform_filter(g2 * g2, size=size) - mu2_sq)
    sigma12 = cov_norm * (uniform_filter(g1 * g2, size=size) - mu1_mu2)

    numerator = (2 * mu1_mu2 + c1) * (2 * sigma12 + c2)
    denominator = (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)
    ssim_map = np.where(denominator > 0, numerator / denominator, 1.0)

    return np.clip(1.0 - ssim_map, 0, 1).astype(np.float32)


# --------------------------------------------------------------------------
# Method C - Edge / Texture
# --------------------------------------------------------------------------
def edge_texture_change(gray1: np.ndarray, gray2: np.ndarray, dilate_cells: int = 2) -> np.ndarray:
    """Sobel 그래디언트 크기의 변화량 (Baseline의 Canny XOR을 대체).

    Canny는 이진 엣지맵이라 임계값 두 개에 민감하고, 엣지가 1픽셀만
    어긋나도 전부 변화로 잡혀 Baseline은 dilate로 그걸 완화해야 했다.
    Sobel 그래디언트 크기는 연속값이라 그 문제가 덜하고, 정규화 후 절대차를
    쓰면 "건축물 외곽선이 새로 생기거나 사라진 정도"를 부드럽게 표현한다.
    국소 최대값 필터로 미세한 위치 어긋남을 흡수하는 것은 동일하다.
    """
    def _grad(g: np.ndarray) -> np.ndarray:
        gg = np.nan_to_num(g.astype(np.float64))
        mag = np.hypot(sobel(gg, axis=0), sobel(gg, axis=1))
        peak = np.percentile(mag, 99.0)
        return np.clip(mag / peak, 0, 1) if peak > 0 else np.zeros_like(mag)

    diff = np.abs(_grad(gray1) - _grad(gray2))
    if dilate_cells > 0:
        size = 2 * dilate_cells + 1
        # 최대값 필터 대신 uniform_filter로 국소 평균을 내면 1픽셀 어긋남이
        # 과대평가되지 않으면서도 주변 엣지 변화가 반영된다.
        diff = uniform_filter(diff, size=size)
        peak = np.percentile(diff, 99.0)
        if peak > 0:
            diff = np.clip(diff / peak, 0, 1)
    return diff.astype(np.float32)


# --------------------------------------------------------------------------
# Threshold
# --------------------------------------------------------------------------
def compute_otsu_threshold(
    prob: np.ndarray,
    valid_mask: np.ndarray | None = None,
    fallback: float = 0.5,
    nbins: int = 256,
) -> float:
    """Otsu 임계값 (클래스 간 분산 최대화) - skimage 없이 numpy로 구현.

    유효 픽셀(AOI 내부)만 대상으로 계산해야 한다. AOI 바깥 NoData가 섞이면
    분포가 0에 몰려 임계값이 비정상적으로 낮아진다.

    **동점(plateau) 처리**: 변화 확률처럼 두 봉우리가 뚜렷이 분리된 분포에서는
    그 사이 빈 구간의 모든 분할이 **수학적으로 동일한** 클래스간 분산을 낸다
    (실측: 상대오차 2e-15 수준으로 동일). 이때 단순 argmax는 부동소수점
    잡음에 따라 매번 다른 인덱스를 고르고, 그 결과 numpy 버전이나 CPU가
    바뀌면 임계값이 달라진다 - run_manifest.json으로 재현성을 보장하는
    이 프로젝트에서는 실제 문제다(skimage.threshold_otsu도 같은 성질을 가진다).
    따라서 최대 분산과 사실상 동률인 구간을 모두 찾아 **그 중앙값**을
    임계값으로 쓴다. 동률 구간은 어차피 두 모드를 똑같이 분리하므로
    중앙을 고르는 것이 경계에 붙는 것보다 안정적이기도 하다.
    """
    values = prob[valid_mask] if valid_mask is not None else prob.ravel()
    values = values[np.isfinite(values)]

    if values.size == 0 or np.allclose(values.min(), values.max()):
        logger.warning("[THRESHOLD] Otsu 계산 불가 - fallback=%.2f 사용", fallback)
        return fallback

    hist, bin_edges = np.histogram(values, bins=nbins)
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    weight1 = np.cumsum(hist)
    weight2 = np.cumsum(hist[::-1])[::-1]
    valid = (weight1[:-1] > 0) & (weight2[1:] > 0)
    if not valid.any():
        return fallback

    mean1 = np.cumsum(hist * centers) / np.maximum(weight1, 1)
    mean2 = (np.cumsum((hist * centers)[::-1]) / np.maximum(weight2[::-1], 1))[::-1]
    variance = weight1[:-1] * weight2[1:] * (mean1[:-1] - mean2[1:]) ** 2
    variance = np.where(valid, variance, -np.inf)

    best = variance.max()
    if not np.isfinite(best) or best <= 0:
        logger.warning("[THRESHOLD] Otsu 분산 최대값이 유효하지 않음 - fallback=%.2f", fallback)
        return fallback

    # 상대허용오차 안에서 최대값과 동률인 후보들의 중앙 인덱스를 쓴다.
    tied = np.flatnonzero(variance >= best * (1.0 - 1e-9))
    idx = int(tied[len(tied) // 2])

    threshold = float(centers[:-1][idx])
    logger.info(
        "[THRESHOLD] Otsu 임계값=%.4f (유효 픽셀 %d개, 동률 후보 %d개 중 중앙 선택)",
        threshold, values.size, len(tied),
    )
    return threshold


# --------------------------------------------------------------------------
# 전체 실행
# --------------------------------------------------------------------------
def run_change_detection(
    t1_raster: str,
    t2_raster: str,
    out_prob: str,
    out_mask: str,
    ensemble_weights: dict[str, float] | None = None,
    threshold_method: str = "fixed",
    mask_threshold: float = 0.5,
) -> tuple[str, str, float, dict]:
    """T1/T2 스택에서 change_probability와 change_mask 래스터를 만든다.

    Args:
        t1_raster: T1 스택 (build_stacked_scene 결과).
        t2_raster: T2 스택 (T1과 동일 격자).
        out_prob: 저장할 change_probability 래스터.
        out_mask: 저장할 change_mask 래스터 (0/1).
        ensemble_weights: {"spectral","structural","edge_texture"} 가중치.
        threshold_method: "fixed" | "otsu".
        mask_threshold: fixed일 때 임계값, otsu 실패 시 fallback.

    Returns:
        (prob 경로, mask 경로, 사용된 임계값, 방법별 통계 dict)
    """
    weights = ensemble_weights or {"spectral": 1 / 3, "structural": 1 / 3, "edge_texture": 1 / 3}

    t1, meta1 = read_stack(t1_raster)
    t2, meta2 = read_stack(t2_raster)
    if t1.shape != t2.shape:
        raise ValueError(
            f"[CHANGE] T1/T2 격자가 다릅니다 ({t1.shape} vs {t2.shape}). "
            "raster_ops.build_stacked_scene에 snap_raster=T1을 지정해 재생성하세요."
        )

    valid_mask = np.all(np.isfinite(t1), axis=0) & np.all(np.isfinite(t2), axis=0)
    logger.info("[CHANGE] 유효 픽셀 %d / %d", int(valid_mask.sum()), valid_mask.size)

    logger.info("[CHANGE] Method A: robust CVA")
    spectral = robust_cva(t1, t2)
    gray1, gray2 = to_grayscale(t1), to_grayscale(t2)
    logger.info("[CHANGE] Method B: SSIM")
    structural = ssim_change(gray1, gray2)
    logger.info("[CHANGE] Method C: edge/texture")
    edge = edge_texture_change(gray1, gray2)

    logger.info("[CHANGE] Method D: ensemble")
    prob = (
        weights["spectral"] * spectral
        + weights["structural"] * structural
        + weights["edge_texture"] * edge
    ).astype(np.float32)
    prob = np.where(valid_mask, prob, np.nan)

    if threshold_method == "otsu":
        used_threshold = compute_otsu_threshold(prob, valid_mask, fallback=mask_threshold)
    elif threshold_method == "fixed":
        used_threshold = mask_threshold
    else:
        raise ValueError(f"[CHANGE] 알 수 없는 threshold_method: {threshold_method}")

    mask = ((prob >= used_threshold) & valid_mask).astype(np.float32)
    mask = np.where(valid_mask, mask, np.nan)

    write_raster(prob, meta1, out_prob)
    write_raster(mask, meta1, out_mask)

    stats = {
        "valid_pixels": int(valid_mask.sum()),
        "changed_pixels": int(np.nansum(mask)),
        "changed_pct": round(100.0 * float(np.nansum(mask)) / max(int(valid_mask.sum()), 1), 4),
        "threshold_method": threshold_method,
        "used_threshold": round(float(used_threshold), 6),
        "mean_scores": {
            "spectral": round(float(np.nanmean(np.where(valid_mask, spectral, np.nan))), 4),
            "structural": round(float(np.nanmean(np.where(valid_mask, structural, np.nan))), 4),
            "edge_texture": round(float(np.nanmean(np.where(valid_mask, edge, np.nan))), 4),
        },
    }
    logger.info(
        "[CHANGE] 완료: threshold=%.4f(%s), 변화 픽셀 비율=%.2f%%",
        used_threshold, threshold_method, stats["changed_pct"],
    )
    return out_prob, out_mask, used_threshold, stats


def clean_mask(
    mask_raster: str,
    out_raster: str,
    opening_cells: int = 1,
    closing_cells: int = 1,
    min_component_area_m2: float = 25.0,
) -> str:
    """arcpy.sa 기반 마스크 후처리: Opening → Closing → 최소면적 제거.

    Opening = Shrink 후 Expand (돌출 노이즈 제거),
    Closing = Expand 후 Shrink (내부 구멍 메움).
    Baseline의 3x3 커널 opening/closing은 1셀 Shrink/Expand와 같다.

    최소면적 제거는 RegionGroup으로 연결요소에 ID를 부여하고 각 요소의
    픽셀 수(COUNT)를 Lookup으로 래스터화한 뒤 임계 미만을 버린다 -
    skimage.remove_small_objects와 동일한 결과이면서, 중간 산출물
    (region ID, 면적)을 ArcGIS Pro에서 그대로 열어볼 수 있다.

    Args:
        mask_raster: 0/1 이진 마스크 래스터.
        out_raster: 저장할 정리된 마스크.
        opening_cells: opening 셀 수.
        closing_cells: closing 셀 수.
        min_component_area_m2: 이보다 작은 연결요소는 제거.

    Returns:
        저장된 래스터 경로.
    """
    ras = arcpy.Raster(mask_raster)
    cell_area = float(ras.meanCellWidth) * float(ras.meanCellHeight)
    min_pixels = max(1, int(round(min_component_area_m2 / cell_area)))

    # 0을 NoData로 바꿔야 Shrink/Expand/RegionGroup이 "변화 영역"만 zone으로 다룬다.
    binary = arcpy.sa.SetNull(ras, 1, "VALUE = 0")

    work = binary
    if opening_cells > 0:
        work = arcpy.sa.Expand(arcpy.sa.Shrink(work, opening_cells, [1]), opening_cells, [1])
    if closing_cells > 0:
        work = arcpy.sa.Shrink(arcpy.sa.Expand(work, closing_cells, [1]), closing_cells, [1])

    regions = arcpy.sa.RegionGroup(work, "EIGHT", "WITHIN", "NO_LINK")
    counts = arcpy.sa.Lookup(regions, "COUNT")
    cleaned = arcpy.sa.Con(counts >= min_pixels, 1)

    delete_if_exists(out_raster)
    cleaned.save(out_raster)
    logger.info(
        "[CHANGE] 마스크 후처리 완료: %s (최소 %d픽셀 = %.0fm²)",
        out_raster, min_pixels, min_component_area_m2,
    )
    return out_raster


def polygonize(
    mask_raster: str,
    prob_raster: str,
    out_fc: str,
    t1_date: str,
    t2_date: str,
    method: str = "ensemble",
) -> str:
    """정리된 마스크를 Feature Class로 변환하고 변화 통계 필드를 채운다.

    change_id / change_area_m2 / mean_change_score / max_change_score /
    t1_date / t2_date / method 필드를 만든다 (Baseline 스키마와 동일).

    Returns:
        생성된 Feature Class 경로.
    """
    delete_if_exists(out_fc)
    arcpy.conversion.RasterToPolygon(
        in_raster=mask_raster,
        out_polygon_features=out_fc,
        simplify="NO_SIMPLIFY",     # 픽셀 경계를 유지해야 면적이 정확하다
        raster_field="Value",
        create_multipart_features="SINGLE_OUTER_PART",
    )

    count = int(arcpy.management.GetCount(out_fc)[0])
    if count == 0:
        logger.warning("[CHANGE] Polygon화 결과 0건")

    for name, ftype in [
        ("change_id", "TEXT"), ("change_area_m2", "DOUBLE"),
        ("mean_change_score", "DOUBLE"), ("max_change_score", "DOUBLE"),
        ("t1_date", "TEXT"), ("t2_date", "TEXT"), ("method", "TEXT"),
    ]:
        if not any(f.name == name for f in arcpy.ListFields(out_fc)):
            arcpy.management.AddField(out_fc, name, ftype, field_length=40 if ftype == "TEXT" else None)

    with arcpy.da.UpdateCursor(out_fc, ["OID@", "SHAPE@AREA", "change_id", "change_area_m2",
                                       "t1_date", "t2_date", "method"]) as cur:
        for i, row in enumerate(cur, start=1):
            row[2] = f"CHG_{i:05d}"
            row[3] = round(row[1], 2)
            row[4] = t1_date
            row[5] = t2_date
            row[6] = method
            cur.updateRow(row)

    # change_probability의 polygon별 평균/최대 - Baseline의 rasterstats에 해당
    zonal_tbl = f"{out_fc}_probstats"
    delete_if_exists(zonal_tbl)
    # statistics_type은 정해진 도메인만 받는다. MEAN과 MAX를 함께 얻으려면
    # "MEAN_MAX"가 아니라 "MIN_MAX_MEAN"을 써야 한다 (MIN은 쓰지 않고 버린다).
    arcpy.sa.ZonalStatisticsAsTable(
        in_zone_data=out_fc, zone_field="change_id",
        in_value_raster=prob_raster, out_table=zonal_tbl,
        ignore_nodata="DATA", statistics_type="MIN_MAX_MEAN",
    )
    stats = {
        r[0]: (r[1], r[2])
        for r in arcpy.da.SearchCursor(zonal_tbl, ["change_id", "MEAN", "MAX"])
    }
    with arcpy.da.UpdateCursor(out_fc, ["change_id", "mean_change_score", "max_change_score"]) as cur:
        for row in cur:
            mean_max = stats.get(row[0])
            if mean_max:
                row[1] = round(float(mean_max[0]), 4)
                row[2] = round(float(mean_max[1]), 4)
                cur.updateRow(row)
    delete_if_exists(zonal_tbl)

    logger.info("[CHANGE] Polygon화 완료: %s (%d건)", out_fc, count)
    return out_fc


def add_brightness_delta(
    change_fc: str,
    t1_raster: str,
    t2_raster: str,
    gdb: str,
) -> str:
    """change polygon별 T1→T2 밝기 변화(그레이스케일 평균)를 필드로 추가한다.

    change_probability는 변화의 **크기**만 담고 **방향**(밝아짐/어두워짐)은
    버린다. 그 때문에 "철거"로 분류된 곳에 실제로는 밝은 신축 구조물이
    나타난 사례가 육안검수에서 발견됐다
    (`outputs/reports/high_priority_visual_qa.md`의 CHG_00070). 방향을
    보조 근거로 남겨 classify가 그런 오분류를 걸러낼 수 있게 한다.
    """
    t1, meta = read_stack(t1_raster)
    t2, _ = read_stack(t2_raster)
    gray1_path = str(arcpy.CreateScratchName("g1", "", "RasterDataset", arcpy.env.scratchFolder))
    gray2_path = str(arcpy.CreateScratchName("g2", "", "RasterDataset", arcpy.env.scratchFolder))
    write_raster(to_grayscale(t1), meta, gray1_path)
    write_raster(to_grayscale(t2), meta, gray2_path)

    for name in ("brightness_t1", "brightness_t2", "brightness_delta"):
        if not any(f.name == name for f in arcpy.ListFields(change_fc)):
            arcpy.management.AddField(change_fc, name, "DOUBLE")

    values: dict[str, list[float | None]] = {}
    for slot, raster in (("t1", gray1_path), ("t2", gray2_path)):
        tbl = f"{gdb}\\_bright_{slot}"
        delete_if_exists(tbl)
        arcpy.sa.ZonalStatisticsAsTable(
            in_zone_data=change_fc, zone_field="change_id",
            in_value_raster=raster, out_table=tbl,
            ignore_nodata="DATA", statistics_type="MEAN",
        )
        for cid, mean in arcpy.da.SearchCursor(tbl, ["change_id", "MEAN"]):
            values.setdefault(cid, [None, None])[0 if slot == "t1" else 1] = mean
        delete_if_exists(tbl)

    with arcpy.da.UpdateCursor(
        change_fc, ["change_id", "brightness_t1", "brightness_t2", "brightness_delta"]
    ) as cur:
        for row in cur:
            pair = values.get(row[0])
            if pair and pair[0] is not None and pair[1] is not None:
                row[1] = round(float(pair[0]), 2)
                row[2] = round(float(pair[1]), 2)
                row[3] = round(float(pair[1]) - float(pair[0]), 2)
                cur.updateRow(row)

    delete_if_exists(gray1_path, gray2_path)
    logger.info("[CHANGE] brightness_delta 계산 완료: %s", change_fc)
    return change_fc
