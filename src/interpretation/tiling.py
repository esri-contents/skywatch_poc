"""지구 전체를 타일로 나눠 두 시기 영상을 같은 격자에서 분할하고 조각 특징을 계산한다.

판독_코드는 필지마다 주변 영상을 따로 잘라 분할했기 때문에, 같은 건물도 어느 필지 기준으로
자르느냐에 따라 조각이 달라졌다(여백 6m를 4·8·12m로 바꾸면 10~21칸이 뒤집힘). 여기서는 지구
전체를 고정 격자의 타일로 한 번만 분할하고, 필지 판정은 그 결과를 필지 경계로 집계한다.

타일은 core(결과를 쓰는 영역)와 pad(분할 문맥용 여백)로 이뤄진다. core 화소에는 그 타일의
조각 번호를 쓰고, 조각 특징은 pad까지 포함한 전체 조각으로 계산한다. 조각 번호는
타일 번호 * 100000 + 타일 안 번호로 전역에서 유일하다.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds, from_origin
from shapely.geometry import box

from . import obia

ID_STRIDE = 100_000
_STATE: dict = {}


def grid(aoi: gpd.GeoDataFrame, res: float, core_m: float) -> tuple[rasterio.Affine, int, int, list[tuple]]:
    """AOI를 덮는 res 격자와, AOI에 걸치는 core 타일 목록(tile_id, row0, col0, rows, cols)."""
    x0, y0, x1, y1 = aoi.total_bounds
    x0, y1 = np.floor(x0 / core_m) * core_m, np.ceil(y1 / core_m) * core_m
    width = int(np.ceil((x1 - x0) / core_m) * core_m / res)
    height = int(np.ceil((y1 - y0) / core_m) * core_m / res)
    transform = from_origin(x0, y1, res, res)
    step = int(round(core_m / res))
    geom = aoi.union_all()
    tiles = []
    for r in range(0, height, step):
        for c in range(0, width, step):
            bx0, by1 = transform * (c, r)
            if box(bx0, by1 - core_m, bx0 + core_m, by1).intersects(geom):
                tiles.append((len(tiles) + 1, r, c, step, step))
    return transform, height, width, tiles


def _init(image_paths: dict, aoi_path: str, buildings_path: str, crs: str) -> None:
    _STATE["images"] = image_paths
    _STATE["aoi"] = gpd.read_file(aoi_path).to_crs(crs).union_all()
    b = gpd.read_file(buildings_path).to_crs(crs)
    b = b[b.area > 0]
    _STATE["buildings"] = b.geometry.values
    _STATE["buildings_near"] = b.geometry.buffer(2.0).values


def _tile(args) -> dict:
    tile_id, r0, c0, rows, cols, transform, res, pad_px, seg = args
    x0, y1 = transform * (c0 - pad_px, r0 - pad_px)
    h, w = rows + 2 * pad_px, cols + 2 * pad_px
    bounds = (x0, y1 - h * res, x0 + w * res, y1)
    tt = from_bounds(*bounds, w, h)
    inside = rasterize([(_STATE["aoi"], 1)], out_shape=(h, w), transform=tt).astype(bool)
    fp = rasterize(((g, 1) for g in _STATE["buildings"]), out_shape=(h, w), transform=tt, fill=0).astype(np.float32)
    fp_near = rasterize(((g, 1) for g in _STATE["buildings_near"]), out_shape=(h, w), transform=tt, fill=0).astype(np.float32)
    core = (slice(pad_px, pad_px + rows), slice(pad_px, pad_px + cols))
    out = {"tile_id": tile_id, "r0": r0, "c0": c0}
    for epoch, path in _STATE["images"].items():
        rgb = obia.read_tile(path, bounds, res)
        valid = inside & (rgb.sum(axis=2) > 0.01)
        labels = obia.segment(rgb, seg["scale"], seg["sigma"], int(seg["min_m2"] / res ** 2))
        in_core = np.unique(labels[core][valid[core]])
        f = obia.segment_features(rgb, labels, valid, res, seg["ring_m"], seg["shadow_dl"], seg["shadow_l_max"])
        f = f[f["label"].isin(in_core)].copy()
        cnt = np.maximum(np.bincount(labels.ravel(), minlength=labels.max() + 1), 1)
        f["fp_frac"] = (np.bincount(labels.ravel(), weights=fp.ravel(), minlength=labels.max() + 1) / cnt)[f["label"]]
        f["fp_near"] = (np.bincount(labels.ravel(), weights=fp_near.ravel(), minlength=labels.max() + 1) / cnt)[f["label"]]
        f["touches_pad_edge"] = _touches_edge(labels, f["label"].to_numpy())
        x, y = tt * (f["cx"].to_numpy() + 0.5, f["cy"].to_numpy() + 0.5)
        f["x"], f["y"] = x, y
        f["seg_id"] = tile_id * ID_STRIDE + f["label"]
        f["tile_id"] = tile_id
        core_labels = np.where(valid[core], labels[core] + tile_id * ID_STRIDE, 0).astype(np.uint32)
        out[epoch] = (f.drop(columns=["cx", "cy"]), core_labels)
    return out


def _touches_edge(labels: np.ndarray, keep: np.ndarray) -> np.ndarray:
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    return np.isin(keep, edge)


def run(image_paths: dict[str, str], aoi_path: str, buildings_path: str, out_dir: Path, cfg: dict,
        crs: str = "EPSG:5186", workers: int | None = None) -> dict[str, Path]:
    """두 시기 영상을 타일 분할해 시기별 조각 라벨 GeoTIFF와 특징 parquet을 쓴다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    res, core_m = cfg["resolution_m"], cfg["tile_core_m"]
    aoi = gpd.read_file(aoi_path).to_crs(crs)
    transform, height, width, tiles = grid(aoi, res, core_m)
    pad_px = int(round(cfg["tile_pad_m"] / res))
    seg = cfg["segmentation"]
    profile = dict(driver="GTiff", width=width, height=height, count=1, dtype="uint32", crs=crs,
                   transform=transform, compress="deflate", tiled=True, blockxsize=512, blockysize=512, nodata=0)
    rasters = {e: rasterio.open(out_dir / f"segments_{e}.tif", "w", **profile) for e in image_paths}
    feats: dict[str, list] = {e: [] for e in image_paths}
    jobs = [(*t, transform, res, pad_px, seg) for t in tiles]
    workers = workers or min(8, max(1, (os.cpu_count() or 2) - 2))
    # 작업 프로세스마다 OpenBLAS가 코어 수만큼 스레드 버퍼를 잡으면 메모리 할당에 실패한다
    # (16코어 x 14프로세스에서 실측). 타일 단위로 이미 병렬이므로 프로세스당 1스레드로 둔다.
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = "1"
    try:
        with ProcessPoolExecutor(workers, initializer=_init,
                                 initargs=(image_paths, aoi_path, buildings_path, crs)) as pool:
            for i, res_t in enumerate(pool.map(_tile, jobs, chunksize=2), 1):
                for e in image_paths:
                    f, core_labels = res_t[e]
                    feats[e].append(f)
                    win = rasterio.windows.Window(res_t["c0"], res_t["r0"], core_labels.shape[1], core_labels.shape[0])
                    rasters[e].write(core_labels, 1, window=win)
                if i % 50 == 0 or i == len(jobs):
                    print(f"  타일 {i}/{len(jobs)}", flush=True)
    finally:
        for d in rasters.values():
            d.close()
    paths = {}
    for e, parts in feats.items():
        p = out_dir / f"segments_{e}.parquet"
        pd.concat(parts, ignore_index=True).to_parquet(p, index=False)
        paths[e] = p
    return paths
