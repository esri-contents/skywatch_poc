"""Run the LH ArcPy workflow from the same project YAML as baseline analysis."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import arcpy
import yaml

from src.arcpy_pipeline.env import ensure_gdb, resolve_feature_path
from src.arcpy_pipeline.pipeline import run_change_intelligence


def _load_project(path: str) -> dict:
    with open(path, encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _band_paths(cfg: dict, epoch: dict) -> dict[str, Path]:
    root = Path(cfg["paths"]["data_root"]) / "raw" / "imagery" / epoch["id"]
    return {band: root / f'{epoch["item_id"]}_{band}.tif' for band in cfg["imagery"]["bands"]}


def _resolved_config(cfg: dict) -> Path:
    with open(cfg["analysis"]["base_config"], encoding="utf-8") as stream:
        resolved = yaml.safe_load(stream)
    resolved["project"] = cfg["project"]
    resolved["classification"]["change_ratio_new_building_min"] = cfg["analysis"]["change_ratio_new_building_min"]
    resolved["compensation"] = cfg["compensation"]
    resolved["paths"] = cfg["paths"]
    resolved["publishing"] = cfg["publishing"]
    resolved["arcgis_pro"] = {**resolved.get("arcgis_pro", {}), "output_gdb": cfg["paths"]["output_gdb"]}
    aoi = resolve_feature_path(cfg["paths"]["aoi"])
    ext = arcpy.Describe(aoi).extent.projectAs(arcpy.SpatialReference(4326))
    resolved["satellite"] = {**resolved.get("satellite", {}), "search_bbox_wgs84": [ext.XMin, ext.YMin, ext.XMax, ext.YMax]}
    output = Path(cfg["paths"]["output_root"]) / "resolved_analysis_config.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(resolved, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return output


def run(project_path: str) -> dict:
    cfg = _load_project(project_path)
    epochs = {x["id"]: x for x in cfg["imagery"]["epochs"]}
    config_path = _resolved_config(cfg)
    main_left, main_right = "t0", "t2"
    additional = []
    for left, right in cfg["analysis"]["comparison_pairs"]:
        if (left, right) == (main_left, main_right):
            continue
        candidate_path = Path(cfg["paths"]["output_root"]) / "comparisons" / f"{left}_{right}" / "vectors" / "building_change_results.gpkg"
        additional.append({
            "label": f"{left}_{right}",
            "results_fc": resolve_feature_path(str(candidate_path)),
            "t1": date.fromisoformat(epochs[left]["date"]),
            "t2": date.fromisoformat(epochs[right]["date"]),
        })
    gdb = ensure_gdb(cfg["paths"]["output_gdb"])
    parcel_json = Path(cfg["paths"]["data_root"]) / "processed" / "cadastre_arcpy.geojson"
    parcel_fc = str(Path(gdb) / "cadastre_source")
    if arcpy.Exists(parcel_fc):
        arcpy.management.Delete(parcel_fc)
    arcpy.conversion.JSONToFeatures(str(parcel_json), parcel_fc, "POLYGON")
    arcpy.management.RepairGeometry(parcel_fc, "DELETE_NULL")

    result = run_change_intelligence(
        {k: str(v) for k, v in _band_paths(cfg, epochs[main_left]).items()},
        {k: str(v) for k, v in _band_paths(cfg, epochs[main_right]).items()},
        cfg["paths"]["aoi"], cfg["paths"]["buildings"],
        parcel_path=parcel_fc,
        building_register_path=cfg["paths"]["building_register"],
        config_path=str(config_path),
        requirements_path="config/lh_requirements.yaml",
        t1_date=epochs[main_left]["date"], t2_date=epochs[main_right]["date"],
        out_gdb=cfg["paths"]["output_gdb"], additional_epochs=additional,
        reports_dir=cfg["paths"]["reports"],
    )
    status_path = Path(cfg["paths"]["reports"]) / "arcpy_execution_status.json"
    status_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.project), ensure_ascii=False, indent=2))
