"""context(지목/토지이용 맥락), composites(중앙값 합성), visual_validation(혼동행렬), comparison 테스트.

TEST DATA / SYNTHETIC DATA: 합성 입력만 사용하며 네트워크를 호출하지 않는다.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin
from shapely.geometry import box

from src.ndvi import composites as comp_mod
from src.ndvi.comparison import compare_runs
from src.ndvi.context import parcel_context
from src.ndvi.visual_validation import confusion


def _parcels():
    g = gpd.GeoDataFrame({"jimok_abbr": ["답", "임", "대", None]},
                         geometry=[box(0, 0, 40, 40), box(60, 0, 100, 40), box(120, 0, 160, 40), box(180, 0, 220, 40)], crs=5186)
    return g, g.geometry.copy()


def test_parcel_context_jimok_and_lulc_share():
    g, clipped = _parcels()
    tr = from_origin(0, 40, 10, 10)
    shape = (4, 22)
    lulc = {2022: np.zeros(shape, dtype="uint8")}
    lulc[2022][:, 0:4] = 5          # 첫 필지: 경작지(crops)
    lulc[2022][:, 12:16] = 7        # 셋째 필지: 시가지(built)
    ctx = parcel_context(g, clipped, {}, lulc, tr, shape, {"agri_share_min": 0.5})
    assert ctx["jimok"].iloc[:3].tolist() == ["답", "임야", "대"] and pd.isna(ctx["jimok"].iloc[3])
    assert ctx["jimok_agri"].tolist() == [True, False, False, False]
    assert ctx["lulc_crops_2022"].iloc[0] == pytest.approx(1.0)
    assert ctx["lulc_built_2022"].iloc[2] == pytest.approx(1.0)
    assert bool(ctx["agri_context"].iloc[0]) and not bool(ctx["agri_context"].iloc[1])
    assert "지목" in ctx["agri_evidence"].iloc[0] and "LULC" in ctx["agri_evidence"].iloc[0]


def test_median_composite_rejects_cloud_outlier_math():
    # 합성 로직의 핵심: 유효 관측만의 중앙값이며 구름(NaN)은 무시, 관측 수 < min_obs면 무효
    stack = np.array([[[0.2]], [[0.9]], [[np.nan]], [[0.22]]], dtype="float32")
    with np.errstate(all="ignore"):
        med = np.nanmedian(stack, axis=0)
    assert med[0, 0] == pytest.approx(0.22)
    n = np.isfinite(stack).sum(0)
    assert n[0, 0] == 3


def test_stac_duplicate_items_keep_latest_processing(monkeypatch):
    items = [{"id": "S2A_MSIL2A_20240503T020651_N0510_R103_T52SDG_20240503T072226", "properties": {"datetime": "2024-05-03T02:06:51Z"}},
             {"id": "S2A_MSIL2A_20240503T020651_N0510_R103_T52SDG_20240601T000000", "properties": {"datetime": "2024-05-03T02:06:51Z"}},
             {"id": "S2A_MSIL2A_20240503T020651_N0510_R103_T52SDF_20240503T072226", "properties": {"datetime": "2024-05-03T02:06:51Z"}}]

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"features": items}

    monkeypatch.setattr(comp_mod.requests, "post", lambda *a, **k: R())
    out = comp_mod._search([0, 0, 1, 1], 2024, "04-20", "05-20", 70)
    assert len(out) == 2                                         # 타일 2개, 중복 처리본은 1개만
    assert any(i["id"].endswith("20240601T000000") for i in out)


def test_visual_confusion_matrix_excludes_unclear_and_counts_correctly():
    samples = [{"id": f"S{i}", "stratum": "A" if i < 4 else "B", "pred": p}
               for i, p in enumerate([True, True, False, False, True, False])]
    labels = {"S0": "BARE", "S1": "VEG_TREE", "S2": "BUILT", "S3": "VEG_LOW", "S4": "UNCLEAR", "S5": "BARE"}
    c = confusion(samples, labels, lambda s: s["pred"])
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (1, 1, 2, 1)
    assert c["n"] == 5                                            # UNCLEAR 제외
    assert c["precision"] == pytest.approx(0.5) and c["recall"] == pytest.approx(1 / 3)
    assert c["per_stratum"]["A"]["n"] == 4


def test_compare_runs_flags_robust_candidates(tmp_path):
    def mk(root, types, ctxs):
        (root / "tables").mkdir(parents=True)
        pd.DataFrame({"pnu": ["1", "2", "3"], "jibun_display": ["a", "b", "c"], "area_in_aoi_m2": [1000.0] * 3,
                      "composite_type": types, "quarry_context": ctxs,
                      "exposed_increase_area_20m_m2": [800.0, 0.0, 1200.0]}).to_csv(
            root / "tables" / "parcel_composite_summary.csv", index=False, encoding="utf-8-sig")
        nd = root.parent / "ndvi" / "metadata"
        nd.mkdir(parents=True, exist_ok=True)
        (nd / "analysis_metadata.json").write_text(
            '{"aoi": {"summary": {"per_year_aoi_ndvi_mean": {"2016": 0.4}, "aoi_decline_area_m2": 10000}}}', encoding="utf-8")

    s_root, c_root = tmp_path / "single" / "bsi", tmp_path / "comp" / "bsi"
    mk(s_root, ["EXPOSED_SURFACE_INCREASE", "STABLE_VEGETATION", "EXPOSED_SURFACE_INCREASE"],
       ["POTENTIAL_QUARRY_EXPANSION", "NONE", "EXPOSED_SURFACE_CHANGE"])
    mk(c_root, ["EXPOSED_SURFACE_INCREASE", "STABLE_VEGETATION", "NO_SIGNIFICANT_CHANGE"],
       ["POTENTIAL_QUARRY_EXPANSION", "NONE", "NONE"])
    r = compare_runs(s_root, c_root, tmp_path / "out")
    assert r["exposed_increase"] == {"single": 2, "composite": 1, "both": 1, "jaccard": 0.5}
    assert r["potential_quarry_expansion"]["both"] == 1
    assert r["robust_candidates"] == 1
    rb = pd.read_csv(tmp_path / "out" / "robust_candidates.csv", dtype={"pnu": str}, encoding="utf-8-sig")
    assert rb["pnu"].tolist() == ["1"]
