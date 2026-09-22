"""국가철도공단 노하리 등 - 건물 footprint 없이 영상만으로 실행하는 경량 Change Detection Pipeline.

src/pipeline.py::run_change_detection()은 건물 footprint/건축물대장 조인/분류/
우선순위 스코어링/공간통계까지 수행하는 LH 업무용 파이프라인이다. 국가철도공단
1차 Demo는 "두 시점 영상에서 변화 가능성이 있는 영역을 탐지해 지도로 보여주는 것"
까지가 목적이고 건물 footprint를 요구하지 않으므로, 기존 run_change_detection()을
고치는 대신 이 모듈을 새로 추가한다.

Change Detection/후처리 알고리즘은 새로 만들지 않고 기존 모듈을 그대로 쓴다:
  - change_detection.baseline.run_baseline_change_detection (robust CVA + SSIM +
    edge/texture 앙상블 + threshold)
  - change_detection.postprocess.clean_mask / polygonize_change / compute_brightness_delta
  - preprocessing.alignment.verify_alignment (ECC 정합 QA)

기존 4-band Sentinel-2(B02/B03/B04/B08) 입력과 동일하게 동작하며, RGB(3-band)/
RGBNIR(4-band) GeoTIFF도 band_schema.resolve_band_roles()를 통해 지원한다.

결과는 건물 분류(NEW_BUILDING/DEMOLITION 등)를 하지 않고 "변화 후보(Potential
Change)" polygon 자체로만 표현한다 - 지장물 여부/보상 판정 자료가 아니다.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import geopandas as gpd
import rasterio
import yaml

from .change_detection.band_schema import determine_band_order
from .change_detection.baseline import run_baseline_change_detection, to_grayscale
from .change_detection.postprocess import clean_mask, compute_brightness_delta, polygonize_change
from .evaluation.visualize import plot_imagery_change_demo
from .preprocessing.alignment import verify_alignment
from .preprocessing.raster_preprocess import align_imagery_pair
from .utils.manifest import build_run_manifest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("imagery_change_pipeline")

ANALYSIS_LIMITATIONS_DEFAULT = [
    "본 결과는 영상 기반 자동 변화탐지 결과이며 보상 대상 지장물의 존재 여부 또는 "
    "보상 여부를 최종 판정하기 위한 자료가 아니다.",
    "사업 전·후 영상에서 변화 가능성이 높은 영역을 우선 선별하여 담당자의 확인 "
    "업무를 지원하기 위한 Decision Support 자료이다.",
    "계절 차이, 태양고도, 그림자, 센서 차이, 해상도 차이, 촬영각, 정합오차, 식생 "
    "변화 등이 오탐(false positive)을 유발할 수 있다.",
]


def _confidence(score: float | None, thresholds: dict) -> str | None:
    if score is None:
        return None
    if score >= thresholds["high"]:
        return "HIGH"
    if score >= thresholds["medium"]:
        return "MEDIUM"
    return "LOW"


def run_imagery_change_detection(
    t1_path: str | Path,
    t2_path: str | Path,
    project_config_path: str | Path,
    t1_date: str,
    t2_date: str,
    aoi_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    highlight_aoi_path: str | Path | None = None,
) -> dict:
    """영상(T1/T2) + AOI만으로 변화 후보 polygon/summary/Demo 이미지를 생성한다.

    Args:
        t1_path: T1 GeoTIFF (임의 CRS/해상도, 3~4 band).
        t2_path: T2 GeoTIFF.
        project_config_path: config/projects/kr_rail_nohari.yaml 등 프로젝트 설정.
        t1_date: T1 촬영일 (확정되지 않았으면 추정치임을 호출측에서 명시해야 함).
        t2_date: T2 촬영일.
        aoi_path: 분석에 실제로 쓸 AOI 벡터 경로. None이면 project config의
            paths.aoi를 쓴다.
        out_dir: 결과 저장 루트. None이면 project config의 paths.output_root를 쓴다.
        highlight_aoi_path: 분석 AOI가 실제 대상 필지보다 넓은 "Demo용 컨텍스트"일
            때, 실제 필지 경계를 표시하기 위한 별도 벡터 경로 (예: 노하리 149-2처럼
            위성 해상도 대비 필지가 너무 작아 분석 범위를 넓힌 경우). Demo 이미지에
            점선으로 겹쳐 그려지고 summary에 명시적 disclaimer가 추가된다. None이면
            일반적인 단일 AOI 실행(기존 동작과 동일).

    Returns:
        analysis_summary.json과 동일한 내용의 dict.
    """
    project_config_path = Path(project_config_path)
    if not project_config_path.exists():
        raise FileNotFoundError(f"[DATA] project config가 없습니다: {project_config_path}")
    with open(project_config_path, encoding="utf-8") as f:
        proj_cfg = yaml.safe_load(f)

    project_name = proj_cfg["project"].get("name", proj_cfg["project"]["id"])
    short_label = proj_cfg["project"].get("short_label", project_name)

    aoi_path = Path(aoi_path) if aoi_path else Path(proj_cfg["paths"]["aoi"])
    if not aoi_path.exists():
        raise FileNotFoundError(
            f"[DATA] {short_label} AOI가 없습니다.\n"
            f"{aoi_path.parent}/ 아래에 GeoJSON/GPKG를 준비하세요."
        )
    for label, p in [("t1", t1_path), ("t2", t2_path)]:
        if not Path(p).exists():
            raise FileNotFoundError(
                f"[DATA] {label} 경로가 존재하지 않습니다: {p}\n"
                "실제 데이터를 확보한 뒤 다시 실행하세요. 가짜 데이터로 진행하지 않습니다."
            )

    base_config_path = proj_cfg.get("analysis", {}).get("base_config", "config/config.yaml")
    with open(base_config_path, encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)

    analysis_cfg = proj_cfg.get("analysis", {})
    threshold_method = analysis_cfg.get(
        "threshold_method", base_cfg.get("change_detection", {}).get("threshold_method", "fixed")
    )
    mask_threshold = analysis_cfg.get(
        "mask_threshold", base_cfg.get("change_detection", {}).get("mask_threshold", 0.5)
    )
    min_component_area_m2 = analysis_cfg.get(
        "min_component_area_m2", base_cfg.get("postprocess", {}).get("min_component_area_m2", 25)
    )
    morphology_cfg = base_cfg.get("postprocess", {}).get("morphology", {})
    opening_kernel = morphology_cfg.get("opening_kernel", 3)
    closing_kernel = morphology_cfg.get("closing_kernel", 3)
    confidence_thresholds = analysis_cfg.get("confidence_thresholds", {"high": 0.7, "medium": 0.4})
    ssim_win_size = analysis_cfg.get("ssim_win_size", 7)

    out_dir = Path(out_dir) if out_dir else Path(proj_cfg["paths"]["output_root"])
    rasters_dir = out_dir / "rasters"
    vectors_dir = out_dir / "vectors"
    figures_dir = out_dir / "figures"
    reports_dir = out_dir / "reports"
    for d in (rasters_dir, vectors_dir, figures_dir, reports_dir):
        d.mkdir(parents=True, exist_ok=True)

    dst_crs = proj_cfg["project"].get("analysis_crs", "EPSG:5186")
    imagery_cfg = proj_cfg.get("imagery", {})
    resolution = imagery_cfg.get("resolution_m")
    fallback_band_order = imagery_cfg.get("band_order")

    t1_aligned = rasters_dir / "t1_aligned.tif"
    t2_aligned = rasters_dir / "t2_aligned.tif"
    logger.info("[PIPELINE] 영상 정렬(재투영/AOI clip/공통 grid) 시작")
    grid_info = align_imagery_pair(
        t1_path, t2_path, aoi_path, t1_aligned, t2_aligned,
        dst_crs=dst_crs, resolution=resolution,
    )

    band_order_t1 = determine_band_order(t1_aligned, fallback_band_order=fallback_band_order)
    band_order_t2 = determine_band_order(t2_aligned, fallback_band_order=fallback_band_order)
    if list(band_order_t1) != list(band_order_t2):
        raise ValueError(
            f"[BAND] T1/T2 밴드 순서가 다릅니다: {band_order_t1} vs {band_order_t2}"
        )
    band_order = list(band_order_t1)
    logger.info("[PIPELINE] band_order=%s", band_order)

    prob_path = rasters_dir / "change_probability.tif"
    mask_path = rasters_dir / "change_mask.tif"
    logger.info("[PIPELINE] Baseline Change Detection 시작 (threshold_method=%s)", threshold_method)
    _, _, used_threshold = run_baseline_change_detection(
        t1_aligned, t2_aligned, prob_path, mask_path,
        band_order=band_order, threshold_method=threshold_method, mask_threshold=mask_threshold,
        ssim_win_size=ssim_win_size,
    )

    with rasterio.open(mask_path) as src:
        raw_mask = src.read(1)
        transform = src.transform
        crs = src.crs
        pixel_area_m2 = abs(transform.a * transform.e)
    with rasterio.open(prob_path) as src:
        prob = src.read(1)

    logger.info("[PIPELINE] Mask 후처리 (opening/closing/min-area)")
    cleaned = clean_mask(
        raw_mask, pixel_area_m2,
        opening_kernel=opening_kernel, closing_kernel=closing_kernel,
        min_component_area_m2=min_component_area_m2,
    )

    logger.info("[PIPELINE] Polygon화")
    change_polygons = polygonize_change(cleaned, prob, transform, crs, t1_date, t2_date)
    change_polygons = compute_brightness_delta(change_polygons, t1_aligned, t2_aligned, band_order=band_order)
    if len(change_polygons):
        change_polygons["confidence"] = change_polygons["mean_change_score"].apply(
            lambda s: _confidence(s, confidence_thresholds)
        )
    else:
        change_polygons["confidence"] = []

    change_polygons.to_file(vectors_dir / "change_polygons.gpkg", driver="GPKG", layer="change_polygons")
    if len(change_polygons):
        change_polygons.to_crs("EPSG:4326").to_file(vectors_dir / "change_polygons.geojson", driver="GeoJSON")
    else:
        # 빈 GeoDataFrame.to_crs()는 crs가 없으면 실패하므로 별도 처리.
        gpd.GeoDataFrame(
            columns=list(change_polygons.columns), geometry="geometry", crs="EPSG:4326",
        ).to_file(vectors_dir / "change_polygons.geojson", driver="GeoJSON")

    logger.info("[PIPELINE] 정합 검증(ECC)")
    with rasterio.open(t1_aligned) as s1, rasterio.open(t2_aligned) as s2:
        gray1 = to_grayscale(s1.read(), band_order)
        gray2 = to_grayscale(s2.read(), band_order)
    alignment_result = verify_alignment(gray1, gray2, pixel_size_m=abs(transform.a))

    total_area = float(change_polygons["change_area_m2"].sum()) if len(change_polygons) else 0.0
    mean_area = float(change_polygons["change_area_m2"].mean()) if len(change_polygons) else 0.0
    max_score = float(change_polygons["max_change_score"].max()) if len(change_polygons) else 0.0

    summary = {
        "project": project_name,
        "project_id": proj_cfg["project"]["id"],
        "t1_date": t1_date,
        "t2_date": t2_date,
        "change_polygon_count": int(len(change_polygons)),
        "total_change_area_m2": round(total_area, 2),
        "mean_change_area_m2": round(mean_area, 2),
        "max_change_score": round(max_score, 4),
        "threshold_method": threshold_method,
        "threshold": used_threshold,
        "band_order": band_order,
        "alignment": alignment_result,
        "grid": {
            "crs": grid_info["crs"], "resolution_m": grid_info["resolution_m"],
            "width": grid_info["width"], "height": grid_info["height"],
        },
        "analysis_limitations": proj_cfg.get("analysis_limitations", ANALYSIS_LIMITATIONS_DEFAULT),
    }
    if highlight_aoi_path is not None:
        summary["highlight_note"] = (
            f"AOI({aoi_path}) 안에 {highlight_aoi_path}로 표시된 부분 영역이 별도로 "
            "강조되어 있다(예: 원래 요청받은 단일 필지가 해상도 문제로 인접 필지를 "
            "포함한 더 넓은 공식 AOI로 확장된 경우). 위 change_polygon_count/"
            "total_change_area_m2 등은 AOI 전체 기준이며, 강조 영역만의 결과가 "
            "아니다. before_after_change.png에 강조 영역 경계가 점선으로 표시된다."
        )
    with open(out_dir / "analysis_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info("[PIPELINE] analysis_summary.json 저장 완료: %s", out_dir / "analysis_summary.json")

    logger.info("[PIPELINE] Demo 이미지 생성")
    plot_imagery_change_demo(
        t1_aligned, t2_aligned, prob_path, change_polygons,
        figures_dir / "before_after_change.png",
        band_order=band_order, t1_label=t1_date, t2_label=t2_date,
        highlight_geom=highlight_aoi_path,
        highlight_label="실제 149-2 필지 경계" if highlight_aoi_path else "실제 필지 경계",
    )

    build_run_manifest(
        input_paths={
            "t1": t1_path, "t2": t2_path, "aoi": aoi_path,
            **({"highlight_aoi": highlight_aoi_path} if highlight_aoi_path else {}),
        },
        params={
            "threshold_method": threshold_method,
            "used_threshold": used_threshold,
            "min_component_area_m2": min_component_area_m2,
            "band_order": band_order,
            "grid": {k: v for k, v in grid_info.items() if k != "transform"},
            "confidence_thresholds": confidence_thresholds,
        },
        out_path=out_dir / "run_manifest.json",
        seed=base_cfg.get("random_seed", 42),
    )

    logger.info(
        "[PIPELINE] 완료: 변화 후보 %d건, 총 면적 %.1f m^2 (threshold=%.3f)",
        len(change_polygons), total_area, used_threshold,
    )
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Imagery-only lightweight Change Detection Pipeline (건물 footprint 불필요)"
    )
    parser.add_argument("--project", required=True, help="프로젝트 YAML 경로 (예: config/projects/kr_rail_nohari.yaml)")
    parser.add_argument("--t1", required=True, help="T1 GeoTIFF 경로")
    parser.add_argument("--t2", required=True, help="T2 GeoTIFF 경로")
    parser.add_argument("--aoi", default=None, help="AOI 벡터 경로 (생략 시 project config의 paths.aoi 사용)")
    parser.add_argument("--t1-date", required=True, help="T1 촬영일 (YYYY-MM-DD)")
    parser.add_argument("--t2-date", required=True, help="T2 촬영일 (YYYY-MM-DD)")
    parser.add_argument("--out-dir", default=None, help="결과 저장 루트 (생략 시 project config의 paths.output_root 사용)")
    parser.add_argument(
        "--highlight-aoi", default=None,
        help="AOI가 실제 필지보다 넓은 Demo용 컨텍스트일 때, 실제 필지 경계를 표시할 별도 벡터 경로",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = run_imagery_change_detection(
        args.t1, args.t2, args.project, args.t1_date, args.t2_date,
        aoi_path=args.aoi, out_dir=args.out_dir, highlight_aoi_path=args.highlight_aoi,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
