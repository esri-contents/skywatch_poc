"""농경지/토지이용 맥락 자료: 지목, VWorld 공식 용도·농업·산림 레이어, 연도별 토지피복(IO LULC 10m).

목적은 '농경지 변동과 개발·노출지 변화의 혼동'을 줄이는 보조 근거를 만드는 것이다. 이 자료들은 변화를
확정하지 않으며, 필지에 맥락 플래그/경고를 붙이는 데만 쓴다.

- 지목: 연속지적도 지번 접미 약칭(전/답/과/임/잡 등) - 공부상 지목(현황과 다를 수 있음)
- VWorld 용도지역(lt_c_uq111/112/113), 영농여건불리농지(lt_c_agrixue102), 산림입지도(lt_c_fsdifrsts, '경작지'/'제지'), 산림보호구역
- IO LULC annual v02(Impact Observatory/Esri, 10m, 2017~2023, CC BY 4.0, 모델 산출물) - 참고용 약한 참조
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
import shapely
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds

from . import parcel_statistics as ps

logger = logging.getLogger(__name__)

VWORLD_CONTEXT_LAYERS = {
    "zoning_urban": "lt_c_uq111", "zoning_managed": "lt_c_uq112", "zoning_agroforest": "lt_c_uq113",
    "agri_disadvantaged": "lt_c_agrixue102", "forest_site": "lt_c_fsdifrsts", "forest_protection": "lt_c_uf151",
}
JIMOK_AGRI = {"전", "답", "과"}
JIMOK_NAMES = {"전": "전", "답": "답", "과": "과수원", "목": "목장용지", "임": "임야", "광": "광천지", "염": "염전", "대": "대",
               "장": "공장용지", "학": "학교용지", "차": "주차장", "주": "주유소", "창": "창고용지", "도": "도로", "철": "철도용지",
               "제": "제방", "천": "하천", "구": "구거", "유": "유지", "양": "양어장", "수": "수도용지", "공": "공원",
               "체": "체육용지", "원": "유원지", "종": "종교용지", "사": "사적지", "묘": "묘지", "잡": "잡종지"}
IO_CLASSES = {1: "water", 2: "trees", 4: "flooded_veg", 5: "crops", 7: "built", 8: "bare", 9: "snow", 10: "clouds", 11: "rangeland"}
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"


# ----------------------------------------------------------------------------- VWorld layers
def load_vworld_context(aoi: gpd.GeoDataFrame, cache_dir: str | Path, offline: bool = False) -> tuple[dict, dict]:
    from src.data.download import download_vworld_wfs_layer

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    b = aoi.to_crs(4326).total_bounds
    bbox = (b[0] - 0.002, b[1] - 0.002, b[2] + 0.002, b[3] + 0.002)
    out, meta = {}, {}
    for key, layer in VWORLD_CONTEXT_LAYERS.items():
        p = cache_dir / f"{layer}.geojson"
        try:
            if not p.exists():
                if offline:
                    raise RuntimeError("로컬 캐시 없음 + --offline")
                download_vworld_wfs_layer(layer, bbox, p)
            g = gpd.read_file(p)
            g = g.to_crs(5186) if len(g) else gpd.GeoDataFrame(geometry=[], crs=5186)
            out[key] = g
            meta[key] = {"layer": layer, "n_features": int(len(g)), "status": "OK",
                         "dyear_values": sorted(g["dyear"].dropna().astype(str).unique().tolist()) if "dyear" in g.columns and len(g) else []}
        except Exception as e:  # noqa: BLE001
            meta[key] = {"layer": layer, "status": "UNAVAILABLE", "reason": f"{type(e).__name__}: {e}"}
    return out, meta


# ----------------------------------------------------------------------------- LULC
def load_lulc(aoi: gpd.GeoDataFrame, years: list[int], cache_dir: str | Path, transform, shape,
              offline: bool = False) -> tuple[dict[int, np.ndarray], dict]:
    """IO LULC annual v02를 분석 10m 격자(EPSG:5186)로 정렬. 연도별 캐시(uint8, 0=없음)."""
    from src.imagery.providers import sign_href

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    arrs: dict[int, np.ndarray] = {}
    meta: dict = {"source": "Impact Observatory/Esri 10m Annual Land Use Land Cover v02 (Planetary Computer io-lulc-annual-v02)",
                  "license": "CC BY 4.0", "nature": "모델 산출물(참조 정답 아님)", "years": {}}
    b = aoi.to_crs(4326).total_bounds
    for y in years:
        p = cache_dir / f"io_lulc_{y}.tif"
        try:
            if not p.exists():
                if offline:
                    raise RuntimeError("로컬 캐시 없음 + --offline")
                r = requests.post(STAC, json={"collections": ["io-lulc-annual-v02"], "bbox": list(b),
                                               "datetime": f"{y}-01-01T00:00:00Z/{y}-12-31T23:59:59Z"}, timeout=60)
                r.raise_for_status()
                feats = r.json().get("features", [])
                if not feats:
                    raise RuntimeError(f"{y}년 IO LULC item 없음(제공 연도 2017~2023)")
                dst = np.zeros(shape, dtype="uint8")
                for f in feats:
                    with rasterio.open(sign_href(f["assets"]["data"]["href"])) as src:
                        reproject(rasterio.band(src, 1), dst, src_transform=src.transform, src_crs=src.crs,
                                  dst_transform=transform, dst_crs="EPSG:5186", resampling=Resampling.nearest,
                                  dst_nodata=0, init_dest_nodata=False)
                with rasterio.open(p, "w", driver="GTiff", height=shape[0], width=shape[1], count=1, dtype="uint8",
                                   crs="EPSG:5186", transform=transform, nodata=0, compress="deflate") as w:
                    w.write(dst, 1)
                meta["years"][str(y)] = {"status": "OK", "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            with rasterio.open(p) as s:
                arrs[y] = s.read(1)
            meta["years"].setdefault(str(y), {"status": "OK", "source": "local_cache"})
        except Exception as e:  # noqa: BLE001
            meta["years"][str(y)] = {"status": "UNAVAILABLE", "reason": f"{type(e).__name__}: {e}"}
    return arrs, meta


# ----------------------------------------------------------------------------- parcel context
def _share(clipped: gpd.GeoSeries, layer: gpd.GeoDataFrame, mask=None) -> np.ndarray:
    """필지(AOI 내 부분) 중 layer(필터 후)와 겹치는 면적 비율."""
    if layer is None or len(layer) == 0:
        return np.zeros(len(clipped))
    g = layer if mask is None else layer[mask]
    if len(g) == 0:
        return np.zeros(len(clipped))
    u = shapely.union_all(shapely.make_valid(g.geometry.values))
    a = clipped.area.to_numpy()
    inter = np.array([x.intersection(u).area if x is not None and not x.is_empty else 0.0 for x in clipped.values])
    return np.where(a > 0, inter / np.where(a > 0, a, 1), 0.0)


def parcel_context(parcels: gpd.GeoDataFrame, clipped: gpd.GeoSeries, vctx: dict, lulc: dict[int, np.ndarray],
                   transform, shape, thr: dict) -> pd.DataFrame:
    n = len(parcels)
    out = pd.DataFrame(index=range(n))
    jim = parcels["jimok_abbr"].fillna("").astype(str).str.strip().str[:1] if "jimok_abbr" in parcels else pd.Series([""] * n)
    out["jimok"] = jim.map(lambda c: JIMOK_NAMES.get(c, None) if c else None).values
    out["jimok_agri"] = jim.isin(JIMOK_AGRI).values
    # 용도지역: 면적이 가장 큰 종류
    zone_cols = []
    for key in ("zoning_urban", "zoning_managed", "zoning_agroforest"):
        g = vctx.get(key)
        if g is None or not len(g):
            continue
        names = g["uname"].dropna().unique()
        for nm in names:
            s = _share(clipped, g, g["uname"] == nm)
            zone_cols.append((nm, s))
    if zone_cols:
        M = np.stack([s for _, s in zone_cols], 1)
        best = M.argmax(1)
        out["zoning"] = [zone_cols[i][0] if M[r, i] >= 0.5 else None for r, i in enumerate(best)]
    else:
        out["zoning"] = None
    out["agri_disadvantaged_share"] = _share(clipped, vctx.get("agri_disadvantaged"))
    fs = vctx.get("forest_site")
    out["forest_site_cropland_share"] = _share(clipped, fs, fs["name"] == "경작지") if fs is not None and len(fs) else 0.0
    out["forest_site_nonforest_share"] = _share(clipped, fs, fs["name"] == "제지") if fs is not None and len(fs) else 0.0
    out["forest_protection_share"] = _share(clipped, vctx.get("forest_protection"))
    # LULC 연도별 비율
    wt = ps.pixel_weights(list(clipped.values), transform, shape)
    area = np.bincount(wt["p"], weights=wt["a"], minlength=n)
    crops, built, bare = [], [], []
    for y, a in lulc.items():
        f = a.ravel()[wt["f"]]
        valid = f > 0
        for lst, code in ((crops, 5), (built, 7), (bare, 8)):
            num = np.bincount(wt["p"], weights=wt["a"] * (f == code), minlength=n)
            den = np.bincount(wt["p"], weights=wt["a"] * valid, minlength=n)
            col = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
            out[f"lulc_{IO_CLASSES[code]}_{y}"] = col
            lst.append(col)
    out["lulc_crops_mean"] = np.nanmean(np.stack(crops, 1), axis=1) if crops else np.nan
    out["lulc_built_mean"] = np.nanmean(np.stack(built, 1), axis=1) if built else np.nan
    out["lulc_bare_mean"] = np.nanmean(np.stack(bare, 1), axis=1) if bare else np.nan
    # 농경지 맥락 판단(보조): 공식 지목·산림입지도·영농여건불리농지·LULC 중 하나라도 해당
    out["agri_evidence"] = [
        ",".join(k for k, v in (
            ("지목(전·답·과)", bool(out["jimok_agri"].iat[i])),
            ("산림입지도 경작지", out["forest_site_cropland_share"].iat[i] >= thr["agri_share_min"]),
            ("영농여건불리농지", out["agri_disadvantaged_share"].iat[i] >= thr["agri_share_min"]),
            ("LULC crops", (out["lulc_crops_mean"].iat[i] or 0) >= thr["agri_share_min"])) if v) for i in range(n)]
    out["agri_context"] = out["agri_evidence"].str.len() > 0
    return out
