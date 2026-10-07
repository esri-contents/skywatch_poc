"""multitemporal_pipeline / imagery.providers / 실제 project config 파싱 테스트.

TEST DATA / SYNTHETIC DATA: tmp_path의 합성 래스터/AOI만 쓴다. 네트워크(STAC/VWorld)는 호출하지 않는다.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import yaml
from rasterio.transform import from_origin
from shapely.geometry import box

from src.imagery.providers import LocalGeoTiffProvider, pick_scene, season_date_range, _boa_offset
from src.multitemporal_pipeline import (
    build_provider, collect_polygons, load_config, resolve_pairs, stage_hotspots,
)

PROJECT_YAML = Path("config/projects/kr_rail_danyang.yaml")


def test_real_project_config_parses_and_is_consistent():
    cfg = load_config(PROJECT_YAML)
    years = cfg["imagery"]["years"]
    assert years == sorted(years) and years[0] >= 2016          # Sentinel-2는 2016 이전을 억지로 만들지 않는다
    assert cfg["aoi"]["status"] == "provisional"
    assert cfg["project"]["analysis_crs"] == "EPSG:5186"
    for key in ("top_n", "buffer_m", "cluster_distance_m", "min_roi_size_m"):
        assert key in cfg["hotspot"]
    # 비용/가격 관련 키가 없어야 한다(코드에서 추정하지 않음)
    assert "cost_comparison" not in cfg
    # base_config 파일이 실제 존재해야 imagery_change_pipeline이 동작한다
    assert Path(cfg["analysis"]["base_config"]).exists()
    # 연도쌍: Phase A는 단일 쌍, Phase B는 step + long_term
    a = resolve_pairs(cfg, "A")
    assert [p["pair"] for p in a] == ["2016_2026"]
    b = resolve_pairs(cfg, "B")
    steps = [p["pair"] for p in b if p["kind"] == "step"]
    assert steps == ["2016_2018", "2018_2020", "2020_2022", "2022_2024", "2024_2026"]
    assert {p["pair"] for p in b if p["kind"] == "long_term"} == {"2016_2026", "2020_2026"}


def test_resolve_pairs_rejects_unknown_phase_a_year():
    cfg = load_config(PROJECT_YAML)
    cfg["pairs"]["phase_a"] = [2010, 2026]
    with pytest.raises(ValueError):
        resolve_pairs(cfg, "A")


def test_build_provider_kinds():
    cfg = load_config(PROJECT_YAML)
    assert build_provider(cfg).name.startswith("sentinel-2")
    cfg["imagery"]["provider"] = "local_geotiff"
    assert isinstance(build_provider(cfg), LocalGeoTiffProvider)
    cfg["imagery"]["provider"] = "nope"
    with pytest.raises(ValueError):
        build_provider(cfg)


def test_pick_scene_prefers_target_date_among_clear():
    cands = [
        {"date": "2020-04-12", "aoi_clear_fraction": 0.999},
        {"date": "2020-05-03", "aoi_clear_fraction": 0.985},
        {"date": "2020-05-20", "aoi_clear_fraction": 0.90},   # 기준 미달
    ]
    assert pick_scene(cands, "05-01", 2020, 0.98)["date"] == "2020-05-03"
    assert pick_scene(cands, "05-01", 2020, 0.9999) is None    # 기준을 낮춰서 억지로 고르지 않는다


def test_season_window_and_boa_offset():
    assert season_date_range(2018, {"start": "04-10", "end": "05-31"}) == ("2018-04-10", "2018-05-31")
    assert _boa_offset({"s2:processing_baseline": "05.12"}) == 1000
    assert _boa_offset({"s2:processing_baseline": "02.12"}) == 0
    assert _boa_offset({}) == 0


def test_local_provider_requires_real_file(tmp_path):
    prov = LocalGeoTiffProvider({2024: {"path": str(tmp_path / "missing.tif"), "date": "2024-01-01"}})
    with pytest.raises(FileNotFoundError):
        prov.acquire(2024, tmp_path / "aoi.geojson", tmp_path)
    with pytest.raises(KeyError):
        prov.acquire(1999, tmp_path / "aoi.geojson", tmp_path)


def _synthetic_project(tmp_path: Path, pairs: list[dict]) -> dict:
    """변화 polygon이 0건인 연도쌍 결과 디렉터리를 흉내 낸 최소 project (파일 구조만 실제와 동일)."""
    cfg = load_config(PROJECT_YAML)
    out_root = tmp_path / "out"
    aoi = tmp_path / "aoi.geojson"
    gpd.GeoDataFrame(geometry=[box(0, 0, 200, 200)], crs="EPSG:5186").to_file(aoi, driver="GeoJSON")
    cfg["paths"].update(output_root=str(out_root), aoi=str(aoi), railway=str(tmp_path / "none.geojson"))
    for p in pairs:
        d = out_root / "comparisons" / p["pair"]
        (d / "vectors").mkdir(parents=True)
        (d / "rasters").mkdir(parents=True)
        gpd.GeoDataFrame(
            columns=["change_id", "change_area_m2", "mean_change_score", "max_change_score", "geometry"],
            geometry="geometry", crs="EPSG:5186",
        ).to_file(d / "vectors" / "change_polygons.gpkg", driver="GPKG", layer="change_polygons")
        with rasterio.open(
            d / "rasters" / "change_probability.tif", "w", driver="GTiff", height=20, width=20, count=1,
            dtype="float32", crs="EPSG:5186", transform=from_origin(0, 200, 10, 10),
        ) as dst:
            dst.write(np.zeros((20, 20), dtype="float32"), 1)
    return cfg


def test_stage_hotspots_with_zero_change_polygons(tmp_path):
    pairs = [
        {"year_from": 2016, "year_to": 2018, "kind": "step", "pair": "2016_2018"},
        {"year_from": 2018, "year_to": 2020, "kind": "step", "pair": "2018_2020"},
    ]
    cfg = _synthetic_project(tmp_path, pairs)
    cfg["imagery"]["years"] = [2016, 2018, 2020]
    assert collect_polygons(cfg, pairs).empty
    res = stage_hotspots(cfg, pairs)
    assert res["hotspots"].empty and res["roi"].empty
    out = Path(cfg["paths"]["output_root"])
    for f in ("vectors/hotspots.gpkg", "vectors/hotspots.geojson", "vectors/skywatch_roi.geojson",
              "vectors/change_polygons_all.gpkg", "rasters/change_intensity.tif",
              "reports/pair_change_summary.csv", "reports/hotspots.csv", "reports/recommended_acquisition_years.json"):
        assert (out / f).exists(), f
    summary = res["pair_summary"]
    assert (summary["polygon_count"] == 0).all()
    assert [y["year"] for y in res["acquisition"]["years"]] == [2016, 2020]


def test_collect_polygons_missing_run_is_explicit_error(tmp_path):
    cfg = _synthetic_project(tmp_path, [])
    with pytest.raises(FileNotFoundError):
        collect_polygons(cfg, [{"year_from": 2016, "year_to": 2018, "kind": "step", "pair": "2016_2018"}])
