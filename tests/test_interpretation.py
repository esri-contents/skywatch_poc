"""필지 단위 구조물 판독(src/interpretation) 테스트 - 합성 영상."""

import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin

from src.interpretation import classifier, obia, parcels

RES = 0.5
RULES = {"prob_hi": 0.6, "prob_lo": 0.3, "tolerance_m": 1.0, "min_object_m2": 20, "corroborate_min": 0.5}


def _scene():
    """회색 바닥(0.45)에 밝은 지붕(0.85, 20x30px)과 남쪽에만 그림자(0.08, 4px 띠)."""
    rgb = np.full((80, 80, 3), 0.45, np.float32)
    rgb[20:40, 25:55] = 0.85
    rgb[40:44, 25:55] = 0.08
    labels = np.ones((80, 80), np.int64)
    labels[20:40, 25:55] = 2
    labels[40:44, 25:55] = 3
    return rgb, labels


def test_shift_moves_values_and_fills_edges():
    a = np.arange(9).reshape(3, 3)
    s = obia._shift(a, 1, 0, -1)
    assert s[0].tolist() == [3, 4, 5]
    assert s[2].tolist() == [-1, -1, -1]


def test_roof_features_capture_shape_and_one_sided_shadow():
    rgb, labels = _scene()
    f = obia.segment_features(rgb, labels, np.ones(labels.shape, bool), RES, ring_m=2.0, shadow_dl=20).set_index("label")
    roof = f.loc[2]
    assert roof["area_m2"] == pytest.approx(20 * 30 * RES * RES)
    assert roof["rect_fill"] == pytest.approx(1.0, abs=0.05)
    assert roof["elong"] == pytest.approx(1.5, abs=0.1)
    assert roof["contrast"] > 20
    # 그림자는 남쪽 한 면에만 있어 방향별 최대값이 4방향 평균보다 크다(둘레 평균은 희석됨).
    assert roof["shadow_max"] == pytest.approx(1.0)
    assert roof["shadow_mean"] == pytest.approx(0.25)


def test_hysteresis_new_object_and_ambiguous_pixels():
    p_from = np.zeros((60, 60), np.float32)
    p_to = np.zeros((60, 60), np.float32)
    p_to[10:22, 10:22] = 0.9          # 신규: 12x12px = 36㎡
    p_to[35:50, 35:50] = 0.9
    p_from[35:50, 35:50] = 0.45       # 2022에 애매(0.3~0.6) - 신규로 세지 않음
    tr = from_origin(0, 30, RES, RES)
    objs = parcels.change_objects(p_from, p_to, RULES, RES, tr, "EPSG:5186", None, "new")
    assert len(objs) == 1
    assert objs.iloc[0]["area_m2"] == pytest.approx(36.0)


