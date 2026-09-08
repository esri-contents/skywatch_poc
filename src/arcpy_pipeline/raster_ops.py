"""Raster 전처리 (arcpy) - 재투영 → AOI Clip → 밴드 스택 → 정합 검증.

Baseline(`src/preprocessing/raster_preprocess.py`)의 rasterio 구현을 arcpy로
옮긴 것이다. 기능은 같지만 arcpy 쪽이 유리한 점이 둘 있다:

1. **grid 일치를 도구가 보장한다.** rasterio 구현은 T1/T2를 각각 독립적으로
   재투영한 뒤 "shape/transform이 같은지" 사후 검사했다. arcpy는
   `arcpy.env.snapRaster`로 T2를 T1 격자에 강제로 스냅시킬 수 있어, 애초에
   어긋난 grid가 만들어지지 않는다. 시기가 3개 이상으로 늘어나는
   progress_monitor(REQ04)에서 특히 중요하다.
2. **README에 기록된 Windows PROJ_LIB 충돌이 원천적으로 없다.** arcpy는
   ArcGIS Pro가 자체 관리하는 투영 엔진을 쓰므로 시스템 전역 PROJ_LIB
   환경변수의 영향을 받지 않는다.

정합 검증(STEP 8)은 cv2.findTransformECC 대신 **FFT 위상상관(phase
correlation)** 으로 구현한다 - ArcGIS Pro 파이썬 환경에 OpenCV가 없고,
평행이동 추정이라는 목적에는 위상상관이 표준적이며 numpy만으로 충분하다.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import arcpy
import numpy as np

from .env import add_field_if_missing, analysis_sr, delete_if_exists

logger = logging.getLogger("arcpy_pipeline.raster_ops")

BAND_ORDER = ["B02", "B03", "B04", "B08"]  # Blue, Green, Red, NIR - Baseline과 동일


def project_and_clip(
    in_raster: str,
    aoi_fc: str,
    out_raster: str,
    cell_size: float | None = None,
    snap_raster: str | None = None,
    resampling: str = "BILINEAR",
) -> str:
    """단일 밴드를 분석 좌표계로 재투영하고 AOI로 clip한다.

    Args:
        in_raster: 원본 밴드 GeoTIFF (Sentinel-2는 EPSG:32652).
        aoi_fc: AOI Feature Class / GPKG 레이어.
        out_raster: 저장 경로.
        cell_size: 출력 셀 크기(m). None이면 arcpy가 재투영 결과에서 정한다.
        snap_raster: 격자를 맞출 기준 래스터. T2를 T1에 정확히 정렬시킬 때 지정.
        resampling: 연속형 값이므로 기본 BILINEAR (분류 래스터라면 NEAREST).

    Returns:
        저장된 래스터 경로.
    """
    # 임시 이름은 반드시 stem 기준으로 만든다. f"{out_raster}_proj.tif"처럼
    # 확장자 뒤에 덧붙이면 "t1_stack.tif_proj.tif"가 되어 이름 중간에 점이
    # 들어가고, arcpy가 ERROR 000354(이름에 유효하지 않은 문자)로 거부한다.
    out_p = Path(out_raster)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    tmp_proj = str(out_p.with_name(f"{out_p.stem}_proj{out_p.suffix or '.tif'}"))
    delete_if_exists(tmp_proj, out_raster)

    previous_snap = arcpy.env.snapRaster
    try:
        if snap_raster:
            arcpy.env.snapRaster = snap_raster
        kwargs = {}
        if cell_size:
            kwargs["cell_size"] = f"{cell_size} {cell_size}"
        arcpy.management.ProjectRaster(
            in_raster=in_raster,
            out_raster=tmp_proj,
            out_coor_system=analysis_sr(),
            resampling_type=resampling,
            **kwargs,
        )
        clipped = arcpy.sa.ExtractByMask(tmp_proj, aoi_fc)
        clipped.save(out_raster)
    finally:
        arcpy.env.snapRaster = previous_snap
        delete_if_exists(tmp_proj)

    logger.info("[RASTER] 재투영+clip 완료: %s", out_raster)
    return out_raster


def build_stacked_scene(
    band_paths: dict[str, str],
    aoi_fc: str,
    out_raster: str,
    band_order: list[str] = BAND_ORDER,
    cell_size: float | None = 10.0,
    snap_raster: str | None = None,
) -> str:
    """밴드별 GeoTIFF를 재투영·clip 후 하나의 다중밴드 래스터로 합친다.

    band_order를 항상 [Blue, Green, Red, NIR]로 고정해, change_detect가
    밴드 인덱스를 고정적으로 참조할 수 있게 한다 (Baseline과 동일 규약).

    Args:
        band_paths: {"B02": path, ...}.
        aoi_fc: AOI Feature Class.
        out_raster: 저장할 스택 래스터 경로.
        band_order: 최종 밴드 순서.
        cell_size: 출력 셀 크기(m).
        snap_raster: T1 스택을 넘기면 T2가 동일 격자로 정렬된다.

    Returns:
        저장된 스택 래스터 경로.
    """
    missing = [b for b in band_order if b not in band_paths]
    if missing:
        raise ValueError(f"[RASTER] 다음 밴드가 없습니다: {missing}")

    out_path = Path(out_raster)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_path.parent / "_tmp_bands"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    prepared = []
    # 첫 밴드를 만든 뒤 그것을 snapRaster로 삼아 나머지 밴드를 정렬한다.
    # 밴드끼리 격자가 어긋나면 CompositeBands가 실패하거나 조용히 리샘플링한다.
    local_snap = snap_raster
    for band in band_order:
        band_out = str(tmp_dir / f"{band}.tif")
        project_and_clip(
            str(band_paths[band]), aoi_fc, band_out,
            cell_size=cell_size, snap_raster=local_snap,
        )
        if local_snap is None:
            local_snap = band_out
        prepared.append(band_out)

    delete_if_exists(str(out_path))
    arcpy.management.CompositeBands(prepared, str(out_path))

    for p in prepared:
        delete_if_exists(p)

    logger.info("[RASTER] 스택 저장 완료: %s (밴드=%s)", out_path, band_order)
    return str(out_path)


def read_stack(raster_path: str) -> tuple[np.ndarray, dict]:
    """다중밴드 래스터를 (bands, H, W) float32 배열 + 공간정보 dict로 읽는다.

    NoData는 np.nan으로 바꿔 반환한다 - Sentinel-2 L2A의 NoData가 0이라
    0을 유효값으로 두면 AOI 바깥이 "완전히 검은 픽셀"로 취급되어 변화
    통계를 왜곡한다(Baseline에서 valid_mask로 처리하던 것과 같은 문제).

    **정수형 래스터 주의(실측 확인)**: `RasterToNumPyArray(nodata_to_value=np.nan)`은
    Sentinel-2 같은 uint16 원본에 대해 "정수 유형은 NaN이나 무한 값을 지원하지
    않습니다" ValueError를 던진다 - 합성 테스트 데이터를 전부 float32로 만들어
    검증했을 때는 드러나지 않다가 실제 위성영상으로 처음 돌렸을 때 나온 문제다.
    그래서 원본 dtype 그대로 읽어 float32로 변환한 뒤, NoData 치환은 numpy
    비교로 그 다음에 한다(arcpy 쪽에 맡기지 않는다).

    Returns:
        (arr, meta) - meta는 lower_left(arcpy.Point), cell_w, cell_h,
        spatial_reference, band_count, shape 를 담는다.
    """
    desc = arcpy.Describe(raster_path)
    ras = arcpy.Raster(raster_path)
    nodata = ras.noDataValue

    arr = arcpy.RasterToNumPyArray(raster_path)
    if arr.ndim == 2:
        arr = arr[np.newaxis, ...]
    arr = arr.astype(np.float32)
    if nodata is not None:
        arr = np.where(arr == float(nodata), np.nan, arr)

    meta = {
        "lower_left": arcpy.Point(ras.extent.XMin, ras.extent.YMin),
        "cell_w": float(ras.meanCellWidth),
        "cell_h": float(ras.meanCellHeight),
        "spatial_reference": desc.spatialReference,
        "band_count": arr.shape[0],
        "shape": arr.shape[1:],
        "extent": ras.extent,
    }
    return arr, meta


def write_raster(
    arr: np.ndarray,
    meta: dict,
    out_path: str,
    nodata: float = -9999.0,
) -> str:
    """numpy 배열을 meta의 공간정보로 GeoTIFF에 쓴다.

    NaN은 nodata 값으로 치환한다 (arcpy.NumPyArrayToRaster는 NaN을
    자동으로 NoData로 다루지 않는다).
    """
    out = np.where(np.isfinite(arr), arr, nodata).astype(np.float32)
    ras = arcpy.NumPyArrayToRaster(
        out,
        lower_left_corner=meta["lower_left"],
        x_cell_size=meta["cell_w"],
        y_cell_size=meta["cell_h"],
        value_to_nodata=nodata,
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    delete_if_exists(out_path)
    ras.save(out_path)
    arcpy.management.DefineProjection(out_path, meta["spatial_reference"])
    return out_path


def to_grayscale(stack: np.ndarray, band_order: list[str] = BAND_ORDER) -> np.ndarray:
    """RGB 밴드 평균 그레이스케일 (Baseline `baseline.to_grayscale`과 동일 정의).

    AOI 경계 픽셀은 재투영 리샘플링 특성상 밴드마다 유효/NoData 여부가
    미세하게 갈릴 수 있어, 세 밴드가 전부 NaN인 위치(np.nanmean 기준
    "empty slice")가 실제 위성영상에서 실측 확인됐다. 그 결과 자체는
    의도대로 NaN이 맞으므로(경계 밖은 여전히 무효 픽셀), RuntimeWarning만
    조용히 억제한다 - 매 실행마다 콘솔에 뜨면 실제 문제로 오인하기 쉽다.
    """
    idx = {b: i for i, b in enumerate(band_order)}
    rgb = stack[[idx["B04"], idx["B03"], idx["B02"]]]
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(rgb, axis=0)


def verify_alignment(
    gray1: np.ndarray,
    gray2: np.ndarray,
    pixel_size_m: float,
) -> dict:
    """FFT 위상상관으로 T1→T2 평행이동을 추정해 정합 오차를 정량화한다 (STEP 8).

    Baseline은 cv2.findTransformECC를 썼지만 ArcGIS Pro 파이썬 환경에는
    OpenCV가 없다. 위상상관은 두 영상의 교차파워스펙트럼 역FFT에서 피크
    위치를 찾는 고전적 방법으로, 평행이동 추정에는 ECC와 동등하게 쓰인다.
    피크 주변 3점 포물선 보간으로 sub-pixel까지 추정한다.

    Args:
        gray1: (H, W) T1 그레이스케일.
        gray2: (H, W) T2 그레이스케일.
        pixel_size_m: 픽셀 한 변 길이(m).

    Returns:
        dx_px, dy_px, displacement_px, displacement_m, peak_ratio, converged.
        peak_ratio는 피크의 뚜렷함(최대값/차순위값)으로, 1에 가까우면 추정이
        신뢰할 수 없다는 뜻이다 - ECC의 ecc_score에 대응하는 품질 지표.
    """
    if gray1.shape != gray2.shape:
        raise ValueError(f"[ALIGN] T1/T2 shape이 다릅니다: {gray1.shape} vs {gray2.shape}")

    a = np.nan_to_num(gray1.astype(np.float64))
    b = np.nan_to_num(gray2.astype(np.float64))
    a = a - a.mean()
    b = b - b.mean()

    # Hann 창으로 경계 불연속(FFT의 순환 가정 위반)이 만드는 십자 인공물을 억제.
    h, w = a.shape
    window = np.outer(np.hanning(h), np.hanning(w))
    a *= window
    b *= window

    fa = np.fft.fft2(a)
    fb = np.fft.fft2(b)
    cross = fa * np.conj(fb)
    denom = np.abs(cross)
    denom[denom < 1e-12] = 1e-12
    corr = np.real(np.fft.ifft2(cross / denom))

    peak_idx = int(np.argmax(corr))
    py, px = np.unravel_index(peak_idx, corr.shape)
    peak_val = corr[py, px]

    second = corr.copy()
    second[max(0, py - 2):py + 3, max(0, px - 2):px + 3] = -np.inf
    second_val = float(second.max())
    peak_ratio = float(peak_val / second_val) if second_val > 0 else float("inf")

    dy = _subpixel_offset(corr[:, px], py, corr.shape[0])
    dx = _subpixel_offset(corr[py, :], px, corr.shape[1])

    displacement_px = float(np.hypot(dx, dy))
    result = {
        "dx_px": float(dx),
        "dy_px": float(dy),
        "displacement_px": displacement_px,
        "displacement_m": displacement_px * pixel_size_m,
        "peak_ratio": peak_ratio,
        "converged": bool(np.isfinite(peak_val) and peak_val > 0),
        "method": "fft_phase_correlation",
    }
    logger.info(
        "[ALIGN] dx=%.3fpx dy=%.3fpx displacement=%.3fpx(%.2fm) peak_ratio=%.2f",
        result["dx_px"], result["dy_px"], displacement_px,
        result["displacement_m"], peak_ratio,
    )
    return result


def _subpixel_offset(line: np.ndarray, peak: int, size: int) -> float:
    """1D 상관 프로파일에서 포물선 보간으로 sub-pixel 피크 위치를 구한다."""
    prev_v = line[(peak - 1) % size]
    curr = line[peak]
    next_v = line[(peak + 1) % size]
    denom = prev_v - 2 * curr + next_v
    delta = 0.0 if abs(denom) < 1e-12 else 0.5 * (prev_v - next_v) / denom
    shift = peak + delta
    # FFT 결과는 [0, size) 이므로 절반을 넘으면 음의 평행이동으로 해석한다.
    if shift > size / 2:
        shift -= size
    return float(shift)


def zonal_mean_table(
    zone_fc: str,
    zone_field: str,
    value_raster: str,
    out_table: str,
    statistics: str = "ALL",
) -> str:
    """ZonalStatisticsAsTable 래퍼 (Baseline의 rasterstats.zonal_stats 대체).

    rasterstats와 달리 arcpy는 결과를 GDB 테이블로 남기므로, 이후 JoinField로
    Feature Class에 바로 붙일 수 있다.
    """
    delete_if_exists(out_table)
    arcpy.sa.ZonalStatisticsAsTable(
        in_zone_data=zone_fc,
        zone_field=zone_field,
        in_value_raster=value_raster,
        out_table=out_table,
        ignore_nodata="DATA",
        statistics_type=statistics,
    )
    return out_table
