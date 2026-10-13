"""연도별 Sentinel-2 L2A NDVI 계산 + 공통 EPSG:5186 10m grid 정렬 + QA.

- 밴드 역할은 GeoTIFF band description -> src.change_detection.band_schema로 판별한다(인덱스 가정 금지).
- BOA offset은 scene_selection.json의 boa_offset_removed_dn에 이미 제거된 값으로 기록되어 있어
  여기서 다시 적용하지 않는다(중복 적용 금지). 일관성은 QA로 검증한다.
- NoData(0) 및 SCL 마스크 픽셀은 NaN으로 유지하고, 0이나 '변화 없음'으로 대체하지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling, reproject, transform_bounds

from src.change_detection.band_schema import determine_band_order, resolve_band_roles

logger = logging.getLogger(__name__)

DST_CRS = "EPSG:5186"   # 문자열로 유지: 모듈 임포트 시점에 PROJ DB를 열지 않는다(PROJ_LIB 충돌 회피)


@dataclass
class YearScene:
    year: int
    path: Path
    date: str | None = None
    scene_id: str | None = None
    platform: str | None = None
    processing_baseline: str | None = None
    boa_offset_removed_dn: int | None = None
    scene_cloud_cover_pct: float | None = None
    aoi_clear_fraction: float | None = None
    band_order: list[str] = field(default_factory=list)
    red_idx: int = -1
    nir_idx: int = -1


@dataclass
class NdviStack:
    years: list[int]
    ndvi: np.ndarray          # (T, H, W) float32, 무효=NaN
    transform: Affine
    crs: CRS
    scenes: list[YearScene]
    qa: dict


def ndvi_from_reflectance(red: np.ndarray, nir: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    """NDVI=(NIR-RED)/(NIR+RED). 무효/분모<=0/범위 밖([-1,1])은 NaN. 0 대체 없음."""
    red = np.asarray(red, dtype="float64")
    nir = np.asarray(nir, dtype="float64")
    den = nir + red
    ok = np.isfinite(red) & np.isfinite(nir) & (den > 0)
    if valid is not None:
        ok &= valid
    out = np.full(red.shape, np.nan, dtype="float32")
    with np.errstate(divide="ignore", invalid="ignore"):
        v = (nir - red) / den
    ok &= np.isfinite(v) & (v >= -1.0) & (v <= 1.0)
    out[ok] = v[ok].astype("float32")
    return out


def discover_scenes(imagery_dir: str | Path, years: list[int], selection_json: str | Path | None,
                    fallback_band_order: list[str]) -> list[YearScene]:
    """연도별 로컬 GeoTIFF를 찾고 scene_selection.json 메타데이터를 결합한다(다운로드 없음)."""
    imagery_dir = Path(imagery_dir)
    sel: dict = {}
    if selection_json and Path(selection_json).exists():
        sel = json.loads(Path(selection_json).read_text(encoding="utf-8"))
    scenes = []
    for y in years:
        cands = sorted(imagery_dir.glob(f"s2_{y}_*.tif"))
        if not cands:
            raise FileNotFoundError(f"[NDVI] {y}년 Sentinel-2 GeoTIFF가 없습니다: {imagery_dir}")
        p = cands[0]
        meta = sel.get(str(y), {})
        dt = meta.get("date")
        if not dt:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
            dt = m.group(1) if m else None
        order = determine_band_order(p, fallback_band_order)
        roles = resolve_band_roles(order)
        if "nir" not in roles:
            raise ValueError(f"[NDVI] {p.name}에 NIR 밴드가 없습니다: {order}")
        scenes.append(YearScene(
            year=y, path=p, date=dt, scene_id=meta.get("scene_id"), platform=meta.get("platform"),
            processing_baseline=meta.get("processing_baseline"),
            boa_offset_removed_dn=meta.get("boa_offset_removed_dn"),
            scene_cloud_cover_pct=meta.get("scene_cloud_cover_pct"),
            aoi_clear_fraction=meta.get("aoi_clear_fraction"),
            band_order=order, red_idx=roles["red"], nir_idx=roles["nir"],
        ))
    return scenes


def common_grid(scenes: list[YearScene], res: float, snap: bool = True) -> tuple[Affine, int, int]:
    """모든 연도 원본 범위의 합집합을 EPSG:5186 격자(res m)로 만든다."""
    bs = []
    for s in scenes:
        with rasterio.open(s.path) as src:
            bs.append(transform_bounds(src.crs, DST_CRS, *src.bounds, densify_pts=21))
    minx = min(b[0] for b in bs)
    miny = min(b[1] for b in bs)
    maxx = max(b[2] for b in bs)
    maxy = max(b[3] for b in bs)
    if snap:
        minx = np.floor(minx / res) * res
        miny = np.floor(miny / res) * res
        maxx = np.ceil(maxx / res) * res
        maxy = np.ceil(maxy / res) * res
    w = int(round((maxx - minx) / res))
    h = int(round((maxy - miny) / res))
    return from_origin(minx, maxy, res, res), h, w


def _warp_bands(path: Path, idxs: list[int], transform: Affine, h: int, w: int,
                resampling: Resampling) -> np.ndarray:
    out = np.zeros((len(idxs), h, w), dtype="float32")
    with rasterio.open(path) as src:
        for k, bi in enumerate(idxs):
            reproject(
                source=rasterio.band(src, bi + 1), destination=out[k],
                src_transform=src.transform, src_crs=src.crs, src_nodata=src.nodata,
                dst_transform=transform, dst_crs=DST_CRS, dst_nodata=0,
                resampling=resampling,
            )
    return out


def check_native_grids(scenes: list[YearScene]) -> dict:
    """원본 연도들의 CRS/transform/shape 동일성 검사(픽셀 단위 시계열 비교 가능성)."""
    ref = None
    items, same = [], True
    for s in scenes:
        with rasterio.open(s.path) as src:
            item = {"year": s.year, "epsg": src.crs.to_epsg(), "shape": [src.height, src.width],
                    "res": list(src.res), "transform": list(src.transform)[:6], "nodata": src.nodata,
                    "dtype": src.dtypes[0], "descriptions": list(src.descriptions)}
        if ref is None:
            ref = item
        same &= (item["epsg"] == ref["epsg"] and item["shape"] == ref["shape"]
                 and bool(np.allclose(item["transform"], ref["transform"])))
        items.append(item)
    return {"identical_native_grid": bool(same), "per_year": items}


def build_ndvi_stack(scenes: list[YearScene], res: float = 10.0, scale: float = 10000.0,
                     snap: bool = True, resampling: str = "nearest",
                     reflectance_warn_max: float = 1.5) -> NdviStack:
    transform, h, w = common_grid(scenes, res, snap)
    rs = getattr(Resampling, resampling)
    qa: dict = {"native_grid": check_native_grids(scenes), "per_year": {}}
    arrs = []
    for s in scenes:
        dn = _warp_bands(s.path, [s.red_idx, s.nir_idx], transform, h, w, rs)
        # 원본 NoData(0)는 SCL 마스크/타일 외곽을 의미한다. 한 밴드라도 0이면 무효.
        valid = (dn[0] > 0) & (dn[1] > 0)
        red = np.where(valid, dn[0] / scale, np.nan)
        nir = np.where(valid, dn[1] / scale, np.nan)
        nd = ndvi_from_reflectance(red, nir, valid)
        arrs.append(nd)
        v = np.isfinite(nd)
        red_dn = dn[0][valid]
        nir_dn = dn[1][valid]
        qa["per_year"][s.year] = {
            "date": s.date, "scene_id": s.scene_id, "platform": s.platform,
            "processing_baseline": s.processing_baseline,
            "boa_offset_removed_dn_recorded": s.boa_offset_removed_dn,
            "offset_policy": "scene_selection.json에 기록된 값은 이미 제거됨 - 재적용하지 않음",
            "red_band_index_1based": s.red_idx + 1, "nir_band_index_1based": s.nir_idx + 1,
            "band_order": s.band_order,
            "scene_cloud_cover_pct": s.scene_cloud_cover_pct, "aoi_clear_fraction": s.aoi_clear_fraction,
            "valid_pixels": int(v.sum()), "grid_pixels": int(v.size),
            "valid_fraction_of_grid": float(v.mean()),
            "ndvi_min": float(np.nanmin(nd)) if v.any() else None,
            "ndvi_max": float(np.nanmax(nd)) if v.any() else None,
            "ndvi_median": float(np.nanmedian(nd)) if v.any() else None,
            "ndvi_p05": float(np.nanpercentile(nd, 5)) if v.any() else None,
            "ndvi_p95": float(np.nanpercentile(nd, 95)) if v.any() else None,
            "red_dn_p0.5": float(np.percentile(red_dn, 0.5)) if red_dn.size else None,
            "reflectance_over_warn_fraction": float(((nir_dn / scale) > reflectance_warn_max).mean())
            if nir_dn.size else None,
        }
    st = np.stack(arrs).astype("float32")
    years = [s.year for s in scenes]

    # offset 일관성: processing baseline >= 04.00 이면 offset 1000이 제거되어 있어야 한다.
    issues = []
    for s in scenes:
        try:
            pb = float(s.processing_baseline) if s.processing_baseline else None
        except ValueError:
            pb = None
        if pb is not None and s.boa_offset_removed_dn is not None:
            expect = 1000 if pb >= 4.0 else 0
            if s.boa_offset_removed_dn != expect:
                issues.append(f"{s.year}: baseline {s.processing_baseline}, offset {s.boa_offset_removed_dn} != {expect}")
    p = [qa["per_year"][y]["red_dn_p0.5"] for y in years if qa["per_year"][y]["red_dn_p0.5"] is not None]
    qa["offset_consistency"] = {
        "metadata_issues": issues,
        "red_dn_p0.5_by_year": {str(y): qa["per_year"][y]["red_dn_p0.5"] for y in years},
        "red_dn_p0.5_spread": float(max(p) - min(p)) if p else None,
        "note": "p0.5 spread이 ~1000 DN에 근접하면 offset 불일치 의심. 기록된 offset 제거를 신뢰하되 값을 병기한다.",
    }
    # 날짜/센서 차이 QA
    doy = {}
    for s in scenes:
        if s.date:
            doy[str(s.year)] = date.fromisoformat(s.date).timetuple().tm_yday
    qa["acquisition"] = {
        "day_of_year_by_year": doy,
        "day_of_year_range_days": int(max(doy.values()) - min(doy.values())) if doy else None,
        "platforms": sorted({s.platform for s in scenes if s.platform}),
        "note": "촬영일(DOY) 편차와 센서(S2A/S2B/S2C) 차이는 NDVI 변화량에 계절/센서 편향을 만들 수 있으며 보정하지 않았다.",
    }
    # 인접 연도 NDVI 상관(정합 sanity check)
    cors = {}
    for i in range(len(years) - 1):
        a, b = st[i], st[i + 1]
        m = np.isfinite(a) & np.isfinite(b)
        cors[f"{years[i]}_{years[i + 1]}"] = float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 10 else None
    qa["adjacent_year_ndvi_correlation"] = cors
    qa["grid"] = {"epsg": 5186, "res_m": res, "shape": [h, w], "transform": list(transform)[:6]}
    with rasterio.Env():
        crs = CRS.from_string(DST_CRS)
    return NdviStack(years, st, transform, crs, scenes, qa)


def write_raster(path: str | Path, arr: np.ndarray, transform: Affine, dtype: str = "float32",
                 nodata: float | int | None = -9999.0, descr: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = arr
    if dtype.startswith("float") and nodata is not None:
        data = np.where(np.isfinite(arr), arr, nodata)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype=dtype, crs=DST_CRS, transform=transform, nodata=nodata,
                       compress="deflate") as dst:
        dst.write(data.astype(dtype), 1)
        if descr:
            dst.set_band_description(1, descr)
    return path
