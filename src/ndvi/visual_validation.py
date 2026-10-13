"""고해상도 현재 영상(VWorld Satellite 타일) 육안 판독 기반 상태 검증 도구.

주의: 판독 기준 영상은 촬영 시점을 알 수 없는 '현재' 영상(약 0.3~0.6m)이며, 판독은 현장 조사가 아니다. 변화 '발생'이 아니라
분석 최종 시점(2026)의 노출/식생 '상태'가 일치하는지만 검증할 수 있다. 표본은 층화추출이므로 층별 비율은 모집단 정확도가 아니다.
"""

from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path

import numpy as np
import rasterio
import requests
from PIL import Image, ImageDraw, ImageFont
from pyproj import Transformer

LABELS = ["BARE", "BUILT", "VEG_TREE", "VEG_LOW", "WATER", "UNCLEAR"]
EXPOSED_LABELS = {"BARE", "BUILT"}      # 비식생(노출/불투수) 상태로 간주


def _key() -> str:
    from dotenv import load_dotenv

    load_dotenv(".env")
    k = os.getenv("VWORLD_API_KEY")
    if not k:
        raise RuntimeError("VWORLD_API_KEY 없음")
    return k


def _tile_xy(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def fetch_chip(lon: float, lat: float, half_m: float = 100.0, z: int = 19, cache: Path | None = None, size: int = 360) -> Image.Image:
    """(lon,lat) 중심 ±half_m 정사각 영상 조각을 타일 스티칭으로 만든다."""
    key = _key()
    res = 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)       # m/px
    half_px = half_m / res
    fx, fy = _tile_xy(lon, lat, z)
    x0, x1 = int(math.floor(fx - half_px / 256)), int(math.floor(fx + half_px / 256))
    y0, y1 = int(math.floor(fy - half_px / 256)), int(math.floor(fy + half_px / 256))
    W, H = (x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256
    canvas = Image.new("RGB", (W, H))
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            p = cache / f"{z}_{tx}_{ty}.jpg" if cache else None
            if p is not None and p.exists():
                im = Image.open(p)
            else:
                u = f"https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{ty}/{tx}.jpeg"
                r = requests.get(u, timeout=40)
                r.raise_for_status()
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                if p is not None:
                    cache.mkdir(parents=True, exist_ok=True)
                    im.save(p, quality=92)
            canvas.paste(im, ((tx - x0) * 256, (ty - y0) * 256))
    cx, cy = (fx - x0) * 256, (fy - y0) * 256
    box = (cx - half_px, cy - half_px, cx + half_px, cy + half_px)
    return canvas.crop(tuple(int(round(v)) for v in box)).resize((size, size), Image.LANCZOS)


def build_samples(bsi_dir: Path, aoi_geom, n_per_stratum: int = 12, seed: int = 20260513) -> list[dict]:
    """복합 분류 결과에서 층별 20m 픽셀 표본(AOI 내부)을 뽑는다."""
    import shapely
    from rasterio.features import geometry_mask

    r = bsi_dir / "rasters"
    with rasterio.open(r / "composite_change_type.tif") as s:
        ctype = s.read(1)
        tr = s.transform
        shape = ctype.shape
    with rasterio.open(r / "quarry_class.tif") as s:
        qc = s.read(1)
    with rasterio.open(r / "exposed_persistence.tif") as s:
        persist = s.read(1)
    with rasterio.open(r / "bsi_2026.tif") as s:
        b26 = s.read(1).astype("float32")
        nodata = s.nodata
    b26 = np.where(b26 == nodata, np.nan, b26)
    with rasterio.open(r / "ndvi20_2026.tif") as s:
        n26 = s.read(1).astype("float32")
    n26 = np.where(n26 == nodata, np.nan, n26)
    inside = ~geometry_mask([aoi_geom], out_shape=shape, transform=tr)
    strata = {
        "POTENTIAL_QUARRY_EXPANSION": qc == 2, "EXPOSED_SURFACE_CHANGE": qc == 3, "QUARRY_INTERNAL_CHANGE": qc == 6,
        "VEGETATION_LOSS": ctype == 1, "PERSISTENT_EXPOSED_SURFACE": ctype == 3, "VEGETATION_RECOVERY": ctype == 4,
        "UNCERTAIN_CHANGE": ctype == 5 + 1, "STABLE_VEGETATION": ctype == 5, "NO_SIGNIFICANT_CHANGE": ctype == 0,
    }
    # 마지막 두 층은 코드 충돌 방지: composite 코드 6=UNCERTAIN, 5=STABLE
    rng = np.random.default_rng(seed)
    t = Transformer.from_crs(5186, 4326, always_xy=True)
    out = []
    for name, m in strata.items():
        m = m & inside & np.isfinite(b26) & np.isfinite(n26)
        idx = np.argwhere(m)
        if len(idx) == 0:
            continue
        pick = idx[rng.choice(len(idx), size=min(n_per_stratum, len(idx)), replace=False)]
        for rr, cc in pick:
            x, y = tr * (cc + 0.5, rr + 0.5)
            lon, lat = t.transform(x, y)
            out.append({"id": f"S{len(out):03d}", "stratum": name, "row": int(rr), "col": int(cc), "x": float(x), "y": float(y),
                        "lon": float(lon), "lat": float(lat), "bsi_2026": float(b26[rr, cc]), "ndvi20_2026": float(n26[rr, cc]),
                        "persist": int(persist[rr, cc]), "quarry_class": int(qc[rr, cc]), "ctype": int(ctype[rr, cc]),
                        "n_stratum_pixels": int(m.sum())})
    return out


def make_montages(samples: list[dict], out_dir: Path, per_sheet: int = 12, cols: int = 4, cache: Path | None = None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("malgun.ttf", 16)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    paths = []
    size = 330
    for s0 in range(0, len(samples), per_sheet):
        grp = samples[s0: s0 + per_sheet]
        rows = math.ceil(len(grp) / cols)
        sheet = Image.new("RGB", (cols * size, rows * (size + 22)), "white")
        d = ImageDraw.Draw(sheet)
        for k, s in enumerate(grp):
            chip = fetch_chip(s["lon"], s["lat"], 100.0, 19, cache, size)
            dd = ImageDraw.Draw(chip)
            half = size / 2
            bw = size * (20.0 / 200.0) / 2        # 20m 픽셀 상자(±10m / 200m 창)
            dd.rectangle([half - bw, half - bw, half + bw, half + bw], outline=(255, 0, 0), width=2)
            x, y = (k % cols) * size, (k // cols) * (size + 22)
            sheet.paste(chip, (x, y + 22))
            d.text((x + 4, y + 2), s["id"], fill=(0, 0, 0), font=font)
        p = out_dir / f"montage_{s0 // per_sheet + 1:02d}.jpg"
        sheet.save(p, quality=88)
        paths.append(p)
    return paths


def confusion(samples: list[dict], labels: dict[str, str], pred_fn) -> dict:
    """pred_fn(sample)->bool(예측: 비식생/노출 상태). 라벨은 EXPOSED_LABELS 여부로 이진화. UNCLEAR는 제외."""
    tp = fp = fn = tn = 0
    per: dict = {}
    for s in samples:
        lab = labels.get(s["id"])
        if lab is None or lab == "UNCLEAR":
            continue
        truth = lab in EXPOSED_LABELS
        pred = bool(pred_fn(s))
        tp += truth and pred
        fp += (not truth) and pred
        fn += truth and (not pred)
        tn += (not truth) and (not pred)
        d = per.setdefault(s["stratum"], {"n": 0, "visually_exposed": 0, "pred_exposed": 0, "labels": {}})
        d["n"] += 1
        d["visually_exposed"] += int(truth)
        d["pred_exposed"] += int(pred)
        d["labels"][lab] = d["labels"].get(lab, 0) + 1
    n = tp + fp + fn + tn
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
    return {"n": n, "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn), "precision": prec, "recall": rec, "f1": f1,
            "overall_accuracy": (tp + tn) / n if n else None, "per_stratum": per}
