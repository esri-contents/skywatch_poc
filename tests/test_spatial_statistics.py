import geopandas as gpd
import numpy as np
from shapely.geometry import Point

from src.evaluation.spatial_statistics import compute_gi_star, compute_global_moran


def _clustered_gdf(n_per_cluster: int = 15, seed: int = 0) -> gpd.GeoDataFrame:
    """두 개의 뚜렷한 공간 군집(값 높음 vs 낮음)을 가진 데이터 - Moran's I가
    유의한 양의 자기상관을 보여야 한다."""
    rng = np.random.default_rng(seed)
    high_pts = rng.normal(loc=(0, 0), scale=5, size=(n_per_cluster, 2))
    low_pts = rng.normal(loc=(1000, 1000), scale=5, size=(n_per_cluster, 2))
    geoms = [Point(x, y) for x, y in np.vstack([high_pts, low_pts])]
    values = [0.9] * n_per_cluster + [0.1] * n_per_cluster
    return gpd.GeoDataFrame({"priority_score": values}, geometry=geoms)


def _random_gdf(n: int = 30, seed: int = 0) -> gpd.GeoDataFrame:
    rng = np.random.default_rng(seed)
    geoms = [Point(x, y) for x, y in rng.random((n, 2)) * 1000]
    values = rng.random(n)
    return gpd.GeoDataFrame({"priority_score": values}, geometry=geoms)


def test_clustered_data_has_significant_positive_moran():
    gdf = _clustered_gdf()
    result = compute_global_moran(gdf, k=5, permutations=199, seed=42)
    assert result["I"] > 0
    assert result["p_sim"] < 0.05


def test_small_sample_returns_none_without_raising():
    gdf = _random_gdf(n=3)
    result = compute_global_moran(gdf, k=8, permutations=99, seed=42)
    assert result["I"] is None
    assert result["n"] == 3


def test_gi_star_flags_hotspot_cluster():
    gdf = _clustered_gdf()
    out = compute_gi_star(gdf, k=5, permutations=199, seed=42)
    assert "gi_class" in out.columns
    high_cluster = out.iloc[:15]
    assert (high_cluster["gi_class"].astype(str).str.startswith("HOT")).any()


def test_gi_star_small_sample_returns_none_columns():
    gdf = _random_gdf(n=3)
    out = compute_gi_star(gdf, k=8, permutations=99, seed=42)
    assert out["gi_class"].isna().all()
    assert len(out) == 3
