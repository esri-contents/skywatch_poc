"""arcpy_pipeline.change_detect의 numpy 재구현이 Baseline(skimage) 알고리즘과
동일한 정의를 계산하는지 독립 참조 구현으로 검증한다.

ArcGIS Pro 파이썬 환경에는 skimage/OpenCV가 없어 change_detect.py가
SSIM/Otsu/edge_texture를 numpy로 직접 재구현했다 - 이 테스트는 그 재구현이
"같은 답을 다른 방법으로" 낸다는 것을 증명하는 것이지, "빠르다"를 증명하는
게 아니다. 각 알고리즘의 정의를 그대로 따르는 순수 파이썬 루프/전수탐색과
비교한다 (벡터화 버그는 대개 경계조건에서 나므로, 벡터화 결과와 loop 결과가
독립적으로 일치해야 신뢰할 수 있다).
"""

import numpy as np
import pytest

from src.arcpy_pipeline.change_detect import compute_otsu_threshold, robust_cva, ssim_change


def _naive_ssim_change(g1, g2, win=7):
    g1 = g1.astype(np.float64)
    g2 = g2.astype(np.float64)
    data_range = float(max(g1.max(), g2.max()) - min(g1.min(), g2.min())) or 1.0
    c1, c2 = (0.01 * data_range) ** 2, (0.03 * data_range) ** 2
    h, w = g1.shape
    r = win // 2
    out = np.zeros((h, w))
    for y in range(h):
        for x in range(w):
            yi = [_reflect(i, h) for i in range(y - r, y + r + 1)]
            xi = [_reflect(j, w) for j in range(x - r, x + r + 1)]
            a = g1[np.ix_(yi, xi)].ravel()
            b = g2[np.ix_(yi, xi)].ravel()
            mu1, mu2 = a.mean(), b.mean()
            v1, v2 = a.var(ddof=1), b.var(ddof=1)
            cov = ((a - mu1) * (b - mu2)).sum() / (a.size - 1)
            num = (2 * mu1 * mu2 + c1) * (2 * cov + c2)
            den = (mu1**2 + mu2**2 + c1) * (v1 + v2 + c2)
            out[y, x] = 1.0 - (num / den if den > 0 else 1.0)
    return np.clip(out, 0, 1)


def _reflect(i, n):
    # uniform_filter의 기본 경계처리(mode="reflect")와 동일한 인덱스 반사.
    if i < 0:
        return -i - 1
    if i >= n:
        return 2 * n - i - 1
    return i


def _naive_otsu(values, nbins=256):
    hist, edges = np.histogram(values, bins=nbins)
    centers = (edges[:-1] + edges[1:]) / 2
    total = hist.sum()
    best_t, best_var = centers[0], -1.0
    for i in range(1, nbins):
        w1, w2 = hist[:i].sum(), total - hist[:i].sum()
        if w1 == 0 or w2 == 0:
            continue
        m1 = (hist[:i] * centers[:i]).sum() / w1
        m2 = (hist[i:] * centers[i:]).sum() / w2
        var = w1 * w2 * (m1 - m2) ** 2
        if var > best_var:
            best_var, best_t = var, centers[i - 1]
    return float(best_t)


def _between_class_variance(values, t, nbins=256):
    hist, edges = np.histogram(values, bins=nbins)
    centers = (edges[:-1] + edges[1:]) / 2
    lo = centers <= t
    w1, w2 = hist[lo].sum(), hist[~lo].sum()
    if w1 == 0 or w2 == 0:
        return -1.0
    m1 = (hist[lo] * centers[lo]).sum() / w1
    m2 = (hist[~lo] * centers[~lo]).sum() / w2
    return float(w1 * w2 * (m1 - m2) ** 2)


@pytest.fixture
def rng():
    return np.random.default_rng(42)


def test_ssim_matches_naive_pixelwise_loop(rng):
    a = rng.normal(1000, 200, (24, 26))
    b = a.copy()
    b[8:16, 10:18] += 900
    vectorized = ssim_change(a, b)
    naive = _naive_ssim_change(a, b)
    assert float(np.abs(vectorized - naive).max()) < 1e-6


def test_ssim_identical_images_is_zero(rng):
    a = rng.normal(1000, 200, (20, 20))
    assert float(np.abs(ssim_change(a, a)).max()) < 1e-9


def test_ssim_change_region_scores_higher_than_background(rng):
    a = rng.normal(1000, 200, (24, 26))
    b = a.copy()
    b[8:16, 10:18] += 900
    s = ssim_change(a, b)
    assert s[8:16, 10:18].mean() > s[0:6, 0:6].mean() * 5


@pytest.mark.parametrize("trial", range(5))
def test_otsu_achieves_same_between_class_variance_as_exhaustive_search(trial, rng):
    values = np.concatenate([rng.normal(0.2, 0.05, 4000), rng.normal(0.8, 0.07, 2500)])
    values = np.clip(values, 0, 1)
    mine = compute_otsu_threshold(values.reshape(-1, 1))
    ref = _naive_otsu(values)
    # 이봉분포에서는 두 봉우리 사이 빈 구간의 모든 분할이 동일한 클래스간
    # 분산을 낸다 - 인덱스가 아니라 "달성한 분산"이 같은지 비교해야 한다.
    v_mine = _between_class_variance(values, mine)
    v_ref = _between_class_variance(values, ref)
    assert abs(v_mine - v_ref) <= abs(v_ref) * 1e-9


def test_otsu_is_deterministic_across_repeated_calls():
    rng = np.random.default_rng(7)
    values = np.concatenate([rng.normal(0.2, 0.05, 4000), rng.normal(0.8, 0.07, 2500)])
    values = np.clip(values, 0, 1).reshape(-1, 1)
    results = {compute_otsu_threshold(values) for _ in range(5)}
    assert len(results) == 1  # 동률(plateau) 처리가 결정론적이어야 재현성이 보장된다


def test_otsu_falls_back_on_constant_input():
    assert compute_otsu_threshold(np.full((5, 5), 0.3), fallback=0.42) == 0.42


def test_otsu_threshold_lands_between_bimodal_peaks():
    values = np.concatenate([np.full(500, 0.1), np.full(500, 0.9)])
    t = compute_otsu_threshold(values.reshape(-1, 1))
    assert 0.1 < t < 0.9


def test_robust_cva_range_is_0_to_1(rng):
    t1 = rng.normal(3000, 300, (4, 30, 30)).astype(np.float32)
    t2 = t1.copy()
    t2[:, 5:12, 5:12] += 2500
    s = robust_cva(t1, t2)
    assert float(s.min()) >= 0 and float(s.max()) <= 1


def test_robust_cva_change_region_scores_higher(rng):
    t1 = rng.normal(3000, 300, (4, 30, 30)).astype(np.float32)
    t2 = t1.copy()
    t2[:, 5:12, 5:12] += 2500
    s = robust_cva(t1, t2)
    assert s[5:12, 5:12].mean() > s[20:28, 20:28].mean() * 3


def test_robust_cva_identical_input_is_near_zero(rng):
    t1 = rng.normal(3000, 300, (4, 30, 30)).astype(np.float32)
    assert float(robust_cva(t1, t1).max()) < 1e-6


def test_robust_cva_tolerates_nodata_nan(rng):
    t1 = rng.normal(3000, 300, (4, 30, 30)).astype(np.float32)
    t2 = t1.copy()
    t2[:, 5:12, 5:12] += 2500
    t2[:, 0:3, 0:3] = np.nan
    s = robust_cva(t1, t2)
    assert np.isfinite(s[10:20, 10:20]).all()
