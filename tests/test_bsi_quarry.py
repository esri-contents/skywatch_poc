"""NDVI+BSI+채석장 확장(src.ndvi.bsi*, composite, quarry) 테스트.

TEST DATA / SYNTHETIC DATA: 합성 래스터/필지/참조 폴리곤만 사용. 네트워크 호출 없음(B11 캐시를 직접 만든다).
실제 단양 산출물 일관성은 test_real_*(산출물 있을 때만)에서 확인한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
import yaml
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box
from shapely.ops import transform as shp_transform

from src.ndvi import bsi as bsimod
from src.ndvi import parcel_statistics as ps
from src.ndvi.composite import TYPE_CODES, classify_composite
from src.ndvi.processing import discover_scenes
from src.ndvi.quarry import QUARRY_CODES, classify_quarry
from src.ndvi.swir import swir_path

REAL_YAML = Path("config/projects/kr_rail_danyang.yaml")
YEARS = [2016, 2018, 2020, 2022, 2024, 2026]
DATES = ["2016-05-08", "2018-04-28", "2020-04-29", "2022-05-04", "2024-05-03", "2026-05-06"]
BCFG = yaml.safe_load(REAL_YAML.read_text(encoding="utf-8"))["bsi"]
ORIGIN = (440000.0, 4096000.0)


# ------------------------------------------------------------------ 1. BSI 수식
def test_bsi_formula_and_safe_denominator():
    swir, red, nir, blue = np.array([0.3, 0.0]), np.array([0.2, 0.0]), np.array([0.25, 0.0]), np.array([0.05, 0.0])
    out = bsimod.bsi_from_reflectance(swir, red, nir, blue)
    assert out[0] == pytest.approx(((0.3 + 0.2) - (0.25 + 0.05)) / ((0.3 + 0.2) + (0.25 + 0.05)))
    assert np.isnan(out[1])                                   # 분모 0 -> NaN (0 아님)
    assert np.all(np.isnan(bsimod.safe_norm_diff(np.array([1e-5]), np.array([1e-5]), 1e-3)))


# ------------------------------------------------------------------ 합성 입력
def _write_10m(path: Path, ndvi: np.ndarray, order=("B02", "B03", "B04", "B08"), nodata_mask=None, n=40):
    red = np.full((n, n), 1000.0)
    nir = red * (1 + ndvi) / (1 - ndvi)
    bands = {"B02": np.full((n, n), 700.0), "B03": np.full((n, n), 900.0), "B04": red, "B08": nir}
    arr = np.stack([bands[b] for b in order]).astype("uint16")
    if nodata_mask is not None:
        arr[:, nodata_mask] = 0
    with rasterio.open(path, "w", driver="GTiff", height=n, width=n, count=4, dtype="uint16", crs="EPSG:32652",
                       transform=from_origin(*ORIGIN, 10, 10), nodata=0) as dst:
        dst.write(arr)
        dst.descriptions = tuple(order)


def _write_b11(path: Path, dn: np.ndarray, origin=ORIGIN):
    with rasterio.open(path, "w", driver="GTiff", height=dn.shape[0], width=dn.shape[1], count=1, dtype="uint16",
                       crs="EPSG:32652", transform=from_origin(*origin, 20, 20), nodata=0) as dst:
        dst.write(dn.astype("uint16"), 1)
        dst.descriptions = ("B11",)


@pytest.fixture()
def one_scene(tmp_path):
    img, sw = tmp_path / "s2", tmp_path / "sw"
    img.mkdir()
    sw.mkdir()
    _write_10m(img / "s2_2016_2016-05-08.tif", np.full((40, 40), 0.6), order=("B08", "B04", "B03", "B02"))  # 순서 뒤섞음
    sel = {"2016": {"date": "2016-05-08", "scene_id": "SCENE_A", "platform": "Sentinel-2A", "processing_baseline": "02.12",
                    "boa_offset_removed_dn": 0}}
    sp = tmp_path / "sel.json"
    sp.write_text(json.dumps(sel), encoding="utf-8")
    sc = discover_scenes(img, [2016], sp, ["B02", "B03", "B04", "B08"])
    return {"sc": sc, "sw": sw, "img": img, "tmp": tmp_path}


# ------------------------------------------------------------------ 2~3. 밴드 매핑 / 10m-20m 정합
def test_band_mapping_and_20m_aggregation_values(one_scene):
    _write_b11(swir_path(one_scene["sw"], 2016, "SCENE_A"), np.full((21, 21), 3000))
    st = bsimod.build_composite_stack(one_scene["sc"], one_scene["sw"], 10000.0, 4, 1e-3, 20.0)
    assert st["available"][2016] is True
    # 기대값: red=0.1, nir=0.1*(1.6/0.4)=0.4, blue=0.07, swir=0.3
    exp_bsi = ((0.3 + 0.1) - (0.4 + 0.07)) / ((0.3 + 0.1) + (0.4 + 0.07))
    v = st["bsi"][0][np.isfinite(st["bsi"][0])]
    assert v.size > 100 and np.allclose(np.median(v), exp_bsi, atol=1e-3)
    assert np.allclose(np.nanmedian(st["ndvi20"][0]), 0.6, atol=1e-3)
    assert st["transform"].a == 20.0 and st["shape"][0] >= 20


def test_misaligned_10m_20m_grid_raises(one_scene):
    _write_b11(swir_path(one_scene["sw"], 2016, "SCENE_A"), np.full((21, 21), 3000), origin=(ORIGIN[0] + 5, ORIGIN[1]))   # 5m 어긋남 = 10m 픽셀 경계와 불일치
    with pytest.raises(ValueError):
        bsimod.build_composite_stack(one_scene["sc"], one_scene["sw"])


def test_block_mean_requires_valid_children():
    a = np.array([[1.0, 1.0, 2.0, np.nan], [1.0, 1.0, 2.0, 2.0]])
    assert bsimod.block_mean_2x2(a, 4)[0, 0] == 1.0
    assert np.isnan(bsimod.block_mean_2x2(a, 4)[0, 1])        # 4개 중 3개 유효 -> 엄격 모드에서 NaN
    assert bsimod.block_mean_2x2(a, 3)[0, 1] == 2.0


# ------------------------------------------------------------------ 4. NoData
def test_nodata_in_10m_or_b11_stays_nan(one_scene):
    mask = np.zeros((40, 40), dtype=bool)
    mask[10:14, 10:14] = True
    _write_10m(one_scene["img"] / "s2_2016_2016-05-08.tif", np.full((40, 40), 0.6), order=("B08", "B04", "B03", "B02"),
               nodata_mask=mask)
    dn = np.full((21, 21), 3000)
    dn[2:4, 14:16] = 0
    _write_b11(swir_path(one_scene["sw"], 2016, "SCENE_A"), dn)
    st = bsimod.build_composite_stack(one_scene["sc"], one_scene["sw"])
    b = st["bsi"][0]
    assert not np.any(b == 0.0)                                                # 0으로 채우지 않음
    tr = st["transform"]
    t = Transformer.from_crs(32652, 5186, always_xy=True)
    for (x, y) in ((ORIGIN[0] + 10 * 12, ORIGIN[1] - 10 * 12), (ORIGIN[0] + 20 * 14.5, ORIGIN[1] - 20 * 2.5)):
        X, Y = t.transform(x, y)
        r, c = rasterio.transform.rowcol(tr, X, Y)
        assert np.isnan(b[r, c])                                               # 10m 마스크/ B11 무효 모두 NaN
    X, Y = t.transform(ORIGIN[0] + 10 * 30, ORIGIN[1] - 10 * 30)
    r, c = rasterio.transform.rowcol(tr, X, Y)
    assert np.isfinite(b[r, c])


# ------------------------------------------------------------------ 5. 반사율 보정 중복 방지
def test_no_additional_offset_is_applied(one_scene):
    _write_b11(swir_path(one_scene["sw"], 2016, "SCENE_A"), np.full((21, 21), 1500))
    st = bsimod.build_composite_stack(one_scene["sc"], one_scene["sw"], 10000.0)
    exp = ((0.15 + 0.1) - (0.4 + 0.07)) / ((0.15 + 0.1) + (0.4 + 0.07))       # DN/10000만 적용(1000 offset 재차감 금지)
    assert np.nanmedian(st["bsi"][0]) == pytest.approx(exp, abs=1e-3)


def test_missing_b11_year_is_marked_unavailable_not_fabricated(one_scene):
    st = bsimod.build_composite_stack(one_scene["sc"], one_scene["sw"])
    assert st["bsi"] is None and st["available"][2016] is False


# ------------------------------------------------------------------ 6~8. 복합 분류 / 지속·일시 / 노출지
def _series(nd, bs):
    n = np.array(nd, dtype="float32").reshape(len(nd), 1, 1)
    b = np.array(bs, dtype="float32").reshape(len(bs), 1, 1)
    return n, b


def _evt(nd, bs):
    n, b = _series(nd, bs)
    return classify_composite(n, b, None, {**BCFG, "min_valid_obs_pixel": len(nd)}, bias=False)


def test_ndvi_down_bsi_up_is_exposed_increase():
    c = _evt([0.7, 0.1, 0.1, 0.1, 0.1, 0.1], [-0.25, 0.05, 0.06, 0.05, 0.05, 0.06])
    assert c["events"][0, 0, 0] == 2 and c["type"][0, 0] == TYPE_CODES["EXPOSED_SURFACE_INCREASE"]
    assert c["first_exposed_idx"][0, 0] == 0
    assert c["persist"][0, 0] == 1                              # 후속 관측에서도 노출 유지
    assert c["confidence"][0, 0] == 3


def test_ndvi_down_bsi_down_is_uncertain_not_exposed():
    c = _evt([0.7, 0.1, 0.1, 0.1, 0.1, 0.1], [0.0, -0.2, -0.2, -0.2, -0.2, -0.2])
    assert c["events"][0, 0, 0] == 4 and not c["expo_any"][0, 0]


def test_ndvi_down_without_bsi_support_is_vegetation_loss():
    c = _evt([0.7, 0.15, 0.15, 0.15, 0.15, 0.15], [-0.25, -0.22, -0.22, -0.22, -0.22, -0.22])
    assert c["events"][0, 0, 0] == 1 and c["type"][0, 0] == TYPE_CODES["VEGETATION_LOSS"]


def test_recovery_requires_ndvi_up_and_bsi_down():
    c = _evt([0.1, 0.1, 0.8, 0.8, 0.8, 0.8], [0.06, 0.06, -0.3, -0.3, -0.3, -0.3])
    assert c["events"][1, 0, 0] == 3 and c["rec_any"][0, 0]
    c2 = _evt([0.1, 0.1, 0.8, 0.8, 0.8, 0.8], [0.06, 0.06, 0.08, 0.08, 0.08, 0.08])   # BSI가 안 줄면 계절 위상 가능성
    assert not c2["rec_any"][0, 0] and c2["events"][1, 0, 0] == 4


def test_temporary_vs_persistent_vs_unconfirmed_exposure():
    pers = _evt([0.7, 0.7, 0.1, 0.1, 0.1, 0.1], [-.25, -.25, .05, .05, .05, .05])["persist"][0, 0]
    temp = _evt([0.7, 0.7, 0.1, 0.7, 0.7, 0.7], [-.25, -.25, .05, -.25, -.25, -.25])["persist"][0, 0]
    unconf = _evt([0.7, 0.7, 0.7, 0.7, 0.7, 0.1], [-.25, -.25, -.25, -.25, -.25, .05])["persist"][0, 0]
    assert (pers, temp, unconf) == (1, 3, 2)


def test_water_suspect_is_not_exposed():
    n, b = _series([0.7, 0.1, 0.1, 0.1, 0.1, 0.1], [-0.25, 0.05, 0.05, 0.05, 0.05, 0.05])
    m = np.full_like(n, 0.3)                                    # MNDWI>0
    c = classify_composite(n, b, m, {**BCFG, "min_valid_obs_pixel": 6}, bias=False)
    assert not c["expo_any"][0, 0] and c["events"][0, 0, 0] == 4


def test_invalid_observation_is_not_change():
    n, b = _series([0.7, np.nan, 0.1, 0.1, 0.1, 0.1], [-0.25, np.nan, 0.05, 0.05, 0.05, 0.05])
    c = classify_composite(n, b, None, {**BCFG, "min_valid_obs_pixel": 4}, bias=False)
    assert c["events"][0, 0, 0] == 255 and c["events"][1, 0, 0] == 255


def test_stable_vegetation_and_persistent_exposed_states():
    c = _evt([0.7] * 6, [-0.25] * 6)
    assert c["type"][0, 0] == TYPE_CODES["STABLE_VEGETATION"]
    c = _evt([0.1] * 6, [0.06] * 6)
    assert c["type"][0, 0] == TYPE_CODES["PERSISTENT_EXPOSED_SURFACE"]


# ------------------------------------------------------------------ 9. 채석장 인접성
def _grid(h=12, w=12, res=20.0):
    return from_origin(0.0, h * res, res, res), (h, w)


def _comp_with(expo_cells, shape, rec_cells=()):
    z = np.zeros(shape, dtype=bool)
    e = z.copy()
    for r, c in expo_cells:
        e[r, c] = True
    r_ = z.copy()
    for r, c in rec_cells:
        r_[r, c] = True
    return {"expo_any": e, "loss_any": z.copy(), "unc_any": z.copy(), "rec_any": r_, "rec_from_exposed": r_.copy(),
            "evaluable": np.ones(shape, dtype=bool)}


def test_quarry_adjacency_classification():
    tr, shape = _grid()
    quarry = gpd.GeoDataFrame(geometry=[box(0, 160, 60, 240)], crs=5186)       # 좌상단 3x4 픽셀 영역
    comp = _comp_with([(2, 3), (2, 4)] + [(9, 9), (9, 10)] + [(1, 1)], shape, rec_cells=[(5, 0)])
    q = classify_quarry(comp, quarry, tr, shape, adjacency_m=40, min_component_pixels=2)["quarry_class"]
    assert q[2, 3] == QUARRY_CODES["POTENTIAL_QUARRY_EXPANSION"]          # 인접·연결(바깥)
    assert q[2, 4] == QUARRY_CODES["POTENTIAL_QUARRY_EXPANSION"]
    assert q[9, 9] == QUARRY_CODES["EXPOSED_SURFACE_CHANGE"]              # 멀리 떨어진 노출지
    assert q[1, 1] == QUARRY_CODES["QUARRY_INTERNAL_CHANGE"]              # 참조 폴리곤 내부 변화는 별도 표시
    assert q[5, 0] == QUARRY_CODES["POSSIBLE_REVEGETATION"]


def test_without_reference_quarry_expansion_is_never_assigned():
    tr, shape = _grid()
    comp = _comp_with([(2, 3), (2, 4)], shape)
    q = classify_quarry(comp, gpd.GeoDataFrame(geometry=[], crs=5186), tr, shape, 40, 2)["quarry_class"]
    assert (q == QUARRY_CODES["POTENTIAL_QUARRY_EXPANSION"]).sum() == 0
    assert (q == QUARRY_CODES["VERIFIED_QUARRY_AREA"]).sum() == 0          # 검증 안 된 영역을 채석장으로 가정하지 않음
    assert q[2, 3] == QUARRY_CODES["EXPOSED_SURFACE_CHANGE"]


def test_small_component_below_min_pixels_not_expansion():
    tr, shape = _grid()
    quarry = gpd.GeoDataFrame(geometry=[box(0, 160, 60, 240)], crs=5186)
    comp = _comp_with([(2, 4)], shape)
    q = classify_quarry(comp, quarry, tr, shape, 40, 2)["quarry_class"]
    assert q[2, 4] == QUARRY_CODES["EXPOSED_SURFACE_CHANGE"]


# ------------------------------------------------------------------ 10. 필지 면적
def test_parcel_area_stats_weighted_and_bounded():
    tr, shape = _grid()
    comp_e = np.zeros(shape, dtype=bool)
    comp_e[2:4, 2:4] = True                                     # 20m 픽셀 2x2 노출지: x 40~80, y 160~200
    geoms = [box(30, 160, 70, 200), box(300, 20, 340, 60)]
    wt = ps.pixel_weights(geoms, tr, shape)
    a = np.bincount(wt["p"], weights=wt["a"] * comp_e.ravel()[wt["f"]], minlength=2)
    inter = geoms[0].intersection(box(40, 160, 80, 200)).area
    assert a[0] == pytest.approx(inter) and a[1] == 0
    total = np.bincount(wt["p"], weights=wt["a"], minlength=2)
    assert np.all(a <= total + 1e-9) and total[0] == pytest.approx(geoms[0].area)


# ------------------------------------------------------------------ 종단 간(합성): 확장 파이프라인 + 보고서 + CSV 일치
@pytest.fixture()
def synth_ext(tmp_path):
    from src.ndvi.pipeline import run
    img, sw, ref = tmp_path / "s2", tmp_path / "sw", tmp_path / "ref"
    for d in (img, sw, ref):
        d.mkdir()
    region = {"A": (2, 14, 2, 14), "B": (2, 14, 22, 34), "C": (22, 34, 2, 14)}
    nd = {"A": [0.7, 0.7, 0.1, 0.1, 0.1, 0.1], "B": [0.7] * 6, "C": [0.7, 0.7, 0.1, 0.7, 0.7, 0.7]}
    sel = {}
    for i, y in enumerate(YEARS):
        n10 = np.full((40, 40), 0.6)
        b11 = np.full((20, 20), 1800.0)
        for k, (r0, r1, c0, c1) in region.items():
            n10[r0:r1, c0:c1] = nd[k][i]
            bare = nd[k][i] < 0.3
            b11[r0 // 2:r1 // 2, c0 // 2:c1 // 2] = 3500.0 if bare else 1400.0
        _write_10m(img / f"s2_{y}_{DATES[i]}.tif", n10)
        sid = f"SCENE_{y}"
        _write_b11(swir_path(sw, y, sid), b11)
        sel[str(y)] = {"year": y, "date": DATES[i], "scene_id": sid, "platform": "Sentinel-2A",
                       "processing_baseline": "02.12", "boa_offset_removed_dn": 0}
    selp = tmp_path / "sel.json"
    selp.write_text(json.dumps(sel), encoding="utf-8")
    t = Transformer.from_crs(32652, 5186, always_xy=True)

    def ub(r0, r1, c0, c1):
        return shp_transform(t.transform, box(ORIGIN[0] + c0 * 10, ORIGIN[1] - r1 * 10, ORIGIN[0] + c1 * 10, ORIGIN[1] - r0 * 10))

    geoms = [ub(*v) for v in region.values()]
    cad = gpd.GeoDataFrame({"pnu": ["0100000000000000001", "0100000000000000002", "0100000000000000003"],
                            "jibun": ["1-1 전", "2 답", "3 대"], "addr": ["a", "b", "c"], "id": ["1", "2", "3"],
                            "emd_nm": ["x"] * 3, "li_nm": ["y"] * 3, "gosi_year": ["2025"] * 3, "gosi_month": ["01"] * 3,
                            "jiga": ["1"] * 3}, geometry=geoms, crs=5186)
    cadp = tmp_path / "cad.gpkg"
    cad.to_file(cadp, driver="GPKG")
    aoi = gpd.GeoDataFrame(geometry=[box(*cad.total_bounds).buffer(60)], crs=5186)
    aoip = tmp_path / "aoi.geojson"
    aoi.to_file(aoip, driver="GeoJSON")
    rail_line = shp_transform(t.transform, box(ORIGIN[0], ORIGIN[1] - 330, ORIGIN[0] + 400, ORIGIN[1] - 320).exterior)
    rail = gpd.GeoDataFrame({"railway": ["rail"], "tunnel": [None]}, geometry=[rail_line], crs=5186).to_crs(4326)
    railp = tmp_path / "rail.geojson"
    rail.to_file(railp, driver="GeoJSON")
    q = gpd.GeoDataFrame({"landuse": ["quarry"], "natural": [None], "osm_id": [1]},
                         geometry=[ub(1, 16, 8, 18)], crs=5186).to_crs(4326)        # 필지 A와 겹치는 참조 영역
    q.to_file(ref / "osm_quarry_candidates.geojson", driver="GeoJSON")
    (ref / "reference_provenance.json").write_text(json.dumps({"status": "OK", "source": "synthetic"}), encoding="utf-8")
    cfg = yaml.safe_load(REAL_YAML.read_text(encoding="utf-8"))
    cfg["paths"].update(aoi=str(aoip), railway=str(railp), imagery_dir=str(img))
    cfg["ndvi"].update(output_dir=str(tmp_path / "ndvi"), scene_selection=str(selp))
    cfg["ndvi"]["cadastre"]["cache_dir"] = str(tmp_path / "cc")
    cfg["bsi"].update(output_dir=str(tmp_path / "bsi"), swir_cache_dir=str(sw), reference_dir=str(ref))
    proj = tmp_path / "p.yaml"
    proj.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    m = run(proj, offline=True, cadastre=str(cadp), force=True)
    assert m["status"] == "SUCCESS", m.get("error")
    return {"proj": proj, "cfg": cfg, "tmp": tmp_path}


def test_extension_end_to_end_synthetic(synth_ext):
    from src.ndvi.pipeline import run_extension
    m = run_extension(synth_ext["proj"], offline=True)
    assert m["status"] == "SUCCESS", m.get("error")
    assert m["paid_api_called"] is False and m["skywatch_api_called"] is False
    out = Path(synth_ext["cfg"]["bsi"]["output_dir"])
    for y in YEARS:
        assert (out / "rasters" / f"bsi_{y}.tif").exists()
    assert (out / "rasters" / "delta_bsi_2016_2026.tif").exists() and (out / "rasters" / "delta_ndvi20_2022_2024.tif").exists()
    df = pd.read_csv(out / "tables" / "parcel_composite_summary.csv", dtype={"pnu": str}, encoding="utf-8-sig")
    t = dict(zip(df["pnu"], df["composite_type"]))
    assert t["0100000000000000001"] == "EXPOSED_SURFACE_INCREASE"
    assert t["0100000000000000002"] == "STABLE_VEGETATION"
    assert (df["composite_resolution_m"] == 20).all() and (df["ndvi_resolution_m"] == 10).all()
    # 식생 감소 ⊃ 노출지 증가, 평가면적 초과 없음
    assert (df["exposed_increase_area_20m_m2"] <= df["veg_decline_area_20m_m2"] + 1e-6).all()
    assert (df["veg_decline_area_20m_m2"] <= df["evaluable_area_20m_m2"] + 1e-6).all()
    # 두 개의 별도 랭킹 파일
    assert (out / "tables" / "priority_A_rail_adjacent_review.csv").exists()
    b = pd.read_csv(out / "tables" / "priority_B_quarry_exposed_review.csv", dtype={"pnu": str}, encoding="utf-8-sig")
    assert "0100000000000000001" in set(b["pnu"])
    # 참조 채석장과 겹치는 필지는 거리 0 / 중첩 비율 > 0
    a = df[df["pnu"] == "0100000000000000001"].iloc[0]
    assert a["dist_to_quarry_m"] == 0 and a["quarry_overlap_ratio"] > 0
    # HTML 값 == CSV 값
    html = (out / "report" / "kr_rail_danyang_ndvi_bsi_quarry_report.html").read_text(encoding="utf-8")
    d = json.loads(re.search(r"const D=(\{.*?\});\s*const B=", html, re.S).group(1))
    hp = {p["pnu"]: p for p in d["parcels"]}
    for r in df.itertuples():
        assert hp[r.pnu]["ex"] == pytest.approx(round(r.exposed_increase_area_20m_m2, 0), abs=1)
        assert hp[r.pnu]["ct"] == r.composite_type
    assert "https://" not in html.replace("http://www.w3.org", "")
    assert "채석장 확장 가능성" in html and "탐색적 분석이며" in html
    # 쉬운 물건조사 선별 보고서/CSV: 구분은 4가지, 판단 불가 필지는 '판단 보류'
    simple = out / "report" / "kr_rail_danyang_survey_screening_report.html"
    assert simple.exists() and "지장물" in simple.read_text(encoding="utf-8")
    sc = pd.read_csv(out / "tables" / "survey_screening_parcels.csv", dtype={"PNU": str}, encoding="utf-8-sig")
    assert set(sc["확인_구분"]) <= {"먼저 확인", "확인 권장", "변화 흔적 없음", "판단 보류"}
    row = sc[sc["PNU"] == "0100000000000000001"].iloc[0]
    assert row["확인_구분"] == "먼저 확인"          # 맨땅 증가 필지는 먼저 확인
    assert sc[sc["PNU"] == "0100000000000000002"].iloc[0]["확인_구분"] == "변화 흔적 없음"
    # 기존 NDVI 산출물이 유지됨
    assert (Path(synth_ext["cfg"]["ndvi"]["output_dir"]) / "tables" / "parcel_change_summary.csv").exists()


def test_extension_without_b11_reports_unavailable(synth_ext):
    from src.ndvi.pipeline import run_extension
    for p in Path(synth_ext["cfg"]["bsi"]["swir_cache_dir"]).glob("*.tif"):
        p.unlink()
    m = run_extension(synth_ext["proj"], offline=True)
    assert m["status"] == "BSI_UNAVAILABLE"
    assert not (Path(synth_ext["cfg"]["bsi"]["output_dir"]) / "rasters" / "bsi_2016.tif").exists()


# ------------------------------------------------------------------ 실제 산출물(있을 때만)
REAL = Path("outputs/kr_rail_danyang/bsi")


@pytest.mark.skipif(not (REAL / "metadata" / "bsi_metadata.json").exists(), reason="실제 BSI 산출물 없음")
def test_real_outputs_consistency():
    meta = json.loads((REAL / "metadata" / "bsi_metadata.json").read_text(encoding="utf-8"))
    assert meta["accuracy_assessment"]["performed"] is False        # 정답 자료 없이 정확도 수치 금지
    for y, e in meta["swir_provenance"].items():
        if e["status"] == "OK":
            assert e["scene_id"] and e["recorded_boa_offset_removed_dn"] is not None
    for y in meta["years_available_bsi"]:
        with rasterio.open(REAL / "rasters" / f"bsi_{y}.tif") as s:
            a = s.read(1, masked=True)
            assert s.crs.to_epsg() == 5186 and s.res == (20.0, 20.0) and a.min() >= -1 and a.max() <= 1
    df = pd.read_csv(REAL / "tables" / "parcel_composite_summary.csv", dtype={"pnu": str}, encoding="utf-8-sig")
    assert df["pnu"].str.len().eq(19).all()
    assert (df["exposed_increase_area_20m_m2"] <= df["veg_decline_area_20m_m2"] + 1e-6).all()
    assert (df["veg_decline_area_20m_m2"] <= df["evaluable_area_20m_m2"] + 1e-6).all()
    html = (REAL / "report" / "kr_rail_danyang_ndvi_bsi_quarry_report.html").read_text(encoding="utf-8")
    d = json.loads(re.search(r"const D=(\{.*?\});\s*const B=", html, re.S).group(1))
    assert len(d["parcels"]) == len(df)
    hp = {p["pnu"]: p for p in d["parcels"]}
    for r in df.itertuples():
        assert hp[r.pnu]["ct"] == r.composite_type and hp[r.pnu]["qc"] == r.quarry_context
        assert hp[r.pnu]["ex"] == pytest.approx(round(r.exposed_increase_area_20m_m2, 0), abs=1)
