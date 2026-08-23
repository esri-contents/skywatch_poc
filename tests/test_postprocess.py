import numpy as np

from src.change_detection.postprocess import clean_mask


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
