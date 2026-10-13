"""BSI(Bare Soil Index) / 20m 복합 지표 스택.

BSI  = ((SWIR + RED) - (NIR + BLUE)) / ((SWIR + RED) + (NIR + BLUE))     SWIR=B11(20m), RED=B04, NIR=B08, BLUE=B02
MNDWI = (GREEN - SWIR) / (GREEN + SWIR)   수체/습윤 픽셀 오탐 보조 플래그

- B11의 공간해상도는 20m이므로 NDVI+BSI 복합 분석은 20m 격자에서 한다. 10m 밴드(B02/B03/B04/B08)는
  2x2 블록 평균으로 집계하며, 블록 내 유효 10m 픽셀이 aggregate_min_valid 미만이면 무효로 둔다.
  10m 원본 NDVI 결과(ndvi_YYYY.tif)는 그대로 유지한다. B11을 10m로 보간하지 않는다.
- 기존 10m 스택의 BOA offset은 이미 제거되어 있고(B11도 같은 규칙으로 제거해 저장) 여기서는 offset을
  다시 적용하지 않는다. DN/scale만 한다.
- 유효하지 않은 픽셀은 NaN이며 변화 없음(0)으로 대체하지 않는다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling, reproject, transform_bounds

from .processing import DST_CRS, YearScene, write_raster
from .swir import swir_path

logger = logging.getLogger(__name__)


def safe_norm_diff(a: np.ndarray, b: np.ndarray, min_den: float = 1e-3) -> np.ndarray:
    """(a-b)/(a+b). 분모 < min_den, 비유한 값, [-1,1] 밖은 NaN."""
    a = np.asarray(a, dtype="float64")
    b = np.asarray(b, dtype="float64")
    den = a + b
    out = np.full(a.shape, np.nan, dtype="float32")
    with np.errstate(divide="ignore", invalid="ignore"):
        v = (a - b) / den
    ok = np.isfinite(a) & np.isfinite(b) & (den >= min_den) & np.isfinite(v) & (v >= -1) & (v <= 1)
    out[ok] = v[ok].astype("float32")
    return out


def bsi_from_reflectance(swir, red, nir, blue, min_den: float = 1e-3) -> np.ndarray:
    return safe_norm_diff(np.asarray(swir) + np.asarray(red), np.asarray(nir) + np.asarray(blue), min_den)


def block_mean_2x2(a: np.ndarray, min_valid: int = 4) -> np.ndarray:
    """(2H,2W) -> (H,W) 평균. 유효(비NaN) 개수 < min_valid 이면 NaN."""
    h, w = a.shape[0] // 2, a.shape[1] // 2
    b = a[: h * 2, : w * 2].reshape(h, 2, w, 2).transpose(0, 2, 1, 3).reshape(h, w, 4)
    n = np.isfinite(b).sum(-1)
    s = np.nansum(b, axis=-1)
    out = np.full((h, w), np.nan, dtype="float32")
    ok = n >= min_valid
    out[ok] = (s[ok] / n[ok]).astype("float32")
    return out


def _place_10m_on_20m_grid(src, tr20: Affine, h20: int, w20: int) -> tuple[np.ndarray, dict]:
    """10m GeoTIFF(밴드 4개)를 20m 격자 범위를 덮는 (4, 2*h20, 2*w20) 배열로 배치(밖은 0)."""
    off_c = (src.transform.c - tr20.c) / 10.0
    off_r = (tr20.f - src.transform.f) / 10.0
    if abs(off_c - round(off_c)) > 1e-6 or abs(off_r - round(off_r)) > 1e-6:
        raise ValueError(f"10m/20m 격자 정합 불가: offset=({off_c},{off_r}) px가 정수가 아님")
    off_c, off_r = int(round(off_c)), int(round(off_r))
    full = np.zeros((src.count, 2 * h20, 2 * w20), dtype="float32")
    data = src.read().astype("float32")
    r0, c0 = max(off_r, 0), max(off_c, 0)
    r1, c1 = min(off_r + src.height, 2 * h20), min(off_c + src.width, 2 * w20)
    full[:, r0:r1, c0:c1] = data[:, r0 - off_r: r1 - off_r, c0 - off_c: c1 - off_c]
    return full, {"offset_col_px": off_c, "offset_row_px": off_r, "parity_aligned": True}


def build_composite_stack(scenes: list[YearScene], swir_dir: str | Path, scale: float = 10000.0,
                          aggregate_min_valid: int = 4, min_den: float = 1e-3, out_res: float = 20.0) -> dict:
    """연도별 20m NDVI/BSI/MNDWI 스택을 EPSG:5186 20m 격자에 만든다. B11 없는 연도는 available=False."""
    swir_dir = Path(swir_dir)
    # 대상 격자: 가용한 B11 파일 범위의 합집합을 5186 20m 격자(20m 배수 스냅)로
    files = {s.year: swir_path(swir_dir, s.year, s.scene_id or "") for s in scenes}
    avail_years = [y for y, p in files.items() if p.exists()]
    qa: dict = {"per_year": {}, "unavailable_years": [s.year for s in scenes if s.year not in avail_years]}
    if not avail_years:
        return {"years": [s.year for s in scenes], "available": {s.year: False for s in scenes}, "qa": qa,
                "bsi": None, "ndvi20": None, "mndwi": None, "transform": None}
    bs = []
    for y in avail_years:
        with rasterio.open(files[y]) as src:
            bs.append(transform_bounds(src.crs, DST_CRS, *src.bounds, densify_pts=21))
    minx = np.floor(min(b[0] for b in bs) / out_res) * out_res
    miny = np.floor(min(b[1] for b in bs) / out_res) * out_res
    maxx = np.ceil(max(b[2] for b in bs) / out_res) * out_res
    maxy = np.ceil(max(b[3] for b in bs) / out_res) * out_res
    tr_out = from_origin(minx, maxy, out_res, out_res)
    H, W = int(round((maxy - miny) / out_res)), int(round((maxx - minx) / out_res))

    def warp(arr: np.ndarray, src_tr: Affine, src_crs) -> np.ndarray:
        dst = np.full((H, W), np.nan, dtype="float32")
        reproject(arr.astype("float32"), dst, src_transform=src_tr, src_crs=src_crs, src_nodata=np.nan,
                  dst_transform=tr_out, dst_crs=DST_CRS, dst_nodata=np.nan, resampling=Resampling.nearest)
        return dst

    years = [s.year for s in scenes]
    bsi = np.full((len(years), H, W), np.nan, dtype="float32")
    nd = bsi.copy()
    mn = bsi.copy()
    for i, s in enumerate(scenes):
        if s.year not in avail_years:
            continue
        with rasterio.open(files[s.year]) as sw:
            b11 = sw.read(1).astype("float32")
            tr20, crs20 = sw.transform, sw.crs
            tags = sw.tags()
        h20, w20 = b11.shape
        with rasterio.open(s.path) as s10:
            full, align = _place_10m_on_20m_grid(s10, tr20, h20, w20)
            descr = list(s10.descriptions)
        idx = {n: descr.index(n) for n in ("B02", "B03", "B04", "B08")}
        v10 = (full[[idx["B02"], idx["B03"], idx["B04"], idx["B08"]]] > 0).all(0)
        refl = {}
        for n in ("B02", "B03", "B04", "B08"):
            a = np.where(v10, full[idx[n]] / scale, np.nan)
            refl[n] = block_mean_2x2(a, aggregate_min_valid)
        sw_r = np.where(b11 > 0, b11 / scale, np.nan).astype("float32")
        ok = np.isfinite(refl["B02"]) & np.isfinite(refl["B04"]) & np.isfinite(refl["B08"]) & np.isfinite(sw_r)
        for k in refl:
            refl[k] = np.where(ok, refl[k], np.nan)
        sw_r = np.where(ok, sw_r, np.nan)
        b = bsi_from_reflectance(sw_r, refl["B04"], refl["B08"], refl["B02"], min_den)
        n20 = safe_norm_diff(refl["B08"], refl["B04"], min_den)
        m = safe_norm_diff(refl["B03"], sw_r, min_den)
        bsi[i], nd[i], mn[i] = warp(b, tr20, crs20), warp(n20, tr20, crs20), warp(m, tr20, crs20)
        v = np.isfinite(b)
        qa["per_year"][s.year] = {
            "b11_tags": tags, "grid_alignment": align, "valid_fraction_20m_native": float(v.mean()),
            "bsi_p05": float(np.nanpercentile(b, 5)), "bsi_median": float(np.nanmedian(b)),
            "bsi_p95": float(np.nanpercentile(b, 95)),
            "bsi_min": float(np.nanmin(b)), "bsi_max": float(np.nanmax(b)),
            "swir_dn_p0.5": float(np.percentile(b11[b11 > 0] if (b11 > 0).any() else [0], 0.5)),
            "n_bsi_denominator_rejected": int((ok & ~v).sum()),
        }
    return {"years": years, "available": {y: (y in avail_years) for y in years}, "qa": qa, "bsi": bsi,
            "ndvi20": nd, "mndwi": mn, "transform": tr_out, "shape": (H, W), "res": out_res}


def write_stack_rasters(stack: dict, out_dir: str | Path, pairs: list[tuple[int, int]]) -> list[str]:
    out_dir = Path(out_dir)
    tr = stack["transform"]
    years = stack["years"]
    files = []
    for i, y in enumerate(years):
        if not stack["available"][y]:
            continue
        files.append(str(write_raster(out_dir / f"bsi_{y}.tif", stack["bsi"][i], tr, descr=f"BSI {y} (20m)")))
        files.append(str(write_raster(out_dir / f"ndvi20_{y}.tif", stack["ndvi20"][i], tr, descr=f"NDVI {y} (20m composite grid)")))
        files.append(str(write_raster(out_dir / f"mndwi_{y}.tif", stack["mndwi"][i], tr, descr=f"MNDWI {y} (20m)")))
    for a, b in pairs:
        ia, ib = years.index(a), years.index(b)
        if not (stack["available"][a] and stack["available"][b]):
            continue
        for key, nm in (("bsi", "delta_bsi"), ("ndvi20", "delta_ndvi20")):
            d = stack[key][ib] - stack[key][ia]   # NaN 전파 -> 무효 픽셀은 변화로 취급하지 않음
            files.append(str(write_raster(out_dir / f"{nm}_{a}_{b}.tif", d, tr, descr=f"{nm} {a}->{b}")))
    return files
