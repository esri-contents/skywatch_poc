"""입력 영상 정리 - 형식·좌표 상태와 관계없이 판독용 GeoTIFF(분석 좌표계, 0.25m RGB)로 맞춘다.

ArcGIS Pro Add-in(광명시흥 판독)이 받은 항공사진을 파이프라인에 넣기 전에 쓴다.
  1) 형식: 분석 파이썬의 rasterio가 못 여는 형식(ECW·MrSID 등)이나 0.25m보다 훨씬 촘촘한 원본은
     ArcGIS Pro 파이썬의 GDAL(gdal_convert.py)로 RGB 8비트 GeoTIFF로 바꾼다(원본 피라미드로 0.25m까지 축소).
  2) 좌표 상태를 판정한다.
     crs    좌표계와 좌표가 모두 있음 - 그대로 재투영
     coords 좌표는 있으나 좌표계가 없거나 LOCAL_CS(2022 ECW가 이 경우) - 범위가 지구와 겹치면 분석 좌표계로 간주
     none   좌표 없음(2024 JPG가 이 경우) - 기준 영상에 2m SIFT+RANSAC 유사변환으로 초기 좌표를 준다
  3) 지구 경계+여백 범위를 0.25m 격자로 재표본한다.
  4) 기준 영상과 0.25m 타일 기울기영상 위상상관으로 잔여 이동량을 재고, 중앙값만큼 좌표를 옮긴다.
     2024 JPG 지오레퍼런싱(raw/imagery/georef_2024_report.json)과 같은 방법이다.
"""

from __future__ import annotations

import json
import subprocess
import warnings
from pathlib import Path

import cv2
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from skimage.registration import phase_cross_correlation

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)  # 좌표 없는 영상은 정상 입력

FORMATS = "GeoTIFF·ECW·JPEG2000·MrSID·IMG·JPG/PNG(+월드파일)"
CONVERT = Path(__file__).with_name("gdal_convert.py")


class IngestError(RuntimeError):
    pass


