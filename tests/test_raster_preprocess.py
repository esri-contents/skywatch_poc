from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling
from shapely.geometry import Polygon, box

from src.preprocessing.raster_preprocess import align_imagery_pair


def _write_tif(
    path: Path,
    arr: np.ndarray,
    transform,
    crs: str,
    nodata=0,
    descriptions=None,
) -> None:
    profile = {
        "driver": "GTiff", "dtype": arr.dtype.name, "count": arr.shape[0],
        "height": arr.shape[1], "width": arr.shape[2],
        "crs": crs, "transform": transform, "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)
        if descriptions:
            dst.descriptions = descriptions


def _write_aoi(path: Path, bounds, crs="EPSG:5186") -> None:
    minx, miny, maxx, maxy = bounds
    gdf = gpd.GeoDataFrame({"geometry": [box(minx, miny, maxx, maxy)]}, crs=crs)
    gdf.to_file(path, driver="GeoJSON")


def test_align_handles_crs_mismatch(tmp_path):
    # T1: EPSG:5186, 1m 해상도. T2: EPSG:4326(위경도)로 별도 생성 - 서로 다른 CRS.
    rng = np.random.default_rng(0)
    t1_arr = rng.integers(0, 255, size=(3, 30, 30)).astype("uint8")
    t1_transform = from_origin(1000, 1030, 1, 1)
    t1_path = tmp_path / "t1.tif"
    _write_tif(t1_path, t1_arr, t1_transform, "EPSG:5186")

    # T2를 EPSG:5186에서 만든 뒤 gdalwarp 없이 그대로 다른 CRS로 "선언"할 수는
    # 없으므로, 대신 실제로 다른 native CRS(EPSG:4326, 매우 작은 픽셀 크기)로 만든다.
    t2_arr = rng.integers(0, 255, size=(3, 30, 30)).astype("uint8")
    t2_transform = from_origin(127.05, 37.05, 0.00001, 0.00001)
    t2_path = tmp_path / "t2.tif"
    _write_tif(t2_path, t2_arr, t2_transform, "EPSG:4326")

    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (1000, 1000, 1020, 1020), crs="EPSG:5186")

    out_t1 = tmp_path / "t1_aligned.tif"
    out_t2 = tmp_path / "t2_aligned.tif"
    grid = align_imagery_pair(t1_path, t2_path, aoi_path, out_t1, out_t2, dst_crs="EPSG:5186", resolution=1.0)

    with rasterio.open(out_t1) as s1, rasterio.open(out_t2) as s2:
        assert s1.crs == s2.crs == rasterio.crs.CRS.from_epsg(5186)
        assert s1.shape == s2.shape
        assert s1.transform == s2.transform
    assert grid["crs"] == "EPSG:5186"


def test_align_handles_resolution_mismatch_picks_coarser(tmp_path):
    rng = np.random.default_rng(1)
    t1_arr = rng.integers(0, 255, size=(3, 40, 40)).astype("uint8")
    t1_transform = from_origin(1000, 1040, 1, 1)  # 1m
    t1_path = tmp_path / "t1.tif"
    _write_tif(t1_path, t1_arr, t1_transform, "EPSG:5186")

    t2_arr = rng.integers(0, 255, size=(3, 10, 10)).astype("uint8")
    t2_transform = from_origin(1000, 1040, 4, 4)  # 4m (더 낮은 해상도)
    t2_path = tmp_path / "t2.tif"
    _write_tif(t2_path, t2_arr, t2_transform, "EPSG:5186")

    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (1000, 1000, 1040, 1040), crs="EPSG:5186")

    out_t1 = tmp_path / "t1_aligned.tif"
    out_t2 = tmp_path / "t2_aligned.tif"
    grid = align_imagery_pair(t1_path, t2_path, aoi_path, out_t1, out_t2, dst_crs="EPSG:5186")

    # resolution 미지정 -> 더 낮은(coarser) 해상도인 4m을 써야 한다.
    assert grid["resolution_m"] == 4.0
    with rasterio.open(out_t1) as s1, rasterio.open(out_t2) as s2:
        assert s1.shape == s2.shape


