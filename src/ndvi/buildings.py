"""현재 시점 건축물 윤곽(VWorld lt_c_spbd 도로명주소건물) 수집과 필지 연결.

주의(지장물 조사 관점):
- 도로명주소가 부여된 건축물만 포함된다. 가설건축물, 비닐하우스, 축사, 창고, 담장·옹벽, 수목 등은 빠질 수 있다.
- 현재 시점 1벌이라 연도별 생성·변경·소멸 이력은 알 수 없다.
- 이 서비스는 요청당 1,000건에서 잘려 반환되므로(totalFeatures가 1,000으로 고정) 격자로 분할해 누락을 막는다.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely

logger = logging.getLogger(__name__)
LAYER = "lt_c_spbd"
CAP = 1000


def _get(bbox, start: int, key: str) -> dict:
    r = requests.get("https://api.vworld.kr/req/wfs", params={
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAME": LAYER,
        "BBOX": ",".join(f"{v:.7f}" for v in bbox), "SRSNAME": "EPSG:4326", "OUTPUT": "application/json",
        "MAXFEATURES": CAP, "STARTINDEX": start, "KEY": key}, timeout=90)
    r.raise_for_status()
    return r.json()


def _fetch(bbox, key: str, depth: int = 0) -> list[dict]:
    d = _get(bbox, 0, key)
    feats = d.get("features", [])
    if len(feats) >= CAP and depth < 6:      # 상한에 걸림 -> 4분할
        x0, y0, x1, y1 = bbox
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        out = []
        for q in ((x0, y0, xm, ym), (xm, y0, x1, ym), (x0, ym, xm, y1), (xm, ym, x1, y1)):
            out += _fetch(q, key, depth + 1)
        return out
    if len(feats) >= CAP:
        logger.warning("[BLD] 최대 분할 깊이에서도 %d건 - 일부 누락 가능", len(feats))
    return feats


def load_buildings(aoi: gpd.GeoDataFrame, cache_dir: str | Path, offline: bool = False) -> tuple[gpd.GeoDataFrame, dict]:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / "buildings_spbd_full.geojson"
    meta = {"layer": LAYER, "source": "VWorld WFS 도로명주소건물(lt_c_spbd)", "status": "OK",
            "caveat": "도로명주소 부여 건축물만 포함(가설건축물·비닐하우스·축사·창고·공작물 누락 가능), 현재 시점 1벌, 연도별 이력 없음"}
    try:
        if not p.exists():
            if offline:
                raise RuntimeError("로컬 캐시 없음 + --offline")
            from dotenv import load_dotenv

            load_dotenv(".env")
            key = os.getenv("VWORLD_API_KEY")
            if not key:
                raise RuntimeError("VWORLD_API_KEY 없음")
            b = aoi.to_crs(4326).total_bounds
            pad = 0.001
            feats = _fetch((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad), key)
            uniq = {f["id"]: f for f in feats if "id" in f}
            p.write_text(json.dumps({"type": "FeatureCollection", "features": list(uniq.values())}, ensure_ascii=False),
                         encoding="utf-8")
            meta["fetched_features"] = len(uniq)
        g = gpd.read_file(p)
        g = g.to_crs(5186) if len(g) else gpd.GeoDataFrame(geometry=[], crs=5186)
        g = g[g.geometry.notna() & ~g.geometry.is_empty].copy()
        g["geometry"] = [shapely.make_valid(x) if not x.is_valid else x for x in g.geometry]
        aoi_u = aoi.to_crs(5186).geometry.union_all()
        g = g[g.intersects(aoi_u)].reset_index(drop=True)
        g["footprint_m2"] = g.geometry.area
        meta["n_in_aoi"] = int(len(g))
        meta["file_mtime"] = pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC").tz_convert("Asia/Seoul").isoformat(timespec="seconds")
        return g, meta
    except Exception as e:  # noqa: BLE001
        meta.update(status="UNAVAILABLE", reason=f"{type(e).__name__}: {e}")
        return gpd.GeoDataFrame(geometry=[], crs=5186), meta


def link_to_parcels(buildings: gpd.GeoDataFrame, parcels: gpd.GeoDataFrame) -> pd.DataFrame:
    """건축물 -> 필지: 건축물 면적의 50% 이상이 겹치는 필지(없으면 PNU 속성). 필지별 개수/면적/목록."""
    n = len(parcels)
    out = pd.DataFrame({"bld_count": np.zeros(n, dtype=int), "bld_footprint_m2": np.zeros(n), "bld_names": [""] * n,
                        "bld_idx": [[] for _ in range(n)]})
    if buildings.empty or n == 0:
        out["bld_coverage_ratio"] = 0.0
        return out
    tree = shapely.STRtree(parcels.geometry.values)
    assigned: dict[int, int] = {}
    for bi, geom in enumerate(buildings.geometry.values):
        cand = tree.query(geom, predicate="intersects")
        best, best_a = None, 0.0
        for pi in cand:
            a = geom.intersection(parcels.geometry.values[pi]).area
            if a > best_a:
                best, best_a = int(pi), a
        if best is not None and best_a >= 0.5 * geom.area:
            assigned[bi] = best
    for bi, pi in assigned.items():
        out.at[pi, "bld_count"] += 1
        out.at[pi, "bld_footprint_m2"] += float(buildings["footprint_m2"].iat[bi])
        out.at[pi, "bld_idx"].append(int(bi))
    for pi in range(n):
        names = [str(buildings["buld_nm"].iat[b]) for b in out.at[pi, "bld_idx"] if pd.notna(buildings["buld_nm"].iat[b])]
        out.at[pi, "bld_names"] = ", ".join(sorted(set(names)))[:80]
    area = parcels["area_in_aoi_m2"].to_numpy() if "area_in_aoi_m2" in parcels else parcels.geometry.area.to_numpy()
    out["bld_coverage_ratio"] = np.where(area > 0, out["bld_footprint_m2"] / np.where(area > 0, area, 1), 0.0)
    return out
