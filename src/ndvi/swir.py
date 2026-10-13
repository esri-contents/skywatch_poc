"""B11(SWIR1, 20m) 확보: 기존 분석 장면과 '같은 Scene ID'의 공식 Sentinel-2 L2A 자산만 사용한다.

탐색 순서: (1) 로컬 캐시(raw/sentinel2_swir) -> (2) 같은 scene_id의 Microsoft Planetary Computer STAC item
(무료 공개 카탈로그, 유료 주문 API 아님). 동일 연도의 다른 촬영일 장면으로 대체하지 않는다.

기존 10m 스택과 동일한 보정 규칙을 적용한다: processing baseline >= 04.00 이면 BOA_ADD_OFFSET(+1000 DN)을
제거(기존 provider와 동일), SCL 구름/그림자/눈 마스크는 무효(0). 이미 보정된 값에 중복 적용하지 않도록
캐시 파일에 offset 제거 이력을 tag로 기록한다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.windows import Window, from_bounds
from rasterio.windows import transform as window_transform

from src.imagery.providers import SCL_CLEAR, _boa_offset, sign_href  # sign_href: 429 지수 백오프 포함

logger = logging.getLogger(__name__)

STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"


def swir_path(cache_dir: Path, year: int, scene_id: str) -> Path:
    return cache_dir / f"s2_{year}_B11_{scene_id}.tif"


def _find_item(scene_id: str, timeout: int = 60) -> dict | None:
    r = requests.post(STAC_SEARCH, json={"collections": ["sentinel-2-l2a"], "ids": [scene_id]}, timeout=timeout)
    r.raise_for_status()
    feats = r.json().get("features", [])
    return feats[0] if feats else None


def _fetch_one(item: dict, ref_tif: Path, out: Path, offset_override: int | None = None) -> dict:
    """item의 B11/SCL(20m)을 기준 10m GeoTIFF 범위를 덮는 20m 창으로 읽어 저장한다."""
    with rasterio.open(ref_tif) as ref:
        rb, rcrs = ref.bounds, ref.crs
    with rasterio.open(sign_href(item["assets"]["B11"]["href"])) as src:
        if src.crs != rcrs:
            raise RuntimeError(f"CRS 불일치: B11 {src.crs} vs 기준 {rcrs}")
        win = from_bounds(rb.left, rb.bottom, rb.right, rb.top, transform=src.transform)
        win = Window(int(np.floor(win.col_off)), int(np.floor(win.row_off)),
                     int(np.ceil(win.col_off + win.width)) - int(np.floor(win.col_off)),
                     int(np.ceil(win.row_off + win.height)) - int(np.floor(win.row_off)))
        b11 = src.read(1, window=win, boundless=True, fill_value=0).astype("int32")
        tr = window_transform(win, src.transform)
        res = src.res
        crs = src.crs
    with rasterio.open(sign_href(item["assets"]["SCL"]["href"])) as scl_src:
        scl_win = from_bounds(*rasterio.transform.array_bounds(win.height, win.width, tr), transform=scl_src.transform)
        scl = scl_src.read(1, window=scl_win.round_offsets().round_lengths(), boundless=True, fill_value=0,
                           out_shape=b11.shape, resampling=rasterio.enums.Resampling.nearest)
        scl_res = scl_src.res
    offset = _boa_offset(item["properties"]) if offset_override is None else offset_override
    valid = (b11 > 0) & np.isin(scl, SCL_CLEAR)
    arr = np.where(valid, np.clip(b11 - offset, 1, 65535), 0).astype("uint16")
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="uint16",
                       crs=crs, transform=tr, nodata=0, compress="deflate") as dst:
        dst.write(arr, 1)
        dst.descriptions = ("B11",)
        dst.update_tags(scene_id=item["id"], datetime=item["properties"]["datetime"],
                        processing_baseline=str(item["properties"].get("s2:processing_baseline")),
                        boa_offset_removed_dn=str(offset), scl_clear_masked="true",
                        native_resolution_m=str(res[0]))
    return {"offset_removed_dn": offset, "native_res_m": res[0], "scl_res_m": scl_res[0],
            "valid_fraction": float(valid.mean()), "shape": list(arr.shape)}


def ensure_swir(scenes: list, cache_dir: str | Path, offline: bool = False) -> dict:
    """연도별 B11을 확보하고 장면 일관성을 검증한다. 실패 연도는 status로 구분(허위 데이터 생성 안 함)."""
    cache_dir = Path(cache_dir)
    report: dict = {}
    for s in scenes:
        entry: dict = {"year": s.year, "scene_id": s.scene_id, "recorded_date": s.date,
                       "recorded_processing_baseline": s.processing_baseline,
                       "recorded_boa_offset_removed_dn": s.boa_offset_removed_dn}
        path = swir_path(cache_dir, s.year, s.scene_id or "unknown")
        try:
            if not s.scene_id:
                raise RuntimeError("scene_id 메타데이터 없음 - 같은 장면의 B11을 특정할 수 없음")
            if path.exists():
                entry["source"] = "local_cache"
                with rasterio.open(path) as src:
                    tags = src.tags()
                entry.update(path=str(path), cached_tags=tags)
            elif offline:
                raise RuntimeError("로컬 캐시 없음 + --offline")
            else:
                item = _find_item(s.scene_id)
                if item is None:
                    raise RuntimeError("STAC에서 동일 scene_id item을 찾지 못함")
                # 같은 관측 장면인지 검증
                idate = item["properties"]["datetime"][:10]
                if s.date and idate != s.date:
                    raise RuntimeError(f"촬영일 불일치: STAC {idate} vs 기록 {s.date}")
                ib = str(item["properties"].get("s2:processing_baseline"))
                if s.processing_baseline and ib != str(s.processing_baseline):
                    raise RuntimeError(f"processing baseline 불일치: STAC {ib} vs 기록 {s.processing_baseline}")
                info = _fetch_one(item, s.path, path)
                if s.boa_offset_removed_dn is not None and info["offset_removed_dn"] != s.boa_offset_removed_dn:
                    raise RuntimeError("B11 offset 제거값이 기존 10m 스택과 다름 - 보정 불일치")
                entry.update(source="planetary_computer_stac(same scene_id)", path=str(path),
                             stac_datetime=item["properties"]["datetime"], stac_baseline=ib,
                             collection="sentinel-2-l2a", fetched_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                             **info)
            entry["status"] = "OK"
        except Exception as e:  # noqa: BLE001
            entry.update(status="UNAVAILABLE", reason=f"{type(e).__name__}: {e}")
            if path.exists():
                path.unlink()  # 부분 파일 방치 금지
            logger.warning("[SWIR] %s: %s", s.year, entry["reason"])
        report[str(s.year)] = entry
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "swir_provenance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