def test_shadow_and_shape_filters_drop_artifacts():
    p_from = np.zeros((80, 80), np.float32)
    p_to = np.zeros((80, 80), np.float32)
    p_to[5:17, 5:17] = 0.9            # 사각형 신규 - 남음
    p_to[30:42, 30:42] = 0.9          # 2022에 그림자였던 곳 - 제외
    for i in range(40):               # 대각선 띠(사각형 채움률 낮음) - 제외
        p_to[40 + i // 2, 20 + i:24 + i] = 0.9
    shadow = np.zeros_like(p_to, bool)
    shadow[30:42, 30:42] = True
    rules = {**RULES, "min_rect_fill": 0.6}
    objs = parcels.change_objects(p_from, p_to, rules, RES, from_origin(0, 40, RES, RES), "EPSG:5186", None, "new", shadow)
    assert len(objs) == 1
    assert objs.iloc[0]["rect_fill"] == pytest.approx(1.0)


def _parcel_fixture():
    """40m x 20m 격자: 왼쪽 필지 L(0~20m)에 건물, 오른쪽 필지 R(20~40m)은 비어 있음."""
    import geopandas as gpd
    from shapely.geometry import box
    tr = from_origin(0, 20, RES, RES)
    profile = {"height": 40, "width": 80, "transform": tr, "crs": "EPSG:5186"}
    cad = gpd.GeoDataFrame({"pnu": ["L", "R"], "addr": ["a", "b"], "jibun": ["1대", "2도"]},
                           geometry=[box(0, 0, 20, 20), box(20, 0, 40, 20)], crs="EPSG:5186")
    bld = gpd.GeoDataFrame(geometry=[box(5, 5, 19.6, 15)], crs="EPSG:5186")  # 버퍼 1m가 R 쪽으로 0.6m 넘어감
    return profile, cad, bld


def test_registered_area_uses_raw_footprint_not_buffer():
    import geopandas as gpd
    profile, cad, bld = _parcel_fixture()
    p = np.zeros((40, 80), np.float32)
    rules = {**RULES, "min_struct_m2": 10, "min_rect_fill": 0.6}
    objs = gpd.GeoDataFrame(columns=["kind", "area_m2", "geometry"], geometry="geometry", crs="EPSG:5186")
    table, _ = parcels.parcel_table(cad, {"t0": p, "t2": p}, profile, RES, bld, objs, rules, "t0", "t2")
    reg = dict(zip(table["pnu"], table["registered_m2"]))
    assert reg["L"] > 100
    assert reg["R"] == 0                       # 이웃 건물 버퍼 띠(0.6m x 10m)는 등재가 아님
    assert table.set_index("pnu").loc["R", "status_t0"] == "없음"
    assert bool(table.set_index("pnu").loc["R", "linear_jimok"]) is True


def test_unregistered_flag_needs_one_object_not_summed_pixels():
    import geopandas as gpd
    profile, cad, bld = _parcel_fixture()
    p = np.zeros((40, 80), np.float32)
    p[4:16, 50:62] = 0.9                       # R 안, 건물에서 먼 6m x 6m = 36㎡ 구조물
    rules = {**RULES, "min_struct_m2": 10, "min_rect_fill": 0.6}
    fp, near = parcels.footprint_masks(bld, profile, rules["tolerance_m"])
    u = parcels.unregistered_objects(p, near, rules, RES, profile["transform"], "EPSG:5186")
    assert len(u) == 1 and u.iloc[0]["area_m2"] == pytest.approx(36.0)
    table, objs = parcels.parcel_table(cad, {"t0": p, "t2": p}, profile, RES, bld, u, rules, "t0", "t2", (fp, near))
    assert table.set_index("pnu")["unregistered_flag"].to_dict() == {"L": False, "R": True}
    assert table.set_index("pnu").loc["R", "unregistered_conf"] == "저신뢰"   # R은 도로(선형 지목)
    assert objs.iloc[0]["tier"] == "U" and objs.iloc[0]["pnu"] == "R"

    q = np.zeros((40, 80), np.float32)          # 4㎡ 조각 8개(합계 32㎡) - 각 조각이 20㎡ 미만이라 객체 없음
    for i in range(8):
        r, c = 2 + (i // 4) * 8, 44 + (i % 4) * 8
        q[r:r + 4, c:c + 4] = 0.9
    assert len(parcels.unregistered_objects(q, near, rules, RES, profile["transform"], "EPSG:5186")) == 0


def test_unregistered_object_attached_to_registered_roof_is_separated():
    """footprint보다 큰 등재 건물 지붕이 버퍼 밖으로 이어지면 '부속 의심', 떨어진 창고는 '독립'."""
    profile, cad, bld = _parcel_fixture()      # 건물 x 5~19.6m, y 5~15m
    p = np.zeros((40, 80), np.float32)
    p[10:30, 10:50] = 0.9                       # 지붕이 footprint에서 동쪽으로 x=25m까지 이어짐(버퍼 밖 4.4m)
    p[4:16, 60:72] = 0.9                        # 떨어진 6m x 6m 창고
    rules = {**RULES, "min_struct_m2": 10, "min_rect_fill": 0.6}
    fp, near = parcels.footprint_masks(bld, profile, rules["tolerance_m"])
    u = parcels.unregistered_objects(p, near, rules, RES, profile["transform"], "EPSG:5186", fp=fp)
    assert len(u) == 2
    attached = {("roof" if g.centroid.x < 28 else "shed"): bool(a) for g, a in zip(u.geometry, u["attached"])}
    assert attached == {"roof": True, "shed": False}


def test_tolerance_ignores_registration_shift():
    """같은 구조물이 1px(0.5m) 밀린 것은 정합 오차 여유 안이라 신규가 아니다."""
    p_from = np.zeros((60, 60), np.float32)
    p_to = np.zeros((60, 60), np.float32)
    p_from[10:30, 10:30] = 0.9
    p_to[11:31, 11:31] = 0.9
    objs = parcels.change_objects(p_from, p_to, RULES, RES, from_origin(0, 30, RES, RES), "EPSG:5186", None, "new")
    assert len(objs) == 0


def test_drop_aggregate_parcels_keeps_leaves_and_lone_parent():
    import geopandas as gpd
    from shapely.geometry import box
    cad = gpd.GeoDataFrame({"pnu": ["parent", "a", "b", "lone"]}, geometry=[
        box(0, 0, 20, 10),               # a + b로 100% 덮이는 상위 폴리곤
        box(0, 0, 10, 10), box(10, 0, 20, 10),
        box(50, 0, 60, 10),              # 하위 필지가 없는 본번 - 남겨야 함
    ], crs="EPSG:5186")
    assert parcels.drop_aggregate_parcels(cad)["pnu"].tolist() == ["a", "b", "lone"]


def test_drop_aggregate_parcels_resolves_identical_duplicates_by_pnu():
    """부번이 하나뿐인 본번: 같은 모양 두 개 중 PNU가 있는 쪽만 남는다."""
    import geopandas as gpd
    from shapely.geometry import box
    cad = gpd.GeoDataFrame({"pnu": [None, "x1"]}, geometry=[box(0, 0, 10, 10), box(0, 0, 10, 10)], crs="EPSG:5186")
    assert parcels.drop_aggregate_parcels(cad)["pnu"].tolist() == ["x1"]


def test_cohen_kappa_and_human_review_roundtrip(tmp_path):
    from src.interpretation import qa
    a = pd.Series(["structure", "structure", "not_structure", "not_structure"])
    assert qa.cohen_kappa(a, a) == pytest.approx(1.0)
    b = pd.Series(["structure", "not_structure", "structure", "not_structure"])
    assert qa.cohen_kappa(a, b) == pytest.approx(0.0)

    r = tmp_path / "round9"; r.mkdir()
    pd.DataFrame({"sample_id": ["u_000", "u_001"], "stratum": ["unregistered"] * 2, "x": [0, 1], "y": [0, 1],
                  "label": ["structure", "not_structure"], "note": ["", ""]}).to_csv(r / "qa_labels.csv", index=False, encoding="utf-8-sig")
    path = qa.export_for_human(tmp_path)
    t = pd.read_csv(path, encoding="utf-8-sig")
    t["human_label"] = ["structure", "structure"]; t["judge"] = "검수자"
    t.to_csv(path, index=False, encoding="utf-8-sig")
    qa.export_for_human(tmp_path)                       # 다시 만들어도 사람 입력 보존
    res = qa.agreement(path)
    assert res["reviewed"] == 2 and res["judges"] == ["검수자"]
    assert res["by_stratum"][0]["human_precision"] == pytest.approx(1.0)


def test_stratified_precision_weights_by_population(tmp_path):
    from src.interpretation import qa
    rows = [("unreg_대", 900, "structure")] * 9 + [("unreg_대", 900, "not_structure")] \
        + [("unreg_linear", 100, "structure")] * 2 + [("unreg_linear", 100, "not_structure")] * 8
    p = tmp_path / "qa_labels.csv"
    pd.DataFrame(rows, columns=["stratum", "population", "label"]).to_csv(p, index=False, encoding="utf-8-sig")
    r = qa.stratified_precision(p, "unreg")
    assert r["precision"] == pytest.approx(0.9 * 0.9 + 0.1 * 0.2)   # 단순 비율 11/20=0.55가 아님
    assert r["ci_lo"] < r["precision"] < r["ci_hi"]


def test_new_structure_recall_matches_by_key9():
    from src.interpretation import reference
    parcels_df = pd.DataFrame({"pnu": ["4121010500100010000", "4121010500100020000"],
                               "new_A_n": [1, 0], "new_B_n": [0, 0], "new_C_n": [0, 1]})
    events = pd.DataFrame({"key9": reference.key9(pd.Series(["4121010500000010000", "4121010500000020000", "4119410600000010000"])),
                           "source": "대장", "event": "사용승인", "date": pd.Timestamp("2023-05-01"), "purpose": ""})
    r = reference.new_structure_recall(parcels_df, events)["사용승인"]
    assert (r["parcels"], r["A_or_B"], r["A_B_or_C"]) == (2, 1, 2)   # 산 여부 자리가 틀린 PNU도 key9로 매칭


def test_wilson_interval_brackets_proportion():
    from src.interpretation.qa import wilson
    lo, hi = wilson(15, 20)
    assert lo < 0.75 < hi
    assert lo == pytest.approx(0.531, abs=0.005) and hi == pytest.approx(0.888, abs=0.005)


def test_labels_and_blocks_from_footprint_overlap():
    f = pd.DataFrame({c: [0.0, 0.0, 0.0] for c in classifier.FEATURES})
    f["area_m2"] = [50.0, 50.0, 50.0]
    f["fp_frac"] = [0.9, 0.3, 0.0]
    f["fp_near"] = [1.0, 0.6, 0.0]
    f["tile_id"] = 1
    f["x"], f["y"] = [10.0, 10.0, 700.0], [10.0, 10.0, 10.0]
    d = classifier.prepare({"t0": f}, block_m=600)
    assert d["target"].tolist() == [1, -1, 0]   # 경계에 걸친 조각은 학습 제외
    assert d["block"].nunique() == 2
