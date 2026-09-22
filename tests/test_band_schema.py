from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from src.change_detection.band_schema import determine_band_order, resolve_band_roles


def test_resolve_band_roles_sentinel2_names():
    roles = resolve_band_roles(["B02", "B03", "B04", "B08"])
    assert roles == {"blue": 0, "green": 1, "red": 2, "nir": 3}


def test_resolve_band_roles_case_insensitive():
    roles = resolve_band_roles(["b02", "b03", "b04"])
    assert roles == {"blue": 0, "green": 1, "red": 2}


def test_resolve_band_roles_rgb_role_names():
    roles = resolve_band_roles(["red", "green", "blue"])
    assert roles == {"red": 0, "green": 1, "blue": 2}


def test_resolve_band_roles_rgbnir_role_names():
    roles = resolve_band_roles(["blue", "green", "red", "nir"])
    assert roles == {"blue": 0, "green": 1, "red": 2, "nir": 3}


def test_resolve_band_roles_missing_rgb_raises():
    with pytest.raises(ValueError):
        resolve_band_roles(["nir", "swir"])


def _write_tif(path: Path, count: int, descriptions=None):
    transform = from_origin(0, 10, 1, 1)
    profile = {
        "driver": "GTiff", "dtype": "uint8", "count": count,
        "height": 10, "width": 10, "crs": "EPSG:5186", "transform": transform,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.zeros((count, 10, 10), dtype="uint8"))
        if descriptions:
            dst.descriptions = descriptions


def test_determine_band_order_uses_geotiff_description(tmp_path):
    p = tmp_path / "t.tif"
    _write_tif(p, 3, descriptions=("red", "green", "blue"))
    assert determine_band_order(p, fallback_band_order=["should", "not", "be used"]) == ["red", "green", "blue"]


def test_determine_band_order_falls_back_to_config(tmp_path):
    p = tmp_path / "t.tif"
    _write_tif(p, 3, descriptions=None)
    assert determine_band_order(p, fallback_band_order=["red", "green", "blue"]) == ["red", "green", "blue"]


def test_determine_band_order_no_description_no_fallback_raises(tmp_path):
    p = tmp_path / "t.tif"
    _write_tif(p, 3, descriptions=None)
    with pytest.raises(ValueError):
        determine_band_order(p, fallback_band_order=None)


def test_determine_band_order_count_mismatch_raises(tmp_path):
    p = tmp_path / "t.tif"
    _write_tif(p, 3, descriptions=None)
    with pytest.raises(ValueError):
        determine_band_order(p, fallback_band_order=["red", "green", "blue", "nir"])