def test_align_handles_extent_mismatch(tmp_path):
    rng = np.random.default_rng(2)
    # T1은 AOI보다 넓은 범위, T2는 AOI와 딱 맞는 범위 - extent가 서로 다름.
    t1_arr = rng.integers(0, 255, size=(3, 100, 100)).astype("uint8")
    t1_transform = from_origin(900, 1100, 2, 2)
    t1_path = tmp_path / "t1.tif"
    _write_tif(t1_path, t1_arr, t1_transform, "EPSG:5186")

    t2_arr = rng.integers(0, 255, size=(3, 20, 20)).astype("uint8")
    t2_transform = from_origin(1000, 1020, 1, 1)
    t2_path = tmp_path / "t2.tif"
    _write_tif(t2_path, t2_arr, t2_transform, "EPSG:5186")

    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (1000, 1000, 1020, 1020), crs="EPSG:5186")

    out_t1 = tmp_path / "t1_aligned.tif"
    out_t2 = tmp_path / "t2_aligned.tif"
    grid = align_imagery_pair(t1_path, t2_path, aoi_path, out_t1, out_t2, dst_crs="EPSG:5186", resolution=1.0)

    with rasterio.open(out_t1) as s1, rasterio.open(out_t2) as s2:
        assert s1.bounds == s2.bounds
        assert s1.shape == s2.shape


def test_align_masks_outside_aoi_as_nodata(tmp_path):
    # 정렬 grid는 AOI의 bounding box로 만들어지므로, 사각형 AOI로는 bbox
    # 안쪽과 AOI 안쪽이 항상 같아 마스킹 효과를 검증할 수 없다. AOI가 bbox
    # 전체를 덮지 않는(삼각형) 경우로 실제 마스킹을 검증한다.
    arr = np.full((3, 20, 20), 100, dtype="uint8")
    transform = from_origin(1000, 1020, 1, 1)
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_tif(t1_path, arr, transform, "EPSG:5186", nodata=0)
    _write_tif(t2_path, arr, transform, "EPSG:5186", nodata=0)

    # bbox는 (1000,1000)-(1020,1020)이지만 실제 AOI는 우측 하단 삼각형뿐이다.
    aoi_path = tmp_path / "aoi.geojson"
    triangle = Polygon([(1000, 1000), (1020, 1000), (1020, 1020)])
    gpd.GeoDataFrame({"geometry": [triangle]}, crs="EPSG:5186").to_file(aoi_path, driver="GeoJSON")

    out_t1 = tmp_path / "t1_aligned.tif"
    out_t2 = tmp_path / "t2_aligned.tif"
    align_imagery_pair(t1_path, t2_path, aoi_path, out_t1, out_t2, dst_crs="EPSG:5186", resolution=1.0)

    with rasterio.open(out_t1) as s1:
        data = s1.read()
    # 좌상단 모서리(삼각형 바깥, bbox 안)는 nodata(0)여야 한다.
    assert data[0, 0, 0] == 0
    # 우하단 모서리(삼각형 안)는 원본 값(100)이 남아 있어야 한다.
    assert data[0, -1, -1] == 100


def test_align_preserves_band_descriptions(tmp_path):
    arr = np.zeros((3, 10, 10), dtype="uint8")
    transform = from_origin(1000, 1010, 1, 1)
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_tif(t1_path, arr, transform, "EPSG:5186", descriptions=("red", "green", "blue"))
    _write_tif(t2_path, arr, transform, "EPSG:5186", descriptions=("red", "green", "blue"))

    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (1000, 1000, 1010, 1010), crs="EPSG:5186")

    out_t1 = tmp_path / "t1_aligned.tif"
    out_t2 = tmp_path / "t2_aligned.tif"
    align_imagery_pair(t1_path, t2_path, aoi_path, out_t1, out_t2, dst_crs="EPSG:5186", resolution=1.0)

    with rasterio.open(out_t1) as s1:
        assert list(s1.descriptions) == ["red", "green", "blue"]


def test_align_uses_nearest_when_requested_for_masks(tmp_path):
    arr = np.zeros((1, 10, 10), dtype="uint8")
    arr[0, :5, :] = 1
    transform = from_origin(1000, 1010, 1, 1)
    t1_path = tmp_path / "t1.tif"
    t2_path = tmp_path / "t2.tif"
    _write_tif(t1_path, arr, transform, "EPSG:5186")
    _write_tif(t2_path, arr, transform, "EPSG:5186")

    aoi_path = tmp_path / "aoi.geojson"
    _write_aoi(aoi_path, (1000, 1000, 1010, 1010), crs="EPSG:5186")

    out_t1 = tmp_path / "t1_aligned.tif"
    out_t2 = tmp_path / "t2_aligned.tif"
    align_imagery_pair(
        t1_path, t2_path, aoi_path, out_t1, out_t2,
        dst_crs="EPSG:5186", resolution=1.0, resampling=Resampling.nearest,
    )
    with rasterio.open(out_t1) as s1:
        data = s1.read(1)
    assert set(np.unique(data)) <= {0, 1}
