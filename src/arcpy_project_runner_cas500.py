"""Run the LH ArcPy workflow using a local CAS500-1 FileGDB mosaic dataset instead
of Sentinel-2 band tiles.

같은 project YAML(`cas500_imagery` 블록)을 쓰지만, arcpy_project_runner.py와
갈라지는 지점은 다음 세 가지다:

1. **입력이 밴드별 파일이 아니라 이미 4밴드로 합쳐진 raster item이다** -
   `run_change_intelligence`의 t1_raster/t2_raster 경로를 쓴다
   (build_stacked_scene의 CompositeBands 단계를 건너뛴다).
2. **셀 크기가 10m가 아니라 원본 해상도(0.5m)다** - 다운샘플링하면 CAS500-1을
   쓰는 의미가 없다.
3. **다중 시기(additional_epochs)가 없다** - 이 프로젝트는 "같은 두 시점을
   Sentinel-2 vs CAS500-1로 각각 관찰해 비교"가 목적이라 comparison_pairs가
   t0/t1 하나뿐이다 (hwaseong_jinan의 3시점 progress_monitor와는 다른 설계).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import arcpy
import yaml

from src.arcpy_pipeline.env import ensure_gdb, resolve_feature_path
from src.arcpy_pipeline.pipeline import run_change_intelligence


def _load_project(path: str) -> dict:
    with open(path, encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _resolved_config(cfg: dict) -> Path:
    with open(cfg["analysis"]["base_config"], encoding="utf-8") as stream:
        resolved = yaml.safe_load(stream)
    resolved["project"] = cfg["project"]
    resolved["classification"]["change_ratio_new_building_min"] = cfg["analysis"]["change_ratio_new_building_min"]
    resolved["compensation"] = cfg["compensation"]
    resolved["paths"] = cfg["paths"]
    resolved["publishing"] = cfg["publishing"]
    # arcpy_project_runner.py와 동일 출력 gdb를 재사용하면 Sentinel-2 결과와
    # 충돌하므로, CAS500 전용 gdb 경로를 따로 둔다.
    out_gdb = str(Path(cfg["paths"]["output_gdb"]).with_name("jeju_gimnyeong_cas500.gdb"))
    resolved["arcgis_pro"] = {**resolved.get("arcgis_pro", {}), "output_gdb": out_gdb}
    aoi = resolve_feature_path(cfg["paths"]["aoi"])
    ext = arcpy.Describe(aoi).extent.projectAs(arcpy.SpatialReference(4326))
    resolved["satellite"] = {**resolved.get("satellite", {}), "search_bbox_wgs84": [ext.XMin, ext.YMin, ext.XMax, ext.YMax]}
    output = Path(cfg["paths"]["output_root"]) / "resolved_analysis_config_cas500.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(resolved, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return output, out_gdb


def run(project_path: str) -> dict:
    cfg = _load_project(project_path)
    cas_cfg = cfg["cas500_imagery"]
    epochs = {x["id"]: x for x in cas_cfg["epochs"]}
    config_path, out_gdb = _resolved_config(cfg)

    gdb = ensure_gdb(out_gdb)
    parcel_json = Path(cfg["paths"]["data_root"]) / "processed" / "cadastre_arcpy.geojson"
    parcel_fc = str(Path(gdb) / "cadastre_source")
    if arcpy.Exists(parcel_fc):
        arcpy.management.Delete(parcel_fc)
    arcpy.conversion.JSONToFeatures(str(parcel_json), parcel_fc, "POLYGON")
    arcpy.management.RepairGeometry(parcel_fc, "DELETE_NULL")

    reports_dir = str(Path(cfg["paths"]["reports"]) / "cas500")

    result = run_change_intelligence(
        aoi_path=cfg["paths"]["aoi"],
        buildings_path=cfg["paths"]["buildings"],
        parcel_path=parcel_fc,
        building_register_path=cfg["paths"]["building_register"],
        config_path=str(config_path),
        requirements_path="config/lh_requirements.yaml",
        t1_date=epochs["t0"]["date"], t2_date=epochs["t1"]["date"],
        out_gdb=out_gdb,
        reports_dir=reports_dir,
        t1_raster=epochs["t0"]["raster_path"],
        t2_raster=epochs["t1"]["raster_path"],
        cell_size=cas_cfg["native_cell_size_m"],
    )
    status_path = Path(reports_dir) / "arcpy_execution_status_cas500.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.project), ensure_ascii=False, indent=2))
