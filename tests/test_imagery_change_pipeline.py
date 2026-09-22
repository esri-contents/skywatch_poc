"""imagery_change_pipeline.py end-to-end smoke test.

TEST DATA / SYNTHETIC DATA: 아래 T1/T2/AOI는 모두 이 테스트에서 만든 합성
데이터다. 실제 국가철도공단 노하리 분석 결과가 아니다.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import yaml
from rasterio.transform import from_origin
from shapely.geometry import box

from src.imagery_change_pipeline import run_imagery_change_detection


def _write_tif(path: Path, arr: np.ndarray, transform, crs="EPSG:5186", nodata=0, descriptions=None) -> None:
    profile = {
        "driver": "GTiff", "dtype": arr.dtype.name, "count": arr.shape[0],
        "height": arr.shape[1], "width": arr.shape[2],
        "crs": crs, "transform": transform, "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)
        if descriptions:
            dst.descriptions = descriptions


def _write_aoi(path: Path, bounds, crs="EPSG:5186") -> None:
    minx, miny, maxx, maxy = bounds
    gpd.GeoDataFrame({"geometry": [box(minx, miny, maxx, maxy)]}, crs=crs).to_file(path, driver="GeoJSON")


def _write_project_config(path: Path, aoi_path: Path, output_root: Path, band_order) -> None:
    cfg = {
        "project": {
            "id": "test_synthetic_rail", "name": "SYNTHETIC TEST PROJECT",
            "short_label": "SYNTHETIC TEST", "analysis_crs": "EPSG:5186",
        },
        "paths": {"aoi": str(aoi_path), "output_root": str(output_root)},
        "imagery": {"band_order": list(band_order), "resolution_m": 1.0},
        "analysis": {
            "base_config": "config/config.yaml",
            "threshold_method": "fixed",
            "mask_threshold": 0.3,
            "min_component_area_m2": 4,
            "confidence_thresholds": {"high": 0.7, "medium": 0.4},
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)


def _make_scene(rng, bands, size, change_box=None, change_delta=150):
    arr = rng.integers(500, 1500, size=(bands, size, size)).astype("uint16")
    if change_box:
        r0, r1, c0, c1 = change_box
        arr = arr.copy()
        arr[:, r0:r1, c0:c1] += change_delta
    return arr


@pytest.mark.parametrize("band_order", [["red", "green", "blue"], ["blue", "green", "red", "nir"]])
def test_end_to_end_synthetic_rgb_and_rgbnir(tmp_path, band_order):
    rng = np.random.default_rng(42)
    size = 40
    transform = from_origin(1000, 1000 + size, 1, 1)

    t1_arr = _make_scene(rng, len(band_order), size)
    t2_arr = _make_scene(rng, len(band_order), size, change_box=(10, 20, 10, 20))

    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_tif(t1_path, t1_arr, transform, descriptions=tuple(band_order))
    _write_tif(t2_path, t2_arr, transform, descriptions=tuple(band_order))

    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (1000, 1000, 1000 + size, 1000 + size))

    out_dir = tmp_path / "outputs"
    project_path = tmp_path / "project.yaml"
    _write_project_config(project_path, aoi_path, out_dir, band_order)

    summary = run_imagery_change_detection(
        t1_path, t2_path, project_path, t1_date="2024-01-01", t2_date="2024-06-01",
    )

    assert (out_dir / "rasters" / "t1_aligned.tif").exists()
    assert (out_dir / "rasters" / "t2_aligned.tif").exists()
    assert (out_dir / "rasters" / "change_probability.tif").exists()
    assert (out_dir / "rasters" / "change_mask.tif").exists()
    assert (out_dir / "vectors" / "change_polygons.gpkg").exists()
    assert (out_dir / "vectors" / "change_polygons.geojson").exists()
    assert (out_dir / "figures" / "before_after_change.png").exists()
    assert (out_dir / "analysis_summary.json").exists()
    assert (out_dir / "run_manifest.json").exists()

    assert summary["change_polygon_count"] >= 1
    assert summary["band_order"] == list(band_order)
    assert summary["threshold"] == 0.3
    assert "alignment" in summary
    assert "analysis_limitations" in summary

    gdf = gpd.read_file(out_dir / "vectors" / "change_polygons.gpkg")
    for col in [
        "change_id", "change_area_m2", "mean_change_score", "max_change_score",
        "brightness_t1", "brightness_t2", "brightness_delta", "t1_date", "t2_date",
        "method", "confidence",
    ]:
        assert col in gdf.columns


def test_missing_aoi_raises_clear_message(tmp_path):
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    arr = np.zeros((3, 5, 5), dtype="uint8")
    transform = from_origin(0, 5, 1, 1)
    _write_tif(t1_path, arr, transform, descriptions=("red", "green", "blue"))
    _write_tif(t2_path, arr, transform, descriptions=("red", "green", "blue"))

    missing_aoi = tmp_path / "aoi" / "does_not_exist.geojson"
    project_path = tmp_path / "project.yaml"
    _write_project_config(project_path, missing_aoi, tmp_path / "outputs", ["red", "green", "blue"])

    with pytest.raises(FileNotFoundError, match="AOI가 없습니다"):
        run_imagery_change_detection(t1_path, t2_path, project_path, "2024-01-01", "2024-06-01")


def test_missing_t1_raises(tmp_path):
    t2_path = tmp_path / "t2.tif"
    arr = np.zeros((3, 5, 5), dtype="uint8")
    _write_tif(t2_path, arr, from_origin(0, 5, 1, 1), descriptions=("red", "green", "blue"))
    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (0, 0, 5, 5))
    project_path = tmp_path / "project.yaml"
    _write_project_config(project_path, aoi_path, tmp_path / "outputs", ["red", "green", "blue"])

    with pytest.raises(FileNotFoundError):
        run_imagery_change_detection(tmp_path / "missing_t1.tif", t2_path, project_path, "2024-01-01", "2024-06-01")


def test_missing_t2_raises(tmp_path):
    t1_path = tmp_path / "t1.tif"
    arr = np.zeros((3, 5, 5), dtype="uint8")
    _write_tif(t1_path, arr, from_origin(0, 5, 1, 1), descriptions=("red", "green", "blue"))
    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (0, 0, 5, 5))
    project_path = tmp_path / "project.yaml"
    _write_project_config(project_path, aoi_path, tmp_path / "outputs", ["red", "green", "blue"])

    with pytest.raises(FileNotFoundError):
        run_imagery_change_detection(t1_path, tmp_path / "missing_t2.tif", project_path, "2024-01-01", "2024-06-01")


def test_empty_change_result_handled(tmp_path):
    """T1==T2(변화 없음) -> change_polygons가 비어도 파이프라인이 정상 종료해야 한다."""
    rng = np.random.default_rng(7)
    size = 20
    arr = rng.integers(500, 1500, size=(3, size, size)).astype("uint16")
    transform = from_origin(1000, 1000 + size, 1, 1)

    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_tif(t1_path, arr, transform, descriptions=("red", "green", "blue"))
    _write_tif(t2_path, arr, transform, descriptions=("red", "green", "blue"))

    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (1000, 1000, 1000 + size, 1000 + size))

    out_dir = tmp_path / "outputs"
    project_path = tmp_path / "project.yaml"
    _write_project_config(project_path, aoi_path, out_dir, ["red", "green", "blue"])

    summary = run_imagery_change_detection(
        t1_path, t2_path, project_path, t1_date="2024-01-01", t2_date="2024-06-01",
    )
    assert summary["change_polygon_count"] == 0
    assert summary["total_change_area_m2"] == 0.0
    assert (out_dir / "vectors" / "change_polygons.geojson").exists()
    assert (out_dir / "figures" / "before_after_change.png").exists()


def test_nodata_mismatch_handled(tmp_path):
    """T1/T2 nodata 값이 서로 달라도 정상 동작해야 한다."""
    rng = np.random.default_rng(9)
    size = 20
    arr1 = rng.integers(500, 1500, size=(3, size, size)).astype("uint16")
    arr2 = arr1.copy()
    transform = from_origin(1000, 1000 + size, 1, 1)

    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_tif(t1_path, arr1, transform, nodata=0, descriptions=("red", "green", "blue"))
    _write_tif(t2_path, arr2, transform, nodata=65535, descriptions=("red", "green", "blue"))

    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (1000, 1000, 1000 + size, 1000 + size))

    out_dir = tmp_path / "outputs"
    project_path = tmp_path / "project.yaml"
    _write_project_config(project_path, aoi_path, out_dir, ["red", "green", "blue"])

    summary = run_imagery_change_detection(
        t1_path, t2_path, project_path, t1_date="2024-01-01", t2_date="2024-06-01",
    )
    assert "change_polygon_count" in summary
