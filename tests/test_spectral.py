import numpy as np
import pytest

from src.change_detection.spectral import pixel_diff, robust_cva


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


def test_robust_cva_identical_images_zero_change():
    arr = np.random.default_rng(0).integers(0, 1000, size=(4, 20, 20)).astype(np.uint16)
    result = robust_cva(arr, arr)
    assert np.allclose(result, 0)


def test_robust_cva_shape_mismatch_raises():
    t1 = np.zeros((4, 10, 10))
    t2 = np.zeros((4, 5, 5))
    with pytest.raises(ValueError):
        robust_cva(t1, t2)


def test_robust_cva_output_bounded_0_1():
    rng = np.random.default_rng(1)
    t1 = rng.integers(0, 10000, size=(4, 30, 30)).astype(np.float32)
    t2 = rng.integers(0, 10000, size=(4, 30, 30)).astype(np.float32)
    result = robust_cva(t1, t2)
    assert result.min() >= 0
    assert result.max() <= 1.0 + 1e-6


def test_robust_cva_resists_single_extreme_pixel():
    """pixel_diff는 극단 픽셀 하나가 전역 max를 눌러 나머지 변화를 뭉갠다.

    robust_cva는 median/MAD 표준화 + percentile 정규화 덕분에, 넓게 퍼진
    '진짜' 변화 영역이 극단값 하나 때문에 거의 0으로 압축되지 않아야 한다.
    """
    rng = np.random.default_rng(2)
    t1 = rng.integers(0, 100, size=(4, 40, 40)).astype(np.float32)
    t2 = t1.copy()
    # 넓은 영역(10x10)에 중간 크기 변화
    t2[:, 5:15, 5:15] += 40
    # 단일 픽셀에 극단적인 변화(구름 잔여물 등 흉내)
    t2[:, 35, 35] += 5000

    pd_result = pixel_diff(t1, t2)
    cva_result = robust_cva(t1, t2)

    region_pd = pd_result[5:15, 5:15].mean()
    region_cva = cva_result[5:15, 5:15].mean()

    # robust_cva가 넓은 변화 영역에 pixel_diff보다 훨씬 높은 상대 점수를 준다.
    assert region_cva > region_pd
    assert region_cva > 0.1


def test_robust_cva_mad_near_zero_falls_back_to_std():
    """대부분 픽셀의 차분이 동일(MAD=0)해도 나눗셈 에러 없이 동작해야 한다."""
    t1 = np.zeros((2, 10, 10), dtype=np.float32)
    t2 = np.zeros((2, 10, 10), dtype=np.float32)
    t2[:] = 10.0  # 전체 균일한 차분 -> MAD == 0
    t2[:, 0, 0] = 500.0  # 소수의 예외
    result = robust_cva(t1, t2)
    assert np.all(np.isfinite(result))
    assert result.min() >= 0
    assert result.max() <= 1.0 + 1e-6
