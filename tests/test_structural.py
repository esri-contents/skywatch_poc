import numpy as np
import pytest

from src.change_detection.structural import ssim_change


def test_identical_images_near_zero_change():
    rng = np.random.default_rng(0)
    gray = rng.integers(0, 255, size=(40, 40)).astype(np.float32)
    result = ssim_change(gray, gray, win_size=7)
    assert np.allclose(result, 0, atol=1e-5)


def test_shape_mismatch_raises():
    with pytest.raises(ValueError):
        ssim_change(np.zeros((10, 10)), np.zeros((5, 5)))


def test_output_bounded_0_1():
    rng = np.random.default_rng(2)
    g1 = rng.integers(0, 255, size=(40, 40)).astype(np.float32)
    g2 = rng.integers(0, 255, size=(40, 40)).astype(np.float32)
    result = ssim_change(g1, g2, win_size=7)
    assert result.min() >= 0
    assert result.max() <= 1.0
