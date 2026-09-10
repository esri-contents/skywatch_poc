"""프로젝트 YAML 기반 다사업지 변화탐지 실행기."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
import rasterio
import yaml

from .data.project_acquisition import acquire_all, load_project
from .evaluation.report import build_human_validation_sample
from .evaluation.visualize import (
    plot_before_after_grid,
    plot_cadastre_context,
    plot_gi_star_hotspots,
    plot_priority_map,
)
from .pipeline import run_change_detection
from .preprocessing.alignment import verify_alignment
from .preprocessing.raster_preprocess import build_stacked_scene


def _epoch_map(cfg: dict) -> dict[str, dict]:
    return {e["id"]: e for e in cfg["imagery"]["epochs"]}


def _band_paths(cfg: dict, epoch: dict) -> dict[str, Path]:
    root = Path(cfg["paths"]["data_root"]) / "raw" / "imagery" / epoch["id"]
    return {b: root / f"{epoch['item_id']}_{b}.tif" for b in cfg["imagery"]["bands"]}


def preprocess(cfg: dict) -> dict[str, str]:
    root = Path(cfg["paths"]["data_root"]) / "processed" / "imagery"
    stacks = {}
    for epoch in cfg["imagery"]["epochs"]:
        out = root / f"{epoch['id']}_stack.tif"
        build_stacked_scene(
            _band_paths(cfg, epoch), cfg["paths"]["aoi"], out,
            dst_crs=cfg["project"]["analysis_crs"],
        )
        stacks[epoch["id"]] = str(out)
    return stacks


def _resolved_analysis_config(cfg: dict) -> Path:
    with open(cfg["analysis"]["base_config"], encoding="utf-8") as stream:
        resolved = yaml.safe_load(stream)
    resolved["project"] = cfg["project"]
    resolved["satellite"] = {
        **resolved.get("satellite", {}),
        "search_bbox_wgs84": [float(v) for v in gpd.read_file(cfg["paths"]["aoi"]).to_crs(4326).total_bounds],
    }
    resolved["classification"]["change_ratio_new_building_min"] = cfg["analysis"]["change_ratio_new_building_min"]
    resolved["compensation"] = cfg["compensation"]
    resolved["paths"] = cfg["paths"]
    resolved["publishing"] = cfg["publishing"]
    resolved["arcgis_pro"] = {
        **resolved.get("arcgis_pro", {}),
        "output_gdb": cfg["paths"]["output_gdb"],
    }
    out = Path(cfg["paths"]["output_root"]) / "resolved_analysis_config.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as stream:
        yaml.safe_dump(resolved, stream, allow_unicode=True, sort_keys=False)
    return out


def _alignment(a: str, b: str) -> dict:
    with rasterio.open(a) as x, rasterio.open(b) as y:
        if x.shape != y.shape or x.transform != y.transform or x.crs != y.crs:
            raise RuntimeError(f"시기 간 grid 불일치: {a} vs {b}")
        g1 = x.read([1, 2, 3]).astype("float32").mean(axis=0)
        g2 = y.read([1, 2, 3]).astype("float32").mean(axis=0)
        return verify_alignment(g1, g2, abs(x.transform.a))


def analyze(cfg: dict, stacks: dict[str, str]) -> dict:
    epochs = _epoch_map(cfg)
    resolved = _resolved_analysis_config(cfg)
    results = {}
    for left, right in cfg["analysis"]["comparison_pairs"]:
        code = f"{left}_{right}"
        out = Path(cfg["paths"]["output_root"]) / "comparisons" / code
        gdf = run_change_detection(
            stacks[left], stacks[right], cfg["paths"]["aoi"], cfg["paths"]["buildings"],
            config_path=resolved, t1_date=epochs[left]["date"], t2_date=epochs[right]["date"],
            out_dir=out, building_register_path=cfg["paths"]["building_register"],
        )
        site_ids = gdf["site_id"].astype("object").copy()
        site_ids.loc[site_ids.isna()] = [f"row_{i}" for i in site_ids.index[site_ids.isna()]]
        results[code] = {
            "out_dir": str(out), "candidate_count": len(gdf),
            "site_count": int(site_ids.nunique()),
            "priority": {str(k): int(v) for k, v in gdf["inspection_priority"].value_counts().items()},
            "change_type": {str(k): int(v) for k, v in gdf["change_type"].value_counts().items()},
            "register_match_count": int(gdf.get("has_register_match", pd.Series(False, index=gdf.index)).fillna(False).astype(bool).sum()),
            "alignment": _alignment(stacks[left], stacks[right]),
        }
        build_human_validation_sample(
            out / "vectors" / "building_change_results.gpkg",
            out / "reports" / "human_validation_sample.csv",
        )
        figures = out / "figures"
        plot_before_after_grid(
            stacks[left], stacks[right], out / "rasters" / "change_probability.tif",
            out / "vectors" / "building_change_results.gpkg", epochs[left]["date"],
            epochs[right]["date"], figures / "before_after_change.png",
        )
        plot_priority_map(stacks[right], out / "vectors" / "building_change_results.gpkg", cfg["paths"]["aoi"], figures / "priority_map.png")
        plot_gi_star_hotspots(stacks[right], out / "vectors" / "building_change_results.gpkg", cfg["paths"]["aoi"], figures / "hotspots.png")
        plot_cadastre_context(stacks[right], cfg["paths"]["cadastre"], out / "vectors" / "building_change_results.gpkg", cfg["paths"]["aoi"], figures / "cadastre_context.png")
    out = Path(cfg["paths"]["output_root"]) / "analysis_summary.json"
    with open(out, "w", encoding="utf-8") as stream:
        json.dump(results, stream, ensure_ascii=False, indent=2)
    return results


def write_data_inventory(cfg: dict) -> Path:
    roots = [Path(cfg["paths"]["data_root"]), Path(cfg["paths"]["output_root"])]
    rows = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix == ".lock":
                continue
            digest = hashlib.sha256()
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            selected_ids = {e["item_id"] for e in cfg["imagery"]["epochs"]}
            is_imagery = "raw\\imagery" in str(path) or "raw/imagery" in str(path)
            selected = (not is_imagery) or any(item_id in path.name for item_id in selected_ids)
            rows.append({
                "file_path": str(path), "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(), "selected_input_or_output": selected,
                "inventory_note": "superseded scene retained, not analyzed" if is_imagery and not selected else "",
            })
    out = Path(cfg["paths"]["reports"]) / "data_inventory.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    pd.DataFrame([
        {**source, "checked_at": cfg.get("sources_checked_at")}
        for source in cfg.get("official_sources", [])
    ]).to_csv(Path(cfg["paths"]["reports"]) / "official_source_register.csv", index=False, encoding="utf-8-sig")
    execution = {
        "project_id": cfg["project"]["id"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "project_config": "config/projects/hwaseong_jinan.yaml",
        "selected_imagery": cfg["imagery"]["epochs"],
        "aoi": cfg["aoi"],
        "compensation_baseline": cfg["compensation"],
    }
    (Path(cfg["paths"]["reports"]) / "execution_record.json").write_text(
        json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def prepare_arcpy_interchange(cfg: dict) -> Path:
    """Write a minimal UTF-8 GeoJSON to avoid ArcGIS GPKG code-page driver issues."""
    cadastre = gpd.read_file(cfg["paths"]["cadastre"])[["pnu", "jibun", "geometry"]].copy()
    cadastre = cadastre.drop_duplicates("pnu").to_crs(4326)
    out = Path(cfg["paths"]["data_root"]) / "processed" / "cadastre_arcpy.geojson"
    cadastre.to_file(out, driver="GeoJSON")
    return out


def run(project_config: str, acquire: bool = False) -> dict:
    cfg = load_project(project_config)
    if acquire:
        acquire_all(project_config)
    stacks = preprocess(cfg)
    results = analyze(cfg, stacks)
    prepare_arcpy_interchange(cfg)
    write_data_inventory(cfg)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--acquire", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.project, args.acquire), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
