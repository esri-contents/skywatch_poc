import numpy as np
import pytest

from src.change_detection.spectral import pixel_diff


def test_identical_images_zero_change():
    arr = np.random.default_rng(0).integers(0, 1000, size=(4, 20, 20)).astype(np.uint16)
    result = pixel_diff(arr, arr)
    assert np.allclose(result, 0)


def test_different_images_nonzero_change():
    t1 = np.zeros((4, 10, 10), dtype=np.uint16)
    t2 = np.zeros((4, 10, 10), dtype=np.uint16)
    t2[:, 5, 5] = 500
    result = pixel_diff(t1, t2)
    assert result[5, 5] == pytest.approx(1.0)
    assert result.sum() == pytest.approx(1.0)


def test_shape_mismatch_raises():
    t1 = np.zeros((4, 10, 10))
    t2 = np.zeros((4, 5, 5))
    with pytest.raises(ValueError):
        pixel_diff(t1, t2)


def test_output_bounded_0_1():
    rng = np.random.default_rng(1)
    t1 = rng.integers(0, 10000, size=(4, 30, 30)).astype(np.float32)
    t2 = rng.integers(0, 10000, size=(4, 30, 30)).astype(np.float32)
    result = pixel_diff(t1, t2)
    assert result.min() >= 0
    assert result.max() <= 1.0 + 1e-6
