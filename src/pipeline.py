"""Goyang Changneung Building Change Intelligence PoC - 전체 파이프라인 진입점.

입력은 전처리 완료된 T1/T2 스택 GeoTIFF(raster_preprocess.build_stacked_scene
결과, 동일 grid/CRS/shape)와 AOI로 clip된 건물 footprint(build_buildings.py
결과)를 받는다. 원본 위성/항공영상 -> 스택 변환은 영상 소스마다 다르므로
(Sentinel-2 vs NGII 등) 이 파이프라인 앞단에서 별도로 수행한다. 이렇게
분리해두면 향후 SkyWatch 등 다른 영상 소스를 투입할 때도 스택 생성
단계만 교체하면 된다.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
import rasterio
import yaml

from .buildings.classify import classify_building_changes, classify_unmatched_changes
from .buildings.overlay import overlay_buildings_with_changes
from .buildings.validation import compute_administrative_uncertainty, join_building_register
from .change_detection.baseline import run_baseline_change_detection
from .change_detection.postprocess import clean_mask, polygonize_change
from .scoring.priority import compute_priority_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pipeline")


def run_change_detection(
    t1_path: str | Path,
    t2_path: str | Path,
    aoi_path: str | Path,
    building_path: str | Path,
    config_path: str | Path = "config/config.yaml",
    t1_date: str = "2022",
    t2_date: str = "2024",
    out_dir: str | Path = "outputs",
    building_register_path: str | Path | None = None,
) -> gpd.GeoDataFrame:
    """전체 Change Detection 파이프라인 실행 (STEP 9~14, 건축물대장 있으면 STEP 13도).

    Args:
        t1_path: T1 스택 GeoTIFF (전처리 완료본).
        t2_path: T2 스택 GeoTIFF (T1과 동일 grid).
        aoi_path: AOI GeoPackage.
        building_path: AOI로 clip된 건물 footprint GeoPackage.
        config_path: 파라미터 설정 파일.
        t1_date: T1 촬영일(ISO 형식 "YYYY-MM-DD" 권장).
        t2_date: T2 촬영일.
        out_dir: 결과물 저장 루트 디렉터리.
        building_register_path: download.fetch_building_title_info() 결과를
            json.dump()한 파일 경로. 있으면 STEP 13(행정정보 Validation)을
            실제로 수행해 administrative_uncertainty를 계산한다. 없으면
            전 후보를 1.0(완전 불확실)로 둔다.

    Returns:
        building_change_results (change_type/priority_score 포함) GeoDataFrame.
        건물과 무관한 change(OTHER_CHANGE/DEMOLITION 후보 등)도 포함한다.
    """
    for label, p in [("t1", t1_path), ("t2", t2_path), ("aoi", aoi_path), ("buildings", building_path)]:
        if not Path(p).exists():
            raise FileNotFoundError(
                f"[DATA] {label} 경로가 존재하지 않습니다: {p}\n"
                "실제 데이터를 확보한 뒤 다시 실행하세요. 가짜 데이터로 진행하지 않습니다."
            )

    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    out_dir = Path(out_dir)
    prob_path = out_dir / "rasters" / "change_probability.tif"
    mask_path = out_dir / "rasters" / "change_mask.tif"

    logger.info("[CHANGE] Baseline Change Detection 시작")
    run_baseline_change_detection(t1_path, t2_path, prob_path, mask_path)

    with rasterio.open(mask_path) as src:
        raw_mask = src.read(1)
        transform = src.transform
        crs = src.crs
        pixel_area_m2 = abs(transform.a * transform.e)
    with rasterio.open(prob_path) as src:
        prob = src.read(1)

    pp_cfg = cfg["postprocess"]
    logger.info("[CHANGE] Mask 후처리 (opening/closing/min-area)")
    cleaned = clean_mask(
        raw_mask, pixel_area_m2,
        opening_kernel=pp_cfg["morphology"]["opening_kernel"],
        closing_kernel=pp_cfg["morphology"]["closing_kernel"],
        min_component_area_m2=pp_cfg["min_component_area_m2"],
    )

    logger.info("[CHANGE] Polygon화")
    change_polygons = polygonize_change(cleaned, prob, transform, crs, t1_date, t2_date)

    vec_dir = out_dir / "vectors"
    vec_dir.mkdir(parents=True, exist_ok=True)
    change_polygons.to_file(vec_dir / "change_polygons.gpkg", driver="GPKG", layer="change_polygons")

    buildings = gpd.read_file(building_path)

    if building_register_path and Path(building_register_path).exists():
        logger.info("[VALIDATION] 건축물대장 조인 (STEP 13)")
        with open(building_register_path, encoding="utf-8") as f:
            register_items = json.load(f)
        buildings = join_building_register(buildings, register_items)

        t1_d = _parse_date(t1_date)
        t2_d = _parse_date(t2_date)
        buildings["administrative_uncertainty"] = buildings.apply(
            lambda row: compute_administrative_uncertainty(row, t1_d, t2_d), axis=1
        )

    logger.info("[BUILDING] Overlay + 분류")
    buffer_m = cfg["classification"]["buffer_distances_m"][0]
    overlaid = overlay_buildings_with_changes(buildings, change_polygons, buffer_m=buffer_m)

    classified = classify_building_changes(
        overlaid, new_building_ratio_min=cfg["classification"]["change_ratio_new_building_min"],
    )
    building_results = classified[classified["change_type"].notna()].copy()

    unmatched = classify_unmatched_changes(change_polygons, buildings)

    non_empty = [g for g in (building_results, unmatched) if len(g)]
    combined = gpd.GeoDataFrame(pd.concat(non_empty, ignore_index=True), crs=buildings.crs) if non_empty else building_results

    logger.info("[SCORING] Priority Score 계산")
    scored = compute_priority_score(
        combined,
        weights=cfg["priority_scoring"]["weights"],
        high_threshold=cfg["priority_scoring"]["thresholds"]["high"],
        medium_threshold=cfg["priority_scoring"]["thresholds"]["medium"],
    )

    scored.to_file(vec_dir / "building_change_results.gpkg", driver="GPKG", layer="building_change_results")
    scored.to_file(vec_dir / "building_change_results.geojson", driver="GeoJSON")

    report_dir = out_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary_cols = [c for c in scored.columns if c != "geometry"]
    scored[summary_cols].to_csv(report_dir / "building_change_summary.csv", index=False)

    logger.info(
        "[PIPELINE] 완료: 총 %d개 변화 후보 (%s)",
        len(scored), scored["inspection_priority"].value_counts().to_dict(),
    )
    return scored


def _parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"[PIPELINE] 날짜 형식을 인식할 수 없습니다: {s}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Goyang Changneung Building Change Intelligence PoC")
    parser.add_argument("--t1", required=True, help="T1 스택 GeoTIFF 경로")
    parser.add_argument("--t2", required=True, help="T2 스택 GeoTIFF 경로")
    parser.add_argument("--buildings", required=True, help="건물 footprint GeoPackage 경로")
    parser.add_argument("--aoi", required=True, help="AOI 벡터 경로")
    parser.add_argument("--config", default="config/config.yaml", help="설정 파일 경로")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_change_detection(args.t1, args.t2, args.aoi, args.buildings, args.config)


if __name__ == "__main__":
    main()
