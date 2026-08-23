import numpy as np

from src.preprocessing.alignment import verify_alignment


def test_identical_images_zero_displacement():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, size=(80, 80)).astype(np.float32)
    result = verify_alignment(img, img, pixel_size_m=10.0)
    assert result["converged"]
    assert result["displacement_px"] < 0.05
    assert result["ecc_score"] > 0.99


def test_known_translation_is_recovered():
    rng = np.random.default_rng(1)
    base = rng.integers(0, 255, size=(120, 120)).astype(np.float32)
    # Shift by a known 3px in x by cropping - simulates a small registration offset.
    shift_px = 3
    img1 = base[:100, :100]
    img2 = base[:100, shift_px:100 + shift_px]
    result = verify_alignment(img1, img2, pixel_size_m=10.0)
    assert result["converged"]
    # ECC's sign convention depends on which image is "template" vs "input",
    # so check the magnitude of the recovered shift rather than its sign.
    assert abs(abs(result["dx_px"]) - shift_px) < 1.0
