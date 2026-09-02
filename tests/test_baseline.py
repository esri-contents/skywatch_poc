from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from src.change_detection.baseline import run_baseline_change_detection

BAND_ORDER = ["B02", "B03", "B04", "B08"]


def _write_stack(path: Path, arr: np.ndarray, nodata: float | None = 0) -> None:
    transform = from_origin(0, arr.shape[1] * 10, 10, 10)
    profile = {
        "driver": "GTiff", "dtype": "uint16", "count": arr.shape[0],
        "height": arr.shape[1], "width": arr.shape[2],
        "crs": "EPSG:5186", "transform": transform, "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype("uint16"))


def test_identical_scenes_produce_no_change(tmp_path):
    rng = np.random.default_rng(0)
    arr = rng.integers(500, 2000, size=(4, 20, 20))
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_stack(t1_path, arr)
    _write_stack(t2_path, arr)

    prob_path, mask_path, used_threshold = run_baseline_change_detection(
        t1_path, t2_path, tmp_path / "prob.tif", tmp_path / "mask.tif",
    )

    with rasterio.open(mask_path) as src:
        mask = src.read(1)
    assert mask.sum() == 0
    assert used_threshold == 0.5  # default is threshold_method="fixed"


def test_default_threshold_method_is_fixed(tmp_path):
    """threshold_method를 지정하지 않으면 (otsu가 아니라) 고정 0.5를 써야 한다.

    otsu가 기본값이면 이 AOI에서 실측된 것처럼 변화 후보가 급증할 수 있어
    (14.87% vs 3.82%), 명시적으로 opt-in하지 않는 한 보수적인 fixed를 쓴다.
    """
    rng = np.random.default_rng(1)
    t1 = rng.integers(500, 2000, size=(4, 15, 15))
    t2 = t1.copy()
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_stack(t1_path, t1)
    _write_stack(t2_path, t2)

    *_, used_threshold = run_baseline_change_detection(
        t1_path, t2_path, tmp_path / "prob.tif", tmp_path / "mask.tif",
    )
    assert used_threshold == 0.5


def test_nodata_region_excluded_from_change_mask(tmp_path):
    rng = np.random.default_rng(2)
    t1 = rng.integers(500, 2000, size=(4, 20, 20))
    t2 = t1.copy()
    # 우측 절반은 AOI 바깥(nodata=0)이라고 가정하되, 값 자체는 큰 변화처럼 보이게 둔다.
    t1[:, :, 10:] = 0
    t2[:, :, 10:] = 9000

    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_stack(t1_path, t1)
    _write_stack(t2_path, t2)

    _, mask_path, _ = run_baseline_change_detection(
        t1_path, t2_path, tmp_path / "prob.tif", tmp_path / "mask.tif",
        threshold_method="fixed", mask_threshold=0.01,
    )
    with rasterio.open(mask_path) as src:
        mask = src.read(1)
    # nodata 영역(우측 절반)은 어떤 임계값을 써도 change_mask에 포함되면 안 된다.
    assert mask[:, 10:].sum() == 0


def test_shape_mismatch_raises(tmp_path):
    t1 = np.zeros((4, 10, 10))
    t2 = np.zeros((4, 5, 5))
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_stack(t1_path, t1)
    _write_stack(t2_path, t2)

    with pytest.raises(ValueError):
        run_baseline_change_detection(t1_path, t2_path, tmp_path / "prob.tif", tmp_path / "mask.tif")


def test_unknown_threshold_method_raises(tmp_path):
    rng = np.random.default_rng(3)
    arr = rng.integers(500, 2000, size=(4, 10, 10))
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_stack(t1_path, arr)
    _write_stack(t2_path, arr)

    with pytest.raises(ValueError):
        run_baseline_change_detection(
            t1_path, t2_path, tmp_path / "prob.tif", tmp_path / "mask.tif",
            threshold_method="not_a_real_method",
        )
