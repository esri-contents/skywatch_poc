import numpy as np

from src.change_detection.thresholding import compute_otsu_threshold


def test_bimodal_distribution_separates_correctly():
    rng = np.random.default_rng(0)
    background = rng.normal(0.1, 0.02, size=(50, 50))
    change = rng.normal(0.8, 0.02, size=(50, 50))
    prob = np.where(np.arange(2500).reshape(50, 50) % 2 == 0, background, change)
    threshold = compute_otsu_threshold(prob)
    assert 0.1 < threshold < 0.8


def test_respects_valid_mask():
    prob = np.zeros((10, 10))
    prob[:5, :] = 0.9  # AOI 바깥이라고 가정 - 제외되어야 함
    prob[5:, :] = 0.2
    valid_mask = np.zeros((10, 10), dtype=bool)
    valid_mask[5:, :] = True
    threshold = compute_otsu_threshold(prob, valid_mask=valid_mask, fallback=0.5)
    # 유효 영역이 전부 0.2 단일값이므로 fallback을 타야 한다.
    assert threshold == 0.5


def test_empty_valid_mask_uses_fallback():
    prob = np.random.default_rng(1).random((10, 10))
    valid_mask = np.zeros((10, 10), dtype=bool)
    threshold = compute_otsu_threshold(prob, valid_mask=valid_mask, fallback=0.42)
    assert threshold == 0.42


def test_single_value_uses_fallback():
    prob = np.full((10, 10), 0.5)
    threshold = compute_otsu_threshold(prob, fallback=0.33)
    assert threshold == 0.33
