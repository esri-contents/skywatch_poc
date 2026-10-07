"""다중시점 변화탐지용 Imagery Provider 추상화.

흐름:

    ImageryProvider (Sentinel2Provider | LocalGeoTiffProvider[SkyWatch 50cm 등])
        -> year별 multiband GeoTIFF + 메타데이터 record
        -> (공통) align_imagery_pair -> run_baseline_change_detection -> polygonize
           (src/imagery_change_pipeline.py)

Provider는 "해당 연도의 AOI 영상 1장을 band description이 채워진 단일 GeoTIFF로"
돌려주는 것까지만 책임진다. 정합/변화탐지/후처리는 provider와 무관한 공통 코드다.
SkyWatch 50cm 영상으로 교체할 때는 LocalGeoTiffProvider(구매한 GeoTIFF 경로를
config로 지정)를 쓰면 같은 파이프라인이 그대로 돈다.

TODO(SkyWatch): 50cm에서는 (1) imagery.resolution_m, (2) analysis.min_component_area_m2,
(3) postprocess kernel 크기, (4) 정합(ECC)이 10m와 같은 파라미터로 동작하지 않으므로
SkyWatch용 별도 프로젝트 config에서 재설정해야 한다. 또한 SkyWatch 전용 Provider
(Content Store 주문/다운로드)는 구현하지 않았다 - 구매 전에는 임의 영상을 만들지 않는다.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import Protocol

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds, transform as window_transform
from rasterio.enums import Resampling

import requests

from ..arcpy_pipeline.imagery_tasking import search_archive, sign_href as _sign_href

logger = logging.getLogger("imagery.providers")

S2_BANDS = ["B02", "B03", "B04", "B08"]
# SCL(Scene Classification) 값 중 "분석에 써도 되는" 클래스:
# 4 vegetation, 5 not vegetated, 6 water, 7 unclassified.
# 0 nodata, 1 saturated, 2 dark area, 3 cloud shadow, 8/9 cloud, 10 cirrus, 11 snow 는 제외.
SCL_CLEAR = (4, 5, 6, 7)
# L2A processing baseline >= 04.00(2022-01-25 이후 처리)은 DN에 +1000 offset이 있다.
BOA_OFFSET_BASELINE = 4.0
BOA_OFFSET_DN = 1000


def sign_href(href: str, retries: int = 6) -> str:
    """imagery_tasking.sign_href + 429(Too Many Requests) 지수 백오프 재시도."""
    for attempt in range(retries):
        try:
            return _sign_href(href)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning("[IMAGERY] sign %s - %ds 후 재시도", status, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


class ImageryProvider(Protocol):
    """year -> 해당 연도 대표 영상 1장(GeoTIFF) + 메타 record."""

    name: str

    def acquire(self, year: int, aoi_path: str | Path, out_dir: str | Path) -> dict: ...


def season_date_range(year: int, season: dict) -> tuple[str, str]:
    """season={"start":"04-10","end":"05-31"} -> ("YYYY-04-10","YYYY-05-31")."""
    return f"{year}-{season['start']}", f"{year}-{season['end']}"


def pick_scene(
    candidates: list[dict], target_mmdd: str, year: int, min_clear_fraction: float, clear_tolerance: float = 0.02
) -> dict | None:
    """AOI clear fraction이 기준 이상인 후보 중 "최고 clear 대비 clear_tolerance 이내"인 것들에서
    target 날짜(계절 위상)에 가장 가까운 장면을 고른다.

    candidates 각 항목은 {"date": "YYYY-MM-DD", "aoi_clear_fraction": float, ...}.
    기준을 만족하는 장면이 없으면 None (임의로 기준을 낮추지 않는다).
    """
    ok = [c for c in candidates if c["aoi_clear_fraction"] >= min_clear_fraction]
    if not ok:
        return None
    best = max(c["aoi_clear_fraction"] for c in ok)
    ok = [c for c in ok if c["aoi_clear_fraction"] >= best - clear_tolerance]
    target = date.fromisoformat(f"{year}-{target_mmdd}")
    return min(
        ok,
        key=lambda c: (abs((date.fromisoformat(c["date"]) - target).days), -c["aoi_clear_fraction"]),
    )


def _aoi_window(src, aoi: gpd.GeoDataFrame, buffer_m: float) -> Window:
    minx, miny, maxx, maxy = aoi.to_crs(src.crs).total_bounds
    return from_bounds(
        minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m, transform=src.transform
    ).round_offsets().round_lengths()


def aoi_clear_fraction(scl_href: str, aoi: gpd.GeoDataFrame) -> float:
    """SCL에서 AOI polygon 내부 픽셀 중 clear 클래스 비율(0~1). 장면이 AOI를 덮지 못하면 낮게 나온다."""
    with rasterio.open(sign_href(scl_href)) as src:
        win = _aoi_window(src, aoi, 0.0)
        scl = src.read(1, window=win, boundless=True, fill_value=0)
        tr = window_transform(win, src.transform)
        geom = [aoi.to_crs(src.crs).geometry.union_all()]
    inside = ~geometry_mask(geom, out_shape=scl.shape, transform=tr, all_touched=False)
    if inside.sum() == 0:
        return 0.0
    return float(np.isin(scl, SCL_CLEAR)[inside].sum() / inside.sum())


def _boa_offset(properties: dict) -> int:
    try:
        baseline = float(properties.get("s2:processing_baseline", "0"))
    except ValueError:
        baseline = 0.0
    return BOA_OFFSET_DN if baseline >= BOA_OFFSET_BASELINE else 0


class Sentinel2Provider:
    """Microsoft Planetary Computer STAC의 Sentinel-2 L2A (기존 imagery_tasking 재사용)."""

    name = "sentinel-2-l2a (Microsoft Planetary Computer)"

    def __init__(
        self,
        season: dict,
        max_scene_cloud_pct: float = 20.0,
        min_aoi_clear_fraction: float = 0.98,
        max_candidates: int = 8,
        window_buffer_m: float = 100.0,
        bands: list[str] | None = None,
    ):
        self.season = season
        self.max_scene_cloud_pct = max_scene_cloud_pct
        self.min_aoi_clear_fraction = min_aoi_clear_fraction
        self.max_candidates = max_candidates
        self.window_buffer_m = window_buffer_m
        self.bands = bands or S2_BANDS

    # -- 선택 -------------------------------------------------------------
    def select(self, year: int, aoi_path: str | Path) -> dict:
        aoi = gpd.read_file(aoi_path)
        minx, miny, maxx, maxy = aoi.to_crs("EPSG:4326").total_bounds
        start, end = season_date_range(year, self.season)
        scenes = search_archive(
            [minx, miny, maxx, maxy], f"{start}/{end}", max_cloud_pct=self.max_scene_cloud_pct
        )
        # 같은 날짜 중복 항목(PC가 동일 granule을 재처리본으로 중복 제공) 제거 - id 기준.
        seen, uniq = set(), []
        for s in scenes:
            if s["id"] not in seen:
                seen.add(s["id"])
                uniq.append(s)
        considered = []
        for s in uniq[: self.max_candidates]:
            try:
                frac = aoi_clear_fraction(s["assets"]["SCL"], aoi)
            except Exception as exc:  # 네트워크/타일 미포함 - 해당 후보만 탈락
                logger.warning("[IMAGERY] SCL 확인 실패 %s: %s", s["id"], exc)
                frac = 0.0
            considered.append({
                "scene_id": s["id"], "date": s["datetime"][:10], "platform": s.get("platform"),
                "scene_cloud_cover_pct": round(s["cloud_cover"], 2),
                "aoi_clear_fraction": round(frac, 4), "_scene": s,
            })
        chosen = pick_scene(considered, self.season["target_mmdd"], year, self.min_aoi_clear_fraction)
        public = [{k: v for k, v in c.items() if k != "_scene"} for c in considered]
        if chosen is None:
            raise RuntimeError(
                f"[IMAGERY] {year}년 {start}~{end}: AOI clear >= {self.min_aoi_clear_fraction:.0%} 인 "
                f"장면이 없습니다 (후보 {public}). season/기준을 config에서 명시적으로 바꾸세요."
            )
        return {"chosen": chosen, "considered": public}

    # -- 다운로드(AOI window만) -> 단일 스택 GeoTIFF ------------------------
    def acquire(self, year: int, aoi_path: str | Path, out_dir: str | Path) -> dict:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        sel = self.select(year, aoi_path)
        ch = sel["chosen"]
        scene = ch["_scene"]
        out_path = out_dir / f"s2_{year}_{ch['date']}.tif"
        aoi = gpd.read_file(aoi_path)
        offset = _boa_offset(scene["properties"])
        if not out_path.exists():
            self._write_stack(scene, aoi, out_path, offset)
        else:
            logger.info("[IMAGERY] cache hit: %s", out_path)
        return {
            "year": year, "date": ch["date"], "provider": self.name,
            "scene_id": ch["scene_id"], "platform": ch["platform"],
            "scene_cloud_cover_pct": ch["scene_cloud_cover_pct"],
            "aoi_clear_fraction": ch["aoi_clear_fraction"],
            "processing_baseline": scene["properties"].get("s2:processing_baseline"),
            "boa_offset_removed_dn": offset,
            "bands": list(self.bands), "resolution_m": 10.0, "path": str(out_path),
            "season_window": f"{self.season['start']}~{self.season['end']}",
            "selection_rule": (
                f"AOI clear>={self.min_aoi_clear_fraction:.0%} 후보 중 최고 clear 대비 2%p 이내에서 "
                f"{self.season['target_mmdd']}에 가장 가까운 날짜"
            ),
            "considered": sel["considered"],
        }

    def _write_stack(self, scene: dict, aoi: gpd.GeoDataFrame, out_path: Path, offset: int) -> None:
        arrays, profile, win_ref = [], None, None
        for band in self.bands:
            with rasterio.open(sign_href(scene["assets"][band])) as src:
                win = _aoi_window(src, aoi, self.window_buffer_m)
                data = src.read(1, window=win, boundless=True, fill_value=0)
                if profile is None:
                    profile = src.profile.copy()
                    profile.update(
                        driver="GTiff", count=len(self.bands), height=data.shape[0], width=data.shape[1],
                        transform=window_transform(win, src.transform), nodata=0, dtype="uint16",
                        compress="deflate",
                    )
                    win_ref = (win, src.transform, src.crs)
            arrays.append(data)
        stack = np.stack(arrays).astype(np.int32)
        valid = np.all(stack > 0, axis=0)

        # SCL로 구름/그림자/눈 픽셀을 nodata(0) 처리 - 변화로 오검출되지 않게 한다.
        with rasterio.open(sign_href(scene["assets"]["SCL"])) as scl_src:
            scl_win = _aoi_window(scl_src, aoi, self.window_buffer_m)
            scl = scl_src.read(
                1, window=scl_win, boundless=True, fill_value=0,
                out_shape=stack.shape[1:], resampling=Resampling.nearest,
            )
        valid &= np.isin(scl, SCL_CLEAR)

        stack = np.where(valid, np.clip(stack - offset, 1, 65535), 0).astype(np.uint16)
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(stack)
            dst.descriptions = tuple(self.bands)
        logger.info("[IMAGERY] 저장: %s shape=%s valid=%.1f%%", out_path, stack.shape, 100 * valid.mean())


class LocalGeoTiffProvider:
    """이미 확보한 GeoTIFF(예: 향후 구매한 SkyWatch 50cm)를 같은 파이프라인에 넣는 provider.

    files: {year: {"path": ..., "date": "YYYY-MM-DD", "scene_id": ...}} - 날짜/ID는 반드시
    실제 메타데이터를 config에 기입해야 한다(임의로 만들지 않음).
    """

    name = "local-geotiff"

    def __init__(self, files: dict[int, dict], bands: list[str] | None = None):
        self.files = {int(k): v for k, v in files.items()}
        self.bands = bands

    def acquire(self, year: int, aoi_path: str | Path, out_dir: str | Path) -> dict:
        if year not in self.files:
            raise KeyError(f"[IMAGERY] {year}년 로컬 영상이 config에 없습니다")
        spec = self.files[year]
        path = Path(spec["path"])
        if not path.exists():
            raise FileNotFoundError(f"[DATA] 로컬 영상이 없습니다: {path}")
        with rasterio.open(path) as src:
            res = abs(src.transform.a)
            descs = list(src.descriptions) if src.descriptions and all(src.descriptions) else self.bands
        return {
            "year": year, "date": spec["date"], "provider": self.name,
            "scene_id": spec.get("scene_id"), "bands": descs, "resolution_m": res, "path": str(path),
            "scene_cloud_cover_pct": spec.get("cloud_cover_pct"),
        }
