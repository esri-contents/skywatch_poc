"""동일 계절 다중 장면 중앙값 합성(연도별) + 단일 장면 결과와의 비교.

단일 시점 장면의 구름 잔여물/그림자/촬영일 편차 영향을 줄이기 위해, 계절 창(기본 04-20~05-20) 안의 모든 L2A 장면
(타일 T52SDF/T52SDG, 장면 구름 < 70%)을 SCL clear 픽셀만 모아 픽셀별 중앙값을 만든다. 장면별 BOA offset은 장면마다
기록된 processing baseline에 따라 한 번만 제거한다. 결과는 기존 단일 장면 입력과 같은 형식(10m 4밴드 + 20m B11)이라
기존 파이프라인을 그대로 적용할 수 있다.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.windows import Window, from_bounds

from src.imagery.providers import SCL_CLEAR, _boa_offset

logger = logging.getLogger(__name__)
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
BANDS10 = ["B02", "B03", "B04", "B08"]


def _sign(href: str) -> str:
    try:
        import planetary_computer as pc

        return pc.sign(href)
    except Exception:  # noqa: BLE001
        from src.imagery.providers import sign_href

        return sign_href(href)


def _search(bbox, year: int, start_mmdd: str, end_mmdd: str, max_cloud: float) -> list[dict]:
    r = requests.post(STAC, json={"collections": ["sentinel-2-l2a"], "bbox": list(bbox),
                                   "datetime": f"{year}-{start_mmdd}/{year}-{end_mmdd}",
                                   "query": {"eo:cloud_cover": {"lt": max_cloud}}, "limit": 200}, timeout=90)
    r.raise_for_status()
    items = r.json()["features"]
    # 같은 타일·같은 촬영시각의 중복 처리본은 가장 최신 처리본만
    best: dict = {}
    for it in items:
        parts = it["id"].split("_")
        key = (parts[-2], it["properties"]["datetime"][:19])
        if key not in best or it["id"] > best[key]["id"]:
            best[key] = it
    return sorted(best.values(), key=lambda f: f["properties"]["datetime"])


def _win(src, bounds, dec: int = 0) -> Window:
    w = from_bounds(*bounds, transform=src.transform)
    return Window(int(np.floor(w.col_off + 1e-6)), int(np.floor(w.row_off + 1e-6)),
                  int(np.ceil(w.col_off + w.width - 1e-6)) - int(np.floor(w.col_off + 1e-6)),
                  int(np.ceil(w.row_off + w.height - 1e-6)) - int(np.floor(w.row_off + 1e-6)))


def _read_item(it: dict, bounds10: tuple, shape10: tuple, bounds20: tuple, shape20: tuple) -> dict | None:
    off = _boa_offset(it["properties"])
    arr10 = np.zeros((4,) + shape10, dtype="float32")
    for k, b in enumerate(BANDS10):
        with rasterio.open(_sign(it["assets"][b]["href"])) as s:
            w = _win(s, bounds10)
            d = s.read(1, window=w, boundless=True, fill_value=0, out_shape=shape10).astype("float32")
        arr10[k] = d
    with rasterio.open(_sign(it["assets"]["B11"]["href"])) as s:
        w = _win(s, bounds20)
        b11 = s.read(1, window=w, boundless=True, fill_value=0).astype("float32")
        tr20 = s.window_transform(w)
    if b11.shape != shape20:
        return None
    with rasterio.open(_sign(it["assets"]["SCL"]["href"])) as s:
        w = _win(s, bounds20)
        scl20 = s.read(1, window=w, boundless=True, fill_value=0, out_shape=shape20)
    clear20 = np.isin(scl20, SCL_CLEAR)
    clear10 = np.repeat(np.repeat(clear20, 2, 0), 2, 1)[: shape10[0], : shape10[1]]
    # 10m 격자가 20m 격자 범위 안에서 (offset_row, offset_col)만큼 이동되어 있을 수 있어 크기만 맞춘다(아래 참조)
    return {"arr10": arr10, "b11": b11, "clear20": clear20, "off": off, "tr20": tr20}


def build_year_composite(year: int, ref_tif: Path, aoi_bounds_wgs84, out_dir10: Path, out_dir20: Path,
                         season=("04-20", "05-20"), max_cloud: float = 70.0, min_obs: int = 2) -> dict:
    """ref_tif(기존 단일 장면 10m GeoTIFF)의 범위/격자에 맞춰 연도 합성을 만든다."""
    with rasterio.open(ref_tif) as ref:
        b10 = tuple(ref.bounds)
        crs = ref.crs
        shape10 = (ref.height, ref.width)
        tr10 = ref.transform
    # 20m 격자: 10m 범위를 덮도록 20m 격자에 맞춤(기존 swir 규칙과 동일하게 B11 격자 기준)
    items = _search(aoi_bounds_wgs84, year, season[0], season[1], max_cloud)
    if not items:
        return {"year": year, "status": "UNAVAILABLE", "reason": "계절 창에 장면 없음"}
    with rasterio.open(_sign(items[0]["assets"]["B11"]["href"])) as s:
        w = _win(s, b10)
        tr20 = s.window_transform(w)
        shape20 = (w.height, w.width)
        crs20 = s.crs
    if crs20 != crs:
        return {"year": year, "status": "UNAVAILABLE", "reason": f"CRS 불일치 {crs20} vs {crs}"}
    x0_20, y0_20 = tr20.c, tr20.f
    off_c = int(round((tr10.c - x0_20) / 10.0))
    off_r = int(round((y0_20 - tr10.f) / 10.0))
    bounds20 = (x0_20, y0_20 - shape20[0] * 20.0, x0_20 + shape20[1] * 20.0, y0_20)
    used, skipped = [], []
    stack10, stack11 = [], []
    for it in items:
        try:
            r = _read_item(it, b10, shape10, bounds20, shape20)
            if r is None:
                skipped.append({"id": it["id"], "reason": "격자 크기 불일치"})
                continue
            v10 = (r["arr10"] > 0).all(0)
            # 10m 격자에 20m SCL 마스크를 적용(20m 창에서 10m 창의 위치만큼 잘라냄)
            full_clear = np.repeat(np.repeat(r["clear20"], 2, 0), 2, 1)
            clr = full_clear[off_r: off_r + shape10[0], off_c: off_c + shape10[1]]
            m = v10 & clr
            a = np.where(m[None], np.clip(r["arr10"] - r["off"], 1, 65535), np.nan)
            b = np.where((r["b11"] > 0) & r["clear20"], np.clip(r["b11"] - r["off"], 1, 65535), np.nan)
            if m.mean() < 0.02:
                skipped.append({"id": it["id"], "reason": "유효 픽셀 2% 미만(구름/타일 밖)"})
                continue
            stack10.append(a.astype("float32"))
            stack11.append(b.astype("float32"))
            used.append({"id": it["id"], "datetime": it["properties"]["datetime"], "platform": it["properties"].get("platform"),
                         "scene_cloud_pct": it["properties"].get("eo:cloud_cover"),
                         "processing_baseline": str(it["properties"].get("s2:processing_baseline")),
                         "boa_offset_removed_dn": r["off"], "valid_fraction_10m": float(m.mean())})
        except Exception as e:  # noqa: BLE001
            skipped.append({"id": it["id"], "reason": f"{type(e).__name__}: {str(e)[:120]}"})
    if len(used) < min_obs:
        return {"year": year, "status": "UNAVAILABLE", "reason": f"사용 가능한 장면 {len(used)}개 < {min_obs}", "skipped": skipped}
    s10 = np.stack(stack10)
    s11 = np.stack(stack11)
    n10 = np.isfinite(s10[:, 0]).sum(0)
    n20 = np.isfinite(s11).sum(0)
    with np.errstate(all="ignore"):
        med10 = np.nanmedian(s10, axis=0)
        med11 = np.nanmedian(s11, axis=0)
    med10 = np.where((n10 >= min_obs)[None] & np.isfinite(med10), np.round(med10), 0).astype("uint16")
    med11 = np.where((n20 >= min_obs) & np.isfinite(med11), np.round(med11), 0).astype("uint16")
    dates = sorted(date.fromisoformat(u["datetime"][:10]) for u in used)
    mid = dates[len(dates) // 2].isoformat()
    out_dir10.mkdir(parents=True, exist_ok=True)
    out_dir20.mkdir(parents=True, exist_ok=True)
    p10 = out_dir10 / f"s2_{year}_{mid}.tif"
    with rasterio.open(p10, "w", driver="GTiff", height=shape10[0], width=shape10[1], count=4, dtype="uint16", crs=crs,
                       transform=tr10, nodata=0, compress="deflate") as dst:
        dst.write(med10)
        dst.descriptions = tuple(BANDS10)
        dst.update_tags(composite="median", n_scenes=str(len(used)), min_obs=str(min_obs))
    sid = f"COMPOSITE_{year}"
    p20 = out_dir20 / f"s2_{year}_B11_{sid}.tif"
    with rasterio.open(p20, "w", driver="GTiff", height=shape20[0], width=shape20[1], count=1, dtype="uint16", crs=crs,
                       transform=tr20, nodata=0, compress="deflate") as dst:
        dst.write(med11, 1)
        dst.descriptions = ("B11",)
        dst.update_tags(scene_id=sid, composite="median", n_scenes=str(len(used)))
    return {"year": year, "status": "OK", "date": mid, "scene_id": sid, "path10": str(p10), "path20": str(p20),
            "n_scenes_used": len(used), "scenes": used, "skipped": skipped, "season": list(season),
            "n_obs_10m_median": float(np.median(n10[n10 > 0])) if (n10 > 0).any() else 0.0,
            "valid_fraction_10m": float((n10 >= min_obs).mean())}


def build_all(years: list[int], ref_dir: Path, aoi_gdf, out_root: Path, season=("04-20", "05-20"), max_cloud=70.0,
              min_obs=2) -> dict:
    d10, d20 = out_root / "sentinel2_composite", out_root / "sentinel2_composite_swir"
    b = list(aoi_gdf.to_crs(4326).total_bounds)
    res = {}
    for y in years:
        refs = sorted(Path(ref_dir).glob(f"s2_{y}_*.tif"))
        if not refs:
            res[str(y)] = {"year": y, "status": "UNAVAILABLE", "reason": "기준 단일 장면 GeoTIFF 없음"}
            continue
        existing = sorted(d10.glob(f"s2_{y}_*.tif"))
        prov = out_root / f"composite_provenance_{y}.json"
        if existing and prov.exists():
            res[str(y)] = json.loads(prov.read_text(encoding="utf-8"))
            continue
        try:
            r = build_year_composite(y, refs[0], b, d10, d20, season, max_cloud, min_obs)
        except Exception as e:  # noqa: BLE001
            r = {"year": y, "status": "UNAVAILABLE", "reason": f"{type(e).__name__}: {e}"}
        r["built_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if r["status"] == "OK":
            prov.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        res[str(y)] = r
        logger.info("[COMPOSITE] %s %s n=%s", y, r["status"], r.get("n_scenes_used"))
    return res
