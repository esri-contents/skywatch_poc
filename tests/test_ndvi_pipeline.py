"""src/ndvi 테스트.

TEST DATA / SYNTHETIC DATA: 모든 입력은 tmp_path의 합성 래스터/필지/철도다. 네트워크(VWorld/STAC)를 호출하지 않는다.
실제 단양 결과 검증은 실제 산출물을 읽는 test_real_outputs_* (산출물이 있을 때만)에서 한다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
import yaml
from rasterio.transform import Affine, from_origin
from shapely.geometry import LineString, Polygon, box
from shapely.ops import transform as shp_transform
from pyproj import Transformer

from src.ndvi import classification as clf
from src.ndvi import parcel_statistics as ps
from src.ndvi import scoring, temporal
from src.ndvi.cadastre import jibun_from_pnu, normalize_parcels, parse_jibun
from src.ndvi.processing import (build_ndvi_stack, discover_scenes, ndvi_from_reflectance, write_raster)

REAL_YAML = Path("config/projects/kr_rail_danyang.yaml")
YEARS = [2016, 2018, 2020, 2022, 2024, 2026]
TH = {"before_min": 0.35, "after_max": 0.20, "delta_max": -0.20, "recovery_ndvi": 0.35}


# ------------------------------------------------------------------ 합성 데이터
def _write_s2(path: Path, ndvi_blocks: dict, origin=(440000.0, 4096000.0), n=40, order=("B02", "B03", "B04", "B08"),
              nodata_box=None):
    """UTM52N 합성 4밴드 uint16 GeoTIFF. ndvi_blocks: {(r0,r1,c0,c1): ndvi}. 배경 ndvi=0.6."""
    ndvi = np.full((n, n), 0.6, dtype="float64")
    for (r0, r1, c0, c1), v in ndvi_blocks.items():
        ndvi[r0:r1, c0:c1] = v
    red = 1000.0
    nir = red * (1 + ndvi) / (1 - ndvi)
    bands = {"B02": np.full((n, n), 800.0), "B03": np.full((n, n), 900.0), "B04": np.full((n, n), red), "B08": nir}
    arr = np.stack([bands[b] for b in order]).astype("uint16")
    if nodata_box:
        r0, r1, c0, c1 = nodata_box
        arr[:, r0:r1, c0:c1] = 0
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=4, dtype="uint16", crs="EPSG:32652",
                       transform=from_origin(origin[0], origin[1], 10, 10), nodata=0) as dst:
        dst.write(arr)
        dst.descriptions = tuple(order)
    return path


def _utm_box_to_5186(r0, r1, c0, c1, origin=(440000.0, 4096000.0)):
    g = box(origin[0] + c0 * 10, origin[1] - r1 * 10, origin[0] + c1 * 10, origin[1] - r0 * 10)
    t = Transformer.from_crs(32652, 5186, always_xy=True)
    return shp_transform(t.transform, g)


@pytest.fixture()
def synth(tmp_path):
    """3개 필지: A 지속 감소(2018 이후), B 안정 식생, C 일시 감소(2020만)."""
    img = tmp_path / "s2"
    img.mkdir()
    regions = {"A": (2, 14, 2, 14), "B": (2, 14, 22, 34), "C": (22, 34, 2, 14)}
    series = {  # 연도별 NDVI
        "A": [0.7, 0.7, 0.1, 0.1, 0.1, 0.1],
        "B": [0.7, 0.7, 0.7, 0.7, 0.7, 0.7],
        "C": [0.7, 0.7, 0.1, 0.7, 0.7, 0.7],
    }
    dates = ["2016-05-08", "2018-04-28", "2020-04-29", "2022-05-04", "2024-05-03", "2026-05-06"]
    for i, y in enumerate(YEARS):
        blocks = {regions[k]: series[k][i] for k in regions}
        _write_s2(img / f"s2_{y}_{dates[i]}.tif", blocks, order=("B02", "B03", "B04", "B08"))
    parcels = gpd.GeoDataFrame({
        "pnu": ["0123456789012345001", "0123456789012345002", "0123456789012345003"],
        "jibun": ["1-1 전", "산 2 임", "3 대"], "addr": ["a", "b", "c"], "id": ["x1", "x2", "x3"],
        "emd_nm": ["매포읍"] * 3, "li_nm": ["매포리"] * 3,
        "gosi_year": ["2025"] * 3, "gosi_month": ["01"] * 3, "jiga": ["1", "2", "3"],
    }, geometry=[_utm_box_to_5186(*regions[k]) for k in "ABC"], crs=5186)
    cad = tmp_path / "cad.gpkg"
    parcels.to_file(cad, driver="GPKG")
    aoi = gpd.GeoDataFrame(geometry=[box(*parcels.total_bounds).buffer(60)], crs=5186)
    aoi_p = tmp_path / "aoi.geojson"
    aoi.to_file(aoi_p, driver="GeoJSON")
    rail_geom = LineString([(parcels.total_bounds[0] - 30, parcels.total_bounds[1] + 150),
                            (parcels.total_bounds[2] + 30, parcels.total_bounds[1] + 150)])
    rail = gpd.GeoDataFrame({"railway": ["rail"], "tunnel": [None]}, geometry=[rail_geom], crs=5186).to_crs(4326)
    rail_p = tmp_path / "rail.geojson"
    rail.to_file(rail_p, driver="GeoJSON")

    cfg = yaml.safe_load(REAL_YAML.read_text(encoding="utf-8"))
    cfg["paths"].update(aoi=str(aoi_p), railway=str(rail_p), imagery_dir=str(img))
    cfg["ndvi"]["output_dir"] = str(tmp_path / "out")
    cfg["ndvi"]["scene_selection"] = None
    cfg["ndvi"]["cadastre"]["cache_dir"] = str(tmp_path / "cadcache")
    cfg["ndvi"]["min_valid_obs_pixel"] = 4
    cfg["ndvi"]["parcel_quality"]["min_effective_pixels"] = 4
    proj = tmp_path / "proj.yaml"
    proj.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return {"proj": proj, "cad": cad, "cfg": cfg, "tmp": tmp_path, "img": img, "parcels": parcels, "aoi": aoi}


# ------------------------------------------------------------------ 1~3: 수식/밴드/NoData
def test_ndvi_formula_and_range():
    red = np.array([0.1, 0.2, 0.3])
    nir = np.array([0.5, 0.2, 0.1])
    out = ndvi_from_reflectance(red, nir)
    np.testing.assert_allclose(out, [(0.5 - 0.1) / 0.6, 0.0, (0.1 - 0.3) / 0.4], rtol=1e-6)
    assert np.all((out >= -1) & (out <= 1))


def test_ndvi_zero_denominator_and_nodata_are_nan_not_zero():
    red = np.array([0.0, 0.2, np.nan])
    nir = np.array([0.0, 0.4, 0.4])
    out = ndvi_from_reflectance(red, nir)
    assert np.isnan(out[0]) and np.isnan(out[2])          # 0/0, NaN -> NaN (0으로 대체하지 않음)
    assert out[1] == pytest.approx(1 / 3, rel=1e-5)
    valid = np.array([True, False, True])
    assert np.isnan(ndvi_from_reflectance(np.array([0.1, 0.1, 0.1]), np.array([0.5, 0.5, 0.5]), valid)[1])


def test_red_nir_band_mapping_uses_descriptions_not_position(tmp_path):
    p = tmp_path / "s2_2016_2016-05-08.tif"
    _write_s2(p, {}, order=("B08", "B04", "B03", "B02"))   # 순서를 뒤섞음
    sc = discover_scenes(tmp_path, [2016], None, ["B02", "B03", "B04", "B08"])[0]
    assert sc.band_order == ["B08", "B04", "B03", "B02"]
    assert (sc.nir_idx, sc.red_idx) == (0, 1)
    st = build_ndvi_stack([sc])
    assert np.nanmedian(st.ndvi[0]) == pytest.approx(0.6, abs=0.01)   # 배경 NDVI 0.6이 복원되어야 함


def test_nodata_pixels_stay_nan(tmp_path):
    p = tmp_path / "s2_2016_2016-05-08.tif"
    _write_s2(p, {}, nodata_box=(10, 20, 10, 20))
    sc = discover_scenes(tmp_path, [2016], None, ["B02", "B03", "B04", "B08"])
    st = build_ndvi_stack(sc)
    frac_nan_inside = np.isnan(st.ndvi[0]).sum()
    assert frac_nan_inside >= 80                               # 10x10 마스크 영역(재투영 후 경계 약간 차이)
    assert not np.any(st.ndvi[0] == 0.0)                       # 0으로 채우지 않음
    assert st.qa["per_year"][2016]["valid_fraction_of_grid"] < 1.0


# ------------------------------------------------------------------ 4~5: grid / CRS
def test_time_series_grid_alignment_and_crs(synth):
    sc = discover_scenes(synth["img"], YEARS, None, ["B02", "B03", "B04", "B08"])
    st = build_ndvi_stack(sc)
    assert st.ndvi.shape[0] == 6
    assert st.crs.to_epsg() == 5186
    assert st.qa["native_grid"]["identical_native_grid"] is True
    assert abs(st.transform.a) == 10 and abs(st.transform.e) == 10
    assert st.transform.c % 10 == 0 and st.transform.f % 10 == 0     # 10m 격자에 스냅
    # 모든 연도에서 동일 픽셀 위치에 같은 구조가 있어야 한다(정합): 안정 필지 B 영역 NDVI는 연도 간 일정
    B = _utm_box_to_5186(2, 14, 22, 34).centroid
    r, c = rasterio.transform.rowcol(st.transform, B.x, B.y)
    vals = st.ndvi[:, r, c]
    np.testing.assert_allclose(vals, 0.7, atol=0.01)
    out = write_raster(synth["tmp"] / "x.tif", st.ndvi[0], st.transform)
    with rasterio.open(out) as src:
        assert src.crs.to_epsg() == 5186 and src.nodata == -9999.0


def test_mismatched_native_grids_are_reported(tmp_path):
    _write_s2(tmp_path / "s2_2016_2016-05-08.tif", {})
    _write_s2(tmp_path / "s2_2018_2018-04-28.tif", {}, origin=(440100.0, 4096000.0))
    sc = discover_scenes(tmp_path, [2016, 2018], None, ["B02", "B03", "B04", "B08"])
    assert build_ndvi_stack(sc).qa["native_grid"]["identical_native_grid"] is False


# ------------------------------------------------------------------ 6~7: PNU / geometry
def test_pnu_string_preserved_and_jibun_parsed():
    assert parse_jibun("산38-1임") == {"is_mountain": True, "bonbun_no": 38, "bubun_no": 1, "jimok_abbr": "임",
                                       "jibun_norm": "산 38-1"}
    assert parse_jibun("202-14 도")["jibun_norm"] == "202-14"
    assert jibun_from_pnu("0123456789012345001") == "1234-5001"
    assert jibun_from_pnu(None) is None and jibun_from_pnu(float("nan")) is None
    assert jibun_from_pnu("4380025321200170026") == "산 17-26"


def test_normalize_keeps_leading_zero_pnu_no_fabrication(synth):
    raw = synth["parcels"].copy()
    raw.loc[1, "pnu"] = None                                   # PNU 없는 필지
    gdf, clipped, qa = normalize_parcels(raw, synth["aoi"])
    assert gdf["pnu"].iloc[0] == "0123456789012345001" and isinstance(gdf["pnu"].iloc[0], str)
    assert gdf["pnu"].isna().sum() == 1                        # 임의 PNU를 만들지 않음
    assert qa["pnu_missing"] == 1
    assert gdf["parcel_uid"].notna().all()


def test_invalid_geometry_is_repaired_and_duplicates_removed(synth):
    bow = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])        # 나비넥타이(invalid)
    assert not bow.is_valid
    c = synth["parcels"].total_bounds
    shift = Polygon([(c[0] + p[0] * 5, c[1] + p[1] * 5) for p in bow.exterior.coords])
    raw = synth["parcels"].copy()
    raw.loc[0, "geometry"] = shift
    dup = raw.iloc[[1]].copy()
    raw = pd.concat([raw, dup], ignore_index=True)
    gdf, clipped, qa = normalize_parcels(gpd.GeoDataFrame(raw, crs=5186), synth["aoi"])
    assert qa["invalid_geometry_repaired"] == 1 and qa["duplicate_pnu_removed"] == 1
    assert gdf.geometry.is_valid.all() and clipped.is_valid.all()
    assert gdf.crs.to_epsg() == 5186
    assert (gdf["area_in_aoi_m2"] <= gdf["area_total_m2"] + 1e-6).all()


# ------------------------------------------------------------------ 8~10: 통계/경계/면적
def _grid(h=4, w=4):
    return from_origin(0.0, float(h * 10), 10.0, 10.0), (h, w)


def test_area_weighted_mean_and_median():
    tr, shape = _grid()
    arr = np.arange(16, dtype="float32").reshape(4, 4) / 20.0
    geom = box(0, 20, 20, 40)                                  # 좌상단 2x2 픽셀 전체
    wt = ps.pixel_weights([geom], tr, shape)
    assert wt["a"].sum() == pytest.approx(geom.area)
    st = ps.year_stats(wt, 1, arr.ravel(), "y")
    vals = np.array([arr[0, 0], arr[0, 1], arr[1, 0], arr[1, 1]])
    assert st["ndvi_mean_y"].iloc[0] == pytest.approx(vals.mean())
    assert st["ndvi_median_y"].iloc[0] in vals


def test_boundary_pixel_area_is_proportional():
    tr, shape = _grid()
    geom = box(5, 25, 15, 35)                                  # 4개 픽셀에 25m^2씩 걸침
    wt = ps.pixel_weights([geom], tr, shape)
    assert len(wt["a"]) == 4 and np.allclose(wt["a"], 25.0)
    arr = np.full(16, np.nan, dtype="float32")
    arr[[0 * 4 + 0, 0 * 4 + 1, 1 * 4 + 0, 1 * 4 + 1]] = [0.1, 0.3, 0.5, 0.7]
    r = ps.year_stats(wt, 1, arr, "y")
    assert r["ndvi_mean_y"].iloc[0] == pytest.approx(0.4)
    assert r["valid_area_y"].iloc[0] == pytest.approx(100.0)
    # 무효 픽셀은 분모에서 빠져야 하고, 면적을 늘리지 않는다
    arr[1] = np.nan
    r2 = ps.year_stats(wt, 1, arr, "y")
    assert r2["valid_area_y"].iloc[0] == pytest.approx(75.0)
    assert r2["ndvi_mean_y"].iloc[0] == pytest.approx((0.1 + 0.5 + 0.7) / 3)


def test_two_parcels_share_pixel_proportionally():
    tr, shape = _grid()
    a, b = box(0, 30, 3, 40), box(3, 30, 10, 40)               # 한 픽셀을 30:70 분할
    wt = ps.pixel_weights([a, b], tr, shape)
    areas = {i: wt["a"][wt["p"] == i].sum() for i in (0, 1)}
    assert areas[0] == pytest.approx(30.0) and areas[1] == pytest.approx(70.0)
    assert areas[0] + areas[1] == pytest.approx(100.0)


def test_decline_area_never_exceeds_evaluable_area(synth):
    sc = discover_scenes(synth["img"], YEARS, None, ["B02", "B03", "B04", "B08"])
    st = build_ndvi_stack(sc)
    px = temporal.analyze_pixels(st.ndvi, TH, 1, 4)
    geoms = list(synth["parcels"].geometry) + [synth["aoi"].geometry.iloc[0]]
    wt = ps.pixel_weights(geoms, st.transform, st.ndvi.shape[1:])
    d = ps.decline_stats(wt, len(geoms), px, [f"{a}_{b}" for a, b in zip(YEARS, YEARS[1:])], 200)
    assert (d["decline_area_m2"] <= d["evaluable_area_m2"] + 1e-6).all()
    area = np.bincount(wt["p"], weights=wt["a"], minlength=len(geoms))
    assert (d["evaluable_area_m2"] <= area + 1e-6).all()
    s = d["persistent_decline_area_m2"] + d["temporary_decline_area_m2"] + d["unconfirmed_decline_area_m2"]
    np.testing.assert_allclose(s, d["decline_area_m2"])


# ------------------------------------------------------------------ 11~12: 임계값 / 지속·일시
def _series(vals):
    return np.array(vals, dtype="float32").reshape(len(vals), 1, 1)


def test_thresholds_change_classification():
    s = _series([0.50, 0.28, 0.28, 0.28, 0.28, 0.28])          # 0.50 -> 0.28 (Δ -0.22)
    default = temporal.analyze_pixels(s, TH, 1, 4)["any_decline"][0, 0]
    relaxed = temporal.analyze_pixels(s, {**TH, "after_max": 0.30}, 1, 4)["any_decline"][0, 0]
    conservative = temporal.analyze_pixels(s, {**TH, "before_min": 0.55}, 1, 4)["any_decline"][0, 0]
    assert (default, relaxed, conservative) == (False, True, False)


def test_persistent_vs_temporary_vs_unconfirmed():
    cases = {
        "persistent": [0.7, 0.7, 0.1, 0.1, 0.1, 0.1],
        "temporary": [0.7, 0.7, 0.1, 0.7, 0.7, 0.7],
        "unconfirmed": [0.7, 0.7, 0.7, 0.7, 0.7, 0.1],      # 마지막 관측에서만 감소 -> 후속 관측 없음
        "stable": [0.7] * 6,
    }
    code = {k: int(temporal.analyze_pixels(_series(v), TH, 1, 4)["persistence_class"][0, 0]) for k, v in cases.items()}
    assert code == {"persistent": 1, "temporary": 2, "unconfirmed": 3, "stable": 0}
    r = temporal.analyze_pixels(_series(cases["persistent"]), TH, 1, 4)
    assert r["first_interval_idx"][0, 0] == 1                  # 2018->2020 구간에서 최초 관측
    assert r["frequency"][0, 0] == 1


def test_invalid_observation_is_not_treated_as_decline():
    s = _series([0.7, np.nan, 0.7, 0.7, 0.7, 0.7])
    r = temporal.analyze_pixels(s, TH, 1, 4)
    assert not r["any_decline"][0, 0]
    d = temporal.delta_ndvi(s[0], s[1])
    assert np.isnan(d[0, 0])                                   # 0이 아니라 NaN


def test_insufficient_valid_observations_not_evaluable():
    s = _series([0.7, 0.1, np.nan, np.nan, np.nan, 0.1])
    r = temporal.analyze_pixels(s, TH, 1, 4)
    assert not r["evaluable"][0, 0] and not r["any_decline"][0, 0]


# ------------------------------------------------------------------ 13~14: 점수 / 철도 거리
def test_priority_score_formula_and_bounds():
    df = pd.DataFrame({
        "decline_ratio": [0.5, 0.25, 0.0, 1.0], "persistence_status": ["PERSISTENT", "UNCONFIRMED", "NONE", "TEMPORARY"],
        "rail_distance_m": [0.0, 250.0, 800.0, 500.0], "change_type": ["PERSISTENT_DECLINE"] * 4,
        "decline_area_m2": [500.0] * 4, "quality_status": ["VALID"] * 4})
    cfg = yaml.safe_load(REAL_YAML.read_text(encoding="utf-8"))["ndvi"]["priority"]
    out = scoring.priority_scores(df, cfg)
    assert out["priority_score"].iloc[0] == pytest.approx(100.0)
    assert out["priority_score"].iloc[1] == pytest.approx(0.4 * 50 + 0.35 * 50 + 0.25 * 50)
    assert out["priority_score"].iloc[2] == pytest.approx(0.0)
    assert out["score_decline_ratio"].iloc[3] == 100.0         # 상한 클립
    for c in ("score_decline_ratio", "score_persistence", "score_rail_proximity", "priority_score"):
        assert out[c].between(0, 100).all()
    assert out["priority_rank"].iloc[0] == 1


def test_priority_weights_are_configurable():
    df = pd.DataFrame({"decline_ratio": [0.5], "persistence_status": ["NONE"], "rail_distance_m": [0.0],
                       "change_type": ["PERSISTENT_DECLINE"], "decline_area_m2": [500.0], "quality_status": ["VALID"]})
    cfg = yaml.safe_load(REAL_YAML.read_text(encoding="utf-8"))["ndvi"]["priority"]
    cfg = {**cfg, "weights": {"decline_ratio": 1.0, "persistence": 0.0, "rail_proximity": 0.0}}
    assert scoring.priority_scores(df, cfg)["priority_score"].iloc[0] == pytest.approx(100.0)


def test_rail_distance_computation():
    rail = gpd.GeoDataFrame({"railway": ["rail", "abandoned"], "tunnel": [None, None]},
                            geometry=[LineString([(0, 0), (100, 0)]), LineString([(0, 500), (100, 500)])], crs=5186)
    parcels = gpd.GeoSeries([box(10, 50, 20, 60), box(10, 400, 20, 410)], crs=5186)
    d = scoring.rail_distance(parcels, rail[rail["railway"] == "rail"], [100, 300, 500])
    assert d["rail_distance_m"].tolist() == pytest.approx([50.0, 400.0])
    assert d["within_100m"].tolist() == [True, False]
    assert d["within_500m"].tolist() == [True, True]


# ------------------------------------------------------------------ 15: 품질 플래그
def test_quality_flags():
    years = [2016, 2018]
    q = {"min_effective_pixels": 4, "min_evaluable_fraction": 0.8, "min_year_valid_fraction": 0.5, "min_valid_years": 2}
    df = pd.DataFrame({
        "area_in_aoi_m2": [10000.0, 150.0, 10000.0, 10000.0, 10000.0],
        "evaluable_area_m2": [10000.0, 150.0, 5000.0, 10000.0, 0.0],
        "valid_area_2016": [10000.0, 150.0, 10000.0, 10000.0, 0.0],
        "valid_area_2018": [10000.0, 150.0, 10000.0, 3000.0, 0.0],
        "geom_repaired": [False, False, False, False, False], "pnu_missing": [False] * 5, "pnu_len_ok": [True] * 5})
    out = clf.assess_quality(df, years, q, 100.0)
    s = out["quality_status"].tolist()
    assert s == ["VALID", "LOW_PIXEL_COUNT", "LOW_COVERAGE", "CLOUD_CONTAMINATION", "NOT_EVALUABLE"]
    assert pd.isna(out["null_reason"].iloc[0]) or out["null_reason"].iloc[0] is None
    assert out["null_reason"].iloc[4] is not None               # NULL 사유 기록


# ------------------------------------------------------------------ 16~17: 내보내기 / 파이프라인 재실행
def _run(synth, **kw):
    from src.ndvi.pipeline import run
    return run(synth["proj"], offline=True, cadastre=str(synth["cad"]), **kw)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_pipeline_end_to_end_exports_and_types(synth):
    m = _run(synth, force=True)
    assert m["status"] == "SUCCESS", m.get("error")
    assert m["skywatch_api_called"] is False
    out = Path(synth["cfg"]["ndvi"]["output_dir"])
    # 래스터
    for y in YEARS:
        assert (out / "rasters" / f"ndvi_{y}.tif").exists()
    assert (out / "rasters" / "delta_ndvi_2016_2026.tif").exists() and (out / "rasters" / "delta_ndvi_2022_2024.tif").exists()
    # CSV: UTF-8 BOM + PNU 문자열 보존
    raw = (out / "tables" / "parcel_change_summary.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    df = pd.read_csv(out / "tables" / "parcel_change_summary.csv", dtype={"pnu": str}, encoding="utf-8-sig")
    assert set(df["pnu"]) == {"0123456789012345001", "0123456789012345002", "0123456789012345003"}
    types = dict(zip(df["pnu"], df["change_type"]))
    assert types["0123456789012345001"] == "PERSISTENT_DECLINE"
    assert types["0123456789012345002"] == "STABLE_VEGETATION"
    assert types["0123456789012345003"] == "TEMPORARY_DECLINE"
    # GeoPackage / GeoJSON
    g = gpd.read_file(out / "vectors" / "parcel_ndvi_stats.gpkg")
    assert len(g) == 3 and g.crs.to_epsg() == 5186 and g["pnu"].astype(str).str.startswith("0").all()
    gj = gpd.read_file(out / "vectors" / "parcel_change_types.geojson")
    assert gj.crs.to_epsg() == 4326
    pri = pd.read_csv(out / "tables" / "priority_parcels.csv", dtype={"pnu": str}, encoding="utf-8-sig")
    assert pri["pnu"].tolist()[0] == "0123456789012345001" and pri["priority_rank"].tolist()[0] == 1
    # 감소 면적 <= 평가 면적, 검증 지표
    meta = json.loads((out / "metadata" / "analysis_metadata.json").read_text(encoding="utf-8"))
    pv = meta["parcel_validation"]
    assert pv["decline_area_exceeds_evaluable"] == 0 and pv["class_area_sum_mismatch"] == 0
    assert meta["railway"]["kind"] == "OSM_UNOFFICIAL"
    assert "sensitivity" in meta and set(meta["sensitivity"]) == {"default", "conservative", "relaxed"}
    # 보고서(오프라인 단일 HTML)
    html = (out / "report" / "kr_rail_danyang_ndvi_parcel_report.html").read_text(encoding="utf-8")
    assert "탐색적 분석이며" in html and "https://" not in html.replace("http://www.w3.org", "")
    # 매니페스트
    mf = json.loads((out / "metadata" / "run_manifest.json").read_text(encoding="utf-8"))
    assert mf["status"] == "SUCCESS" and mf["steps"]["cadastre"]["status"] == "OK"


def test_pipeline_rerun_is_reproducible_and_skips(synth):
    out = Path(synth["cfg"]["ndvi"]["output_dir"])
    _run(synth, force=True)
    h1 = {n: _sha(out / "tables" / n) for n in ("parcel_change_summary.csv", "priority_parcels.csv", "parcel_ndvi_timeseries.csv")}
    r1 = _sha(out / "rasters" / "ndvi_2026.tif")
    m2 = _run(synth, force=True)                               # 강제 재계산 -> 바이트 단위 동일
    assert m2["status"] == "SUCCESS"
    assert {n: _sha(out / "tables" / n) for n in h1} == h1 and _sha(out / "rasters" / "ndvi_2026.tif") == r1
    m3 = _run(synth)                                           # 입력 동일 -> 분석 재사용
    assert m3["steps"]["analysis"]["status"] == "REUSED_EXISTING_OUTPUTS"
    assert {n: _sha(out / "tables" / n) for n in h1} == h1


def test_pipeline_without_cadastre_does_not_fabricate_parcel_results(synth, tmp_path):
    from src.ndvi.pipeline import run
    m = run(synth["proj"], offline=True, force=True)           # 지적도 캐시 없음 + offline
    out = Path(synth["cfg"]["ndvi"]["output_dir"])
    assert m["status"] == "PARTIAL_NO_CADASTRE", m.get("error")
    assert m["steps"]["parcel_stats"]["status"] == "NOT_PERFORMED"
    assert (out / "rasters" / "ndvi_2016.tif").exists()        # NDVI는 계속 수행
    assert not (out / "vectors" / "parcel_ndvi_stats.gpkg").exists()
    assert not (out / "tables" / "priority_parcels.csv").exists()
    q = pd.read_csv(out / "tables" / "analysis_quality_summary.csv", encoding="utf-8-sig")
    assert "연속지적도" in q["구분"].tolist()


def test_dry_run_writes_nothing(synth):
    m = _run(synth, dry_run=True)
    assert m["status"] == "DRY_RUN"
    assert not Path(synth["cfg"]["ndvi"]["output_dir"]).exists()


# ------------------------------------------------------------------ 실제 산출물 일관성(있을 때만)
REAL_OUT = Path("outputs/kr_rail_danyang/ndvi")


@pytest.mark.skipif(not (REAL_OUT / "metadata" / "analysis_metadata.json").exists(), reason="실제 분석 산출물 없음")
def test_real_outputs_are_consistent():
    meta = json.loads((REAL_OUT / "metadata" / "analysis_metadata.json").read_text(encoding="utf-8"))
    assert [s["year"] for s in meta["scenes"]] == YEARS
    assert all(s["scene_id"] for s in meta["scenes"])
    for y in YEARS:
        with rasterio.open(REAL_OUT / "rasters" / f"ndvi_{y}.tif") as src:
            assert src.crs.to_epsg() == 5186 and src.res == (10.0, 10.0)
            a = src.read(1, masked=True)
            assert a.min() >= -1 and a.max() <= 1
    p = REAL_OUT / "tables" / "parcel_change_summary.csv"
    if p.exists():
        d = pd.read_csv(p, dtype={"pnu": str}, encoding="utf-8-sig")
        assert d["pnu"].dropna().str.len().eq(19).all()
        assert (d["decline_area_m2"] <= d["evaluable_area_m2"] + 1e-6).all()
        assert (d["evaluable_area_m2"] <= d["area_in_aoi_m2"] + 1e-3).all()
        html = (REAL_OUT / "report" / "kr_rail_danyang_ndvi_parcel_report.html").read_text(encoding="utf-8")
        n_review = int(d["is_review_candidate"].sum())
        assert f"const D = " in html and n_review > 0