def pro_python() -> Path | None:
    """ArcGIS Pro 기본 파이썬(arcgispro-py3) 경로. 없으면 None."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ESRI\ArcGISPro") as k:
            root = Path(winreg.QueryValueEx(k, "InstallDir")[0])
    except OSError:
        root = Path(r"C:\Program Files\ArcGIS\Pro")
    p = root / "bin" / "Python" / "envs" / "arcgispro-py3" / "python.exe"
    return p if p.exists() else None


def normalize_format(src: str, dst: Path, res: float, pro: Path | None = None, log=print) -> tuple[str, dict | None]:
    """분석 파이썬으로 읽을 수 있는 경로를 돌려준다. 변환이 필요하면 Pro 파이썬으로 dst에 만든다."""
    pro = pro or pro_python()
    try:
        with rasterio.open(src) as ds:
            px = pixel_m(ds)
            if px is not None and px < res * 0.8:
                why = f"원본 {px:.3g}m → {res}m 축소"
            elif ds.driver in ("JPEG", "PNG") and pro is not None:  # 타일이 없는 형식은 블록 재표본이 느려 먼저 바꾼다
                why = f"{ds.driver} → 타일 GeoTIFF"
            else:
                return src, None
    except rasterio.errors.RasterioIOError:
        why = "분석 환경 GDAL이 읽지 못하는 형식"
    if pro is None:
        raise IngestError(f"{Path(src).name}: {why} - ArcGIS Pro 파이썬(arcgispro-py3)이 필요함")
    log(f"   {why}: ArcGIS Pro GDAL로 변환")
    dst.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([str(pro), "-I", str(CONVERT), src, str(dst), str(res)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=_clean_env())
    if p.returncode != 0:
        raise IngestError(f"형식 변환 실패({FORMATS} 지원): {p.stderr.strip().splitlines()[-1] if p.stderr.strip() else p.returncode}")
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    return str(dst), json.loads(lines[-1])


def pixel_m(ds) -> float | None:
    """화소 크기(m). 경위도면 영상 중심 위도로 환산, 좌표계가 없으면 좌표 단위를 m로 본다. 좌표가 없으면 None."""
    if ds.transform.is_identity:
        return None
    px = float(np.hypot(ds.transform.a, ds.transform.d))
    if ds.crs and ds.crs.is_geographic:
        lat = (ds.bounds.bottom + ds.bounds.top) / 2
        return px * 111_320 * max(np.cos(np.radians(lat)), 0.1)
    if ds.crs and ds.crs.is_projected:
        return px * ds.crs.linear_units_factor[1]
    return px


def _clean_env() -> dict:
    import os
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def georef_state(ds, aoi_bounds: tuple) -> str:
    """'crs' / 'coords' / 'none'. aoi_bounds는 분석 좌표계 기준."""
    if ds.crs and (ds.crs.is_projected or ds.crs.is_geographic) and not ds.transform.is_identity:
        return "crs"
    if not ds.transform.is_identity:
        l, b, r, t = ds.bounds
        al, ab, ar, at = aoi_bounds
        if l < ar and r > al and min(b, t) < at and max(b, t) > ab:
            return "coords"
    return "none"


def _gray(a: np.ndarray) -> np.ndarray:
    return a[:3].astype(np.float32).mean(0)


def _u8(g: np.ndarray) -> np.ndarray:
    v = g[g > 0]
    lo, hi = np.percentile(v, [1, 99]) if v.size else (0, 255)
    return np.clip((g - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)


def _bands(ds) -> list[int]:
    return [1, 2, 3] if ds.count >= 3 else [1, 1, 1]


def coarse_register(src: str, ref: str, bounds: tuple, res: float = 2.0, max_px: int = 4000, log=print) -> tuple[Affine, dict]:
    """좌표 없는 영상의 화소→지도 변환(전체 해상도 기준)을 기준 영상과 SIFT 대응점으로 추정한다."""
    with rasterio.open(ref) as r:
        w, h = int((bounds[2] - bounds[0]) / res), int((bounds[3] - bounds[1]) / res)
        ra = r.read([1, 2, 3], window=from_bounds(*bounds, r.transform), boundless=True, out_shape=(3, h, w),
                    resampling=Resampling.average)
        ref_t = Affine(res, 0, bounds[0], 0, -res, bounds[3])
    with rasterio.open(src) as s:
        f = max(1.0, max(s.width, s.height) / max_px)
        sw, sh = int(s.width / f), int(s.height / f)
        sa = s.read(_bands(s), out_shape=(3, sh, sw), resampling=Resampling.average)
        fx, fy = s.width / sw, s.height / sh
    sift = cv2.SIFT_create(nfeatures=20000)
    k1, d1 = sift.detectAndCompute(_u8(_gray(sa)), None)
    k2, d2 = sift.detectAndCompute(_u8(_gray(ra)), None)
    if d1 is None or d2 is None:
        raise IngestError("자동 정합 실패: 특징점을 찾지 못함")
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        raise IngestError(f"자동 정합 실패: 대응점 {len(good)}개(12개 미만) - 기준 영상(광명시흥 지구)과 같은 지역인지 확인")
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    M, inl = cv2.estimateAffinePartial2D(p1, p2, method=cv2.RANSAC, ransacReprojThreshold=3.0, maxIters=5000)
    n_in = int(inl.sum()) if inl is not None else 0
    if M is None or n_in < 30:
        raise IngestError(f"자동 정합 실패: 정상 대응점 {n_in}개(30개 미만)")
    ok = inl.ravel() == 1
    resid = np.hypot(*(cv2.transform(p1[ok][None], M)[0] - p2[ok]).T)
    full = ref_t * Affine(*M[0], *M[1]) * Affine.scale(1 / fx, 1 / fy)  # 원본 화소 → 축소 화소 → 기준 화소 → 지도
    stats = {"inliers": n_in, "matches": len(good), "pixel_m": float(np.hypot(full.a, full.d)),
             "rot_deg": float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), "resid_px_median": float(np.median(resid))}
    log(f"   SIFT 초기 정합: 정상 대응점 {n_in}/{len(good)}, 화소 {stats['pixel_m']:.3f}m, 회전 {stats['rot_deg']:.3f}°")
    return full, stats


def warp(src: str, out: Path, bounds: tuple, res: float, crs: str, state: str,
         transform: Affine | None = None, block: int = 2048, log=print) -> Path:
    """분석 좌표계 res 격자, RGB uint8 타일 GeoTIFF로 재투영·재표본한다(블록 단위)."""
    w, h = int(round((bounds[2] - bounds[0]) / res)), int(round((bounds[3] - bounds[1]) / res))
    dst_t = Affine(res, 0, bounds[0], 0, -res, bounds[3])
    out.parent.mkdir(parents=True, exist_ok=True)
    prof = {"driver": "GTiff", "dtype": "uint8", "count": 3, "width": w, "height": h, "crs": CRS.from_user_input(crs),
            "transform": dst_t, "tiled": True, "blockxsize": 256, "blockysize": 256, "compress": "deflate",
            "predictor": 2, "BIGTIFF": "IF_SAFER"}
    with rasterio.open(src) as ds:
        kw = {"src_crs": ds.crs if state == "crs" else CRS.from_user_input(crs)}
        if transform is not None:
            kw["src_transform"] = transform
        with WarpedVRT(ds, crs=crs, transform=dst_t, width=w, height=h, resampling=Resampling.average,
                       nodata=0, **kw) as vrt, rasterio.open(out, "w", **prof) as o:
            idx = _bands(ds)
            # 화소 변화탐지(band_schema.determine_band_order)는 밴드 설명으로 RGB 역할을 찾는다 - 없으면 Sentinel-2 기본값으로 넘어가 실패
            o.descriptions = ("red", "green", "blue")
            n = (h + block - 1) // block
            for i, r0 in enumerate(range(0, h, block)):
                win = Window(0, r0, w, min(block, h - r0))
                o.write(vrt.read(idx, window=win), window=win)
                if (i + 1) * 10 // n != i * 10 // n:
                    log(f"   재표본 {(i + 1) * 100 // n}%")
    return out


def ensure_rgb_descriptions(path: str | Path) -> None:
    """정리본에 red/green/blue 밴드 설명이 없으면 단다(설명이 없던 이전 캐시 영상 보정)."""
    with rasterio.open(path) as d:
        if d.descriptions == ("red", "green", "blue"):
            return
    with rasterio.open(path, "r+") as d:
        d.descriptions = ("red", "green", "blue")


def _grad(a: np.ndarray) -> np.ndarray:
    g = a.astype(np.float32).mean(0)
    gy, gx = np.gradient(cv2.GaussianBlur(g, (0, 0), 1.0))
    return np.hypot(gx, gy)


def tile_shifts(img: str, ref: str, aoi: gpd.GeoDataFrame, res: float = 0.25, tile_px: int = 512, step_m: float = 400.0) -> np.ndarray:
    """지구 안 격자 타일마다 (dx, dy, 오차) - 영상을 (dx, dy)m 옮기면 기준 영상과 맞는다.

    두 영상을 같은 격자로 재표본(WarpedVRT, 쌍선형)해 정수 창으로 읽는다. 각자 격자에서 from_bounds 창을 읽으면
    창 위치가 화소 단위로 반올림돼 0.25m 미만 이동을 잴 수 없다.
    """
    half = tile_px * res / 2
    l, b, r, t = aoi.total_bounds
    grid_t = Affine(res, 0, l, 0, -res, t)
    w, h = int((r - l) / res), int((t - b) / res)
    geom = aoi.union_all()
    out = []
    with rasterio.open(img) as a0, rasterio.open(ref) as c0, \
            WarpedVRT(a0, crs=c0.crs, transform=grid_t, width=w, height=h, resampling=Resampling.bilinear) as a, \
            WarpedVRT(c0, crs=c0.crs, transform=grid_t, width=w, height=h, resampling=Resampling.bilinear) as c:
        for x in np.arange(l + half, r - half, step_m):
            for y in np.arange(b + half, t - half, step_m):
                if not geom.contains(gpd.points_from_xy([x], [y])[0]):
                    continue
                win = Window(int(round((x - half - l) / res)), int(round((t - y - half) / res)), tile_px, tile_px)
                ia, ic = a.read([1, 2, 3], window=win), c.read([1, 2, 3], window=win)
                if (ia.max(0) == 0).mean() > 0.2 or (ic.max(0) == 0).mean() > 0.2:
                    continue
                ga, gc = _grad(ia), _grad(ic)
                if ga.std() < 1 or gc.std() < 1:
                    continue
                (sr, sc), err, _ = phase_cross_correlation(gc, ga, upsample_factor=10, normalization=None)
                out.append((sc * res, -sr * res, err))
    return np.array(out, dtype=float).reshape(-1, 3)


def fine_shift(sh: np.ndarray, max_iter: int = 5) -> dict:
    """타일 이동량에서 이상치(중앙값에서 3×1.4826×MAD, 최소 0.75m 밖)를 반복 제거하고 평행이동을 적합한다."""
    keep = np.ones(len(sh), bool)
    for _ in range(max_iter):
        med = np.median(sh[keep, :2], axis=0)
        d = np.hypot(*(sh[:, :2] - med).T)
        new = d <= max(3 * 1.4826 * np.median(d[keep]), 0.75)
        if (new == keep).all():
            break
        keep = new
    med = np.median(sh[keep, :2], axis=0)
    d = np.hypot(*(sh[keep, :2] - med).T)
    return {"dx": float(med[0]), "dy": float(med[1]), "n": int(keep.sum()), "tiles": int(len(sh)),
            "rmse_m": float(np.sqrt((d ** 2).mean())), "median_m": float(np.median(d)),
            "p90_m": float(np.percentile(d, 90)), "outlier_share": float(1 - keep.mean())}


def prepare(src: str, out: Path, ref: str, aoi_path: str, crs: str = "EPSG:5186", res: float = 0.25,
            margin_m: float = 50.0, pro: Path | None = None, log=print) -> dict:
    """입력 영상 하나를 판독용 GeoTIFF로 만든다. 반환: path, state, spec(보고서 문구), registration 등."""
    srcp = Path(src)
    if not srcp.exists():
        raise IngestError(f"영상 파일이 없음: {src}")
    aoi = gpd.read_file(aoi_path).to_crs(crs)
    al, ab, ar, at = aoi.total_bounds
    bounds = (float(np.floor((al - margin_m) / res) * res), float(np.floor((ab - margin_m) / res) * res),
              float(np.ceil((ar + margin_m) / res) * res), float(np.ceil((at + margin_m) / res) * res))
    readable, conv = normalize_format(src, out.with_name(out.stem + "_src.tif"), res, pro, log)
    with rasterio.open(readable) as ds:
        state = georef_state(ds, (al, ab, ar, at))
        if state == "crs":
            sb = transform_bounds(ds.crs, crs, *ds.bounds)
            if not (sb[0] < ar and sb[2] > al and sb[1] < at and sb[3] > ab):
                raise IngestError(f"영상 범위가 광명시흥 지구와 겹치지 않음(영상 좌표계 {ds.crs.to_string()})")
        info = conv or {"driver": ds.driver, "size": [ds.width, ds.height], "bands": ds.count, "dtype": ds.dtypes[0],
                        "src_res": pixel_m(ds)}
        info["src_crs"] = ds.crs.to_string() if ds.crs else None
    log(f"   {srcp.name}: {info['driver']} {info['size'][0]:,}×{info['size'][1]:,} {info['bands']}밴드, 좌표 상태 {state}")
    coarse = transform = None
    if state == "none":
        transform, coarse = coarse_register(readable, ref, bounds, log=log)
    elif state == "coords":
        log(f"   좌표계 정보 없음({info['src_crs']}) - 좌표가 지구 범위와 겹쳐 {crs}로 간주")
    warp(readable, out, bounds, res, crs, state, transform, log=log)
    if conv:  # 형식 변환 중간 파일(원본 범위 전체)은 지운다
        Path(readable).unlink(missing_ok=True)

    log("   기준 영상 대비 잔여 이동량 측정")
    sh = tile_shifts(str(out), ref, aoi, res)
    reg = fine_shift(sh) if len(sh) >= 8 else None
    # 타일 반폭(64m)보다 크게 어긋나면 위상상관 값이 제각각이다 - 일관되지 않으면 보정하지 않는다
    consistent = bool(reg) and reg["n"] >= 8 and reg["outlier_share"] < 0.5 and reg["rmse_m"] <= 2.0
    detail = f"타일 {reg['n']}/{reg['tiles']}개, RMSE {reg['rmse_m']:.2f}m" if reg else f"측정 가능 타일 {len(sh)}개"
    if consistent:
        reg["applied_m"] = float(np.hypot(reg["dx"], reg["dy"]))
        if reg["applied_m"] > 0.05:
            with rasterio.open(out, "r+") as d:
                d.transform = Affine.translation(reg["dx"], reg["dy"]) * d.transform
        log(f"   잔여 이동 {reg['applied_m']:.2f}m 보정, {detail}")
    elif state == "none":
        raise IngestError(f"자동 정합 실패: 기준 영상과 일관된 정합을 얻지 못함({detail})")
    elif reg:
        log(f"   [경고] 기준 영상과의 이동량이 일관되지 않음({detail}) - 좌표계가 맞는지 확인 필요, 보정하지 않음")
        reg.update(applied_m=0.0, warning="inconsistent")
    else:
        log(f"   기준 영상과 겹치는 타일이 부족해 이동량 보정을 건너뜀({detail})")

    src_res = coarse["pixel_m"] if coarse else info.get("src_res")
    how = {"crs": f"원좌표 {info['src_crs']}", "coords": f"좌표계 없음 → {crs} 간주",
           "none": "좌표 없음 → 기준 영상 자동 정합"}[state]
    spec = f"{info['driver']} {'' if src_res is None else f'{src_res:.3g}m '}{'RGB' if info['bands'] >= 3 else '흑백'}, {how} → {res}m 변환"
    if reg and reg.get("warning"):
        spec += "(기준 영상과 위치가 일관되게 맞지 않음 - 좌표계 확인 필요)"
    elif reg:
        spec += f"(기준 영상 대비 RMSE {reg['rmse_m']:.2f}m)"
    return {"path": str(out), "source": str(srcp), "state": state, "spec": spec, "info": info,
            "coarse": coarse, "registration": reg}
