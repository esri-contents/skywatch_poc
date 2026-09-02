import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from src.change_detection.postprocess import clean_mask, compute_brightness_delta


def test_small_component_removed():
    mask = np.zeros((50, 50), dtype=np.uint8)
    mask[5, 5] = 1  # single-pixel noise -> 100m^2 with pixel_area=100
    cleaned = clean_mask(mask, pixel_area_m2=100, opening_kernel=1, closing_kernel=1, min_component_area_m2=500)
    assert cleaned.sum() == 0


def test_large_component_kept():
    mask = np.zeros((50, 50), dtype=np.uint8)
    mask[10:20, 10:20] = 1  # 100 pixels = 10,000 m^2
    cleaned = clean_mask(mask, pixel_area_m2=100, opening_kernel=1, closing_kernel=1, min_component_area_m2=500)
    assert cleaned.sum() > 0


def test_empty_mask_stays_empty():
    mask = np.zeros((30, 30), dtype=np.uint8)
    cleaned = clean_mask(mask, pixel_area_m2=100)
    assert cleaned.sum() == 0


def _write_stack(path, arr, nodata=0):
    transform = from_origin(0, arr.shape[1] * 10, 10, 10)
    profile = {
        "driver": "GTiff", "dtype": "uint16", "count": arr.shape[0],
        "height": arr.shape[1], "width": arr.shape[2],
        "crs": "EPSG:5186", "transform": transform, "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype("uint16"))


def test_brightness_delta_detects_brighter_t2(tmp_path):
    # T1: 전부 어두운 값(500). T2: 전부 밝은 값(3000) -> brightness_delta > 0 기대.
    t1 = np.full((4, 10, 10), 500, dtype=np.uint16)
    t2 = np.full((4, 10, 10), 3000, dtype=np.uint16)
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_stack(t1_path, t1)
    _write_stack(t2_path, t2)

    polys = gpd.GeoDataFrame(
        [{"change_id": "CHG_00001", "geometry": box(10, 10, 50, 50)}], crs="EPSG:5186",
    )
    out = compute_brightness_delta(polys, t1_path, t2_path)
    assert out["brightness_delta"].iloc[0] > 0
    assert out["brightness_t2"].iloc[0] > out["brightness_t1"].iloc[0]


def test_brightness_delta_empty_input_stays_empty():
    empty = gpd.GeoDataFrame(columns=["change_id", "geometry"], geometry="geometry", crs="EPSG:5186")
    out = compute_brightness_delta(empty, "unused_t1.tif", "unused_t2.tif")
    assert out.empty
    assert "brightness_delta" in out.columns
