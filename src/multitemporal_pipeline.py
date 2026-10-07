"""다중시점 Wide-area 변화 Screening Pipeline (예: 한국철도공단 단양 매포읍 Sentinel-2 PoC).

흐름 (Imagery Provider -> 공통 전처리/변화탐지 -> polygon -> hotspot -> report):

    select  : 연도별 영상 선택/다운로드 (provider: sentinel2 | local_geotiff[향후 SkyWatch 50cm])
    run     : 연도쌍별 변화탐지 - src/imagery_change_pipeline.run_imagery_change_detection 재사용
    hotspots: 변화 polygon 군집 -> hotspot -> 고해상도 ROI / 촬영연도 추천
    report  : figure + sentinel2_baseline_report.md

실행 예:
    python -m src.multitemporal_pipeline --project config/projects/kr_rail_danyang.yaml --phase A --stage all
    python -m src.multitemporal_pipeline --project config/projects/kr_rail_danyang.yaml --phase B --stage all

결과는 change candidate / hotspot 이며 지장물·보상대상·불법건축물 판정이 아니다. 비용/가격/법적
기준일은 이 코드에서 다루지 않는다.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import yaml
from rasterio.features import geometry_mask

from .evaluation.multitemporal_figures import make_all_figures
from .evaluation.multitemporal_report import write_baseline_report
from .hotspot import (
    build_hotspots, build_roi, empty_polygons, pair_change_summary, pair_list,
    rank_hotspots, recommend_acquisition_years,
)
from .imagery.providers import ImageryProvider, LocalGeoTiffProvider, Sentinel2Provider
from .imagery_change_pipeline import run_imagery_change_detection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("multitemporal_pipeline")


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_pairs(cfg: dict, phase: str) -> list[dict]:
    """Phase A: phase_a 쌍 하나(최소 실행 경로). Phase B: 연속 연도쌍 + 장기 비교."""
    years = cfg["imagery"]["years"]
    pcfg = cfg.get("pairs", {})
    if phase.upper() == "A":
        a, b = pcfg.get("phase_a", [years[0], years[-1]])
        if a not in years or b not in years:
            raise ValueError(f"[CONFIG] phase_a {a},{b}가 imagery.years {years}에 없습니다")
        return [{"year_from": int(a), "year_to": int(b), "kind": "long_term", "pair": f"{a}_{b}"}]
    return pair_list(years, pcfg.get("consecutive", True), pcfg.get("long_term"))


def build_provider(cfg: dict) -> ImageryProvider:
    icfg = cfg["imagery"]
    kind = icfg.get("provider", "sentinel2")
    if kind == "sentinel2":
        return Sentinel2Provider(
            season=icfg["season"], max_scene_cloud_pct=icfg.get("max_scene_cloud_pct", 20),
            min_aoi_clear_fraction=icfg.get("min_aoi_clear_fraction", 0.98),
            bands=icfg.get("band_order"),
        )
    if kind == "local_geotiff":
        return LocalGeoTiffProvider(icfg.get("local_files", {}), bands=icfg.get("band_order"))
    raise ValueError(f"[CONFIG] 알 수 없는 imagery.provider: {kind}")


def stage_select(cfg: dict, years: list[int], reselect: bool = False) -> dict[int, dict]:
    """연도별 영상 선택/다운로드 -> reports/scene_selection.json (선택 근거 포함)."""
    out_root = Path(cfg["paths"]["output_root"])
    (out_root / "reports").mkdir(parents=True, exist_ok=True)
    provider = build_provider(cfg)
    sel_path = out_root / "reports" / "scene_selection.json"
    existing = json.loads(sel_path.read_text(encoding="utf-8")) if sel_path.exists() else {}
    records = {}
    for y in years:
        prev = existing.get(str(y))
        if prev and Path(prev["path"]).exists() and not reselect:
            # 네트워크 일시 오류(429 등)로 후보가 달라져 선택이 바뀌는 것을 막는다 - 재선택은 --reselect.
            logger.info("[PIPELINE] %d년 기존 선택 재사용: %s", y, prev["date"])
            records[y] = prev
        else:
            records[y] = provider.acquire(y, cfg["paths"]["aoi"], cfg["paths"]["imagery_dir"])
    existing.update({str(y): r for y, r in records.items()})
    sel_path.write_text(json.dumps(dict(sorted(existing.items())), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[PIPELINE] scene_selection.json 저장: %s", sel_path)
    return records


def load_scene_selection(cfg: dict) -> dict[int, dict]:
    p = Path(cfg["paths"]["output_root"]) / "reports" / "scene_selection.json"
    if not p.exists():
        raise FileNotFoundError(f"[DATA] {p} 없음 - 먼저 --stage select 를 실행하세요")
    return {int(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items()}


def stage_run(cfg: dict, config_path: str, pairs: list[dict], records: dict[int, dict], force: bool = False) -> None:
    """연도쌍별 변화탐지 (기존 imagery_change_pipeline 재사용, 결과는 comparisons/<pair>/)."""
    out_root = Path(cfg["paths"]["output_root"])
    for p in pairs:
        out_dir = out_root / "comparisons" / p["pair"]
        r1, r2 = records[p["year_from"]], records[p["year_to"]]
        summary_path = out_dir / "analysis_summary.json"
        if summary_path.exists() and not force:
            prev = json.loads(summary_path.read_text(encoding="utf-8"))
            if (prev.get("t1_date"), prev.get("t2_date")) == (r1["date"], r2["date"]):
                logger.info("[PIPELINE] %s 결과 존재 - skip (--force로 재실행)", p["pair"])
                continue
            logger.info("[PIPELINE] %s 선택 영상이 바뀌어(%s/%s -> %s/%s) 재실행", p["pair"],
                        prev.get("t1_date"), prev.get("t2_date"), r1["date"], r2["date"])
        logger.info("[PIPELINE] 변화탐지 %s (%s -> %s)", p["pair"], r1["date"], r2["date"])
        run_imagery_change_detection(
            r1["path"], r2["path"], config_path, r1["date"], r2["date"],
            aoi_path=cfg["paths"]["aoi"], out_dir=out_dir,
        )


def collect_polygons(cfg: dict, pairs: list[dict]) -> gpd.GeoDataFrame:
    """comparisons/<pair>/vectors/change_polygons.gpkg를 하나로 합친다 (0건 쌍이 있어도 정상)."""
    crs = cfg["project"]["analysis_crs"]
    out_root = Path(cfg["paths"]["output_root"])
    frames = []
    for p in pairs:
        gpkg = out_root / "comparisons" / p["pair"] / "vectors" / "change_polygons.gpkg"
        if not gpkg.exists():
            raise FileNotFoundError(f"[DATA] {gpkg} 없음 - 먼저 --stage run 을 실행하세요")
        g = gpd.read_file(gpkg, layer="change_polygons")
        if g.empty:
            continue
        g = g.to_crs(crs)
        g["pair"], g["year_from"], g["year_to"], g["pair_kind"] = p["pair"], p["year_from"], p["year_to"], p["kind"]
        frames.append(g[["pair", "year_from", "year_to", "pair_kind", "change_area_m2", "mean_change_score", "geometry"]])
    if not frames:
        return empty_polygons(crs)
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=crs)


def build_change_intensity(cfg: dict, pairs: list[dict], out_path: Path) -> Path | None:
    """step 쌍(없으면 전체 쌍) change_probability의 픽셀별 최대값 -> AOI 안쪽만 유효한 raster."""
    out_root = Path(cfg["paths"]["output_root"])
    use = [p for p in pairs if p["kind"] == "step"] or pairs
    arrays, ref = [], None
    for p in use:
        tif = out_root / "comparisons" / p["pair"] / "rasters" / "change_probability.tif"
        if not tif.exists():
            continue
        with rasterio.open(tif) as src:
            if ref is None:
                ref = src.profile.copy()
            elif src.shape != (ref["height"], ref["width"]):
                raise ValueError("[RASTER] 연도쌍 간 grid가 다릅니다 - imagery.resolution_m을 고정하세요")
            arrays.append(src.read(1))
    if not arrays:
        return None
    inten = np.max(np.stack(arrays), axis=0).astype("float32")
    aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(ref["crs"])
    inside = ~geometry_mask([aoi.geometry.union_all()], out_shape=inten.shape, transform=ref["transform"])
    inten = np.where(inside, inten, -9999.0).astype("float32")
    ref.update(count=1, dtype="float32", nodata=-9999.0, compress="deflate")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **ref) as dst:
        dst.write(inten, 1)
    return out_path


def _save_vector(gdf: gpd.GeoDataFrame, base: Path, layer: str) -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(base.with_suffix(".gpkg"), driver="GPKG", layer=layer)
    out4326 = gdf.to_crs("EPSG:4326") if gdf.crs is not None else gdf
    out4326.to_file(base.with_suffix(".geojson"), driver="GeoJSON")


def stage_hotspots(cfg: dict, pairs: list[dict]) -> dict:
    """polygon 합치기 -> hotspot -> ROI -> 연도쌍 요약 -> 촬영연도 추천. vectors/ reports/ rasters/ 저장."""
    out_root = Path(cfg["paths"]["output_root"])
    hcfg = cfg["hotspot"]
    polygons = collect_polygons(cfg, pairs)

    railway = None
    rail_path = Path(cfg["paths"].get("railway", ""))
    if rail_path.is_file():
        rw = gpd.read_file(rail_path).to_crs(cfg["project"]["analysis_crs"])
        # 운영 중 철도(railway=rail, 야드 제외)만 거리 계산에 쓴다 - OSM 비공식 데이터.
        rw = rw[(rw["railway"] == "rail") & (rw.get("service", pd.Series(index=rw.index, dtype=object)).isna())]
        railway = rw if len(rw) else None

    hs = build_hotspots(
        polygons, hcfg["cluster_distance_m"], hcfg["min_hotspot_area_m2"], railway=railway,
    )
    hs = rank_hotspots(hs, hcfg["top_n"], hcfg["recommend_min_area_m2"], hcfg["recommend_min_score"])
    roi = build_roi(hs, hcfg["buffer_m"], hcfg["min_roi_size_m"], hcfg["roi_overlap_merge"], hcfg.get("max_roi_size_m"))
    corridor = None
    if railway is not None and hcfg.get("railway_corridor_buffer_m"):
        corridor = railway.geometry.union_all().buffer(hcfg["railway_corridor_buffer_m"])
    summary = pair_change_summary(polygons, pairs, hs, corridor)
    rec = recommend_acquisition_years(
        summary, cfg["imagery"]["years"], cfg.get("acquisition_years", {}).get("active_fraction_of_max", 0.25),
    )

    vec = out_root / "vectors"
    _save_vector(polygons, vec / "change_polygons_all", "change_polygons_all")
    _save_vector(hs, vec / "hotspots", "hotspots")
    _save_vector(roi, vec / "skywatch_roi", "skywatch_roi")
    intensity = build_change_intensity(cfg, pairs, out_root / "rasters" / "change_intensity.tif")

    rep = out_root / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    summary.to_csv(rep / "pair_change_summary.csv", index=False, encoding="utf-8-sig")
    hs.drop(columns="geometry").to_csv(rep / "hotspots.csv", index=False, encoding="utf-8-sig")
    roi.drop(columns="geometry").to_csv(rep / "skywatch_roi.csv", index=False, encoding="utf-8-sig")
    (rep / "recommended_acquisition_years.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "[PIPELINE] polygon %d건 -> hotspot %d건 (추천 %d건), ROI %d건",
        len(polygons), len(hs), int(hs["recommended_for_highres"].sum()) if len(hs) else 0, len(roi),
    )
    return {
        "polygons": polygons, "hotspots": hs, "roi": roi, "pair_summary": summary,
        "acquisition": rec, "intensity_path": intensity, "railway": railway,
    }


def stage_report(cfg: dict, pairs: list[dict], results: dict, records: dict[int, dict], phase: str) -> Path:
    figures = make_all_figures(cfg, pairs, results, records)
    return write_baseline_report(cfg, pairs, results, records, figures, phase)


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="다중시점 Wide-area 변화 Screening (Sentinel-2 -> hotspot -> SkyWatch ROI)")
    ap.add_argument("--project", required=True, help="프로젝트 YAML (예: config/projects/kr_rail_danyang.yaml)")
    ap.add_argument("--phase", choices=["A", "B"], default="A", help="A: 첫/마지막 연도 1쌍, B: 연속쌍+장기쌍")
    ap.add_argument("--stage", choices=["select", "run", "hotspots", "report", "all"], default="all")
    ap.add_argument("--reselect", action="store_true", help="기존 scene_selection.json 선택을 무시하고 영상을 다시 선택")
    ap.add_argument("--force", action="store_true", help="기존 연도쌍 결과가 있어도 재실행")
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.project)
    pairs = resolve_pairs(cfg, args.phase)
    needed_years = sorted({y for p in pairs for y in (p["year_from"], p["year_to"])})
    logger.info("[PIPELINE] phase=%s pairs=%s years=%s", args.phase, [p["pair"] for p in pairs], needed_years)

    if args.stage in ("select", "all"):
        stage_select(cfg, needed_years, reselect=args.reselect)
    if args.stage == "select":
        return
    records = load_scene_selection(cfg)
    if args.stage in ("run", "all"):
        stage_run(cfg, args.project, pairs, records, force=args.force)
    if args.stage == "run":
        return
    results = stage_hotspots(cfg, pairs)
    if args.stage in ("report", "all"):
        path = stage_report(cfg, pairs, results, records, args.phase)
        logger.info("[PIPELINE] 보고서: %s", path)


if __name__ == "__main__":
    main()
