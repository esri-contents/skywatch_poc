"""건축물대장 필지 대조(reference), 기준 건물 레이어(buildings_ref), 미등재 등급과 붙임 1·1-2(parcels·deliver),
후보 판독표(screen) 테스트."""

import json
from argparse import Namespace

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

import numpy as np

from src.interpretation import addin_run, buildings_ref, parcels, reference, screen
from src.interpretation.deliver import appendix1, appendix1b, write_appendix1_shp

P1, P2, P3, P4 = ("4121010300100010000", "4121010300100020000", "4121010300100030000", "4121010300100040000")


def _rec(bun, ji, pk, kind="2", arch=None, plat="0"):
    r = {"sigunguCd": "41210", "bjdongCd": "10300", "platGbCd": plat, "bun": f"{bun:04d}", "ji": f"{ji:04d}",
         "mgmBldrgstPk": pk, "regstrKindCd": kind, "mainPurpsCdNm": "단독주택"}
    if arch is not None:
        r["archArea"] = arch
    return r


def _write(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return p


def test_register_by_parcel_merges_sources_and_attached_lots(tmp_path):
    title = _write(tmp_path, "t.json", [_rec(1, 0, 11, arch=100.0), _rec(2, 0, 12, arch=50.0)])
    # 기본개요: 표제부와 같은 대장(12)은 중복으로 세지 않고, 표제부에 없던 대장(13)과 전유부(4)는 각각 보태기·제외
    basis = _write(tmp_path, "b.json", [_rec(2, 0, 12), _rec(2, 0, 13), _rec(2, 0, 14, kind="4")])
    # 부속지번: 대장 11(대표지번 1)이 필지 3에 걸침
    atch = _write(tmp_path, "a.json", [{**_rec(1, 0, 11), "atchSigunguCd": "41210", "atchBjdongCd": "10300",
                                        "atchPlatGbCd": "0", "atchBun": "0003", "atchJi": "0000"}])
    reg = reference.register_by_parcel(title, basis, atch)
    k = reference.key9(pd.Series([P1, P2, P3]))
    assert reg.loc[k[0], "register_n"] == 1 and reg.loc[k[0], "register_arch_m2"] == 100.0
    assert reg.loc[k[1], "register_n"] == 2 and reg.loc[k[1], "register_arch_m2"] == 50.0  # 12(표제부)+13(기본개요)
    assert reg.loc[k[2], "register_n"] == 1 and reg.loc[k[2], "register_arch_m2"] == 0.0  # 부속지번만
    assert reg.loc[k[0], "register_multi_lot"] and reg.loc[k[2], "register_multi_lot"] and not reg.loc[k[1], "register_multi_lot"]
    assert reg.loc[k[0], "register_purpose"] == "단독주택"


def test_register_by_parcel_without_files_is_empty():
    assert len(reference.register_by_parcel(None, None, None)) == 0


def _table():
    return pd.DataFrame({
        "pnu": [P1, P2, P3, P4, None],
        "struct_t0_m2": [300.0, 60.0, 80.0, 5.0, 90.0],  # 4번째는 구조물 없음, 마지막은 PNU 없음
        "registered_m2": [250.0, 60.0, 80.0, 0.0, 0.0],
        "linear_jimok": [False, False, True, False, False],
    })


def test_attach_register_checks():
    reg = pd.DataFrame({"register_n": [1, 1], "register_arch_m2": [100.0, 50.0], "register_purpose": ["", ""],
                        "register_multi_lot": [False, False]}, index=reference.key9(pd.Series([P1, P2])))
    t = reference.attach_register(_table(), reg, "t0", {"min_struct_m2": 10})
    # 300 > max(100x2, 100+50) → 면적 초과 / 60 <= max(100, 100) → 있음 / 대장 없음 / 구조물 없음 / PNU 없음은 대조 안 함
    assert t["register_check"].tolist() == ["대장 면적 초과", "대장 있음", "대장 없음", "", ""]
    # 여러 필지에 걸친 대장은 면적을 비교하지 않는다
    reg.loc[reg.index[0], "register_multi_lot"] = True
    assert reference.attach_register(_table(), reg, "t0", {"min_struct_m2": 10})["register_check"].iloc[0] == "대장 있음"


def test_appendices_split_by_unregistered_class(tmp_path):
    g = gpd.GeoDataFrame({
        "pnu": [P2, P1, P3, P4], "addr": "a", "jimok": ["전", "답", "대", "도"],
        "unregistered_conf": ["독립", "대장 없음", "부속 의심", "저신뢰"],
        "unregistered_n": [1, 2, 1, 1], "unregistered_standalone_n": [1, 0, 0, 1], "unregistered_noreg_n": [0, 2, 0, 0],
        "unregistered_m2": [30.0, 50.0, 25.0, 20.0], "registered_m2": [0.0, 0.0, 80.0, 0.0], "mapped_m2": [0.0, 60.0, 80.0, 0.0],
        "violt_n": [0, 1, 0, 0], "status_t0": "x",
    }, geometry=[box(i, 0, i + 1, 1) for i in range(4)], crs="EPSG:5186")
    assert appendix1(g)["pnu"].tolist() == [P2] and appendix1b(g)["pnu"].tolist() == [P1]
    write_appendix1_shp(g, "status_t0", tmp_path / "b", name="b", rows=appendix1b(g), title="붙임 1-2")
    s = gpd.read_file(tmp_path / "b" / "b.shp")
    assert {"N_NOMAP", "N_NOREG", "MAP_M2", "VIOLT_N"} <= set(s.columns)
    assert s["N_NOREG"].tolist() == [2] and s["VIOLT_N"].tolist() == [1] and s["SEQ"].tolist() == [1]
    assert (tmp_path / "b" / "필드설명.txt").read_text(encoding="utf-8-sig").startswith("붙임 1-2")
    assert len(appendix1b(g.drop(columns="unregistered_conf"))) == 0  # 이전 결과(열 없음)


def test_match_register_by_parcel_area_date():
    gis = pd.DataFrame({"pnu": [P1, P1, P2], "ar": [100.0, 40.0, 70.0], "totar": [200.0, 40.0, 70.0],
                        "use_confm_de": ["2001-05-01", "1999-01-01", "2010-01-01"]})
    title = pd.DataFrame({"k": reference.key9(pd.Series([P1, P1, P2])), "arch": [100.0, 40.2, 10.0], "tot": [200.0, 40.0, 10.0],
                          "d": ["20010501", "19990101", "20100101"], "mgmBldrgstPk": ["11", "12", "13"],
                          "mainPurpsCdNm": ["주택", "창고", "공장"]})
    m = buildings_ref.match_register(gis, title)
    # 0: 셋 다 일치 → 11, 1: 연면적·날짜 일치(건축면적 0.2 차이) → 12, 2: 날짜만 일치(점수 1) → 없음
    assert m["reg_pk"].to_dict() == {0: "11", 1: "12"} and m.loc[0, "reg_purpose"] == "주택"


def test_unregistered_tiers_split_mapped_and_attached():
    from rasterio.transform import from_origin
    res, tr = 0.5, from_origin(0, 100, 0.5, 0.5)
    p = np.zeros((200, 200), np.float32)
    p[20:40, 20:40] = 0.9      # 어느 윤곽에도 없음 → U
    p[100:120, 100:120] = 0.9  # 대장 미연계 윤곽 위 → R
    unreg_map = np.zeros_like(p, bool); unreg_map[98:122, 98:122] = True
    near = np.zeros_like(p, bool)
    cfg = {"prob_hi": 0.6, "min_object_m2": 20, "min_rect_fill": 0.5, "min_struct_m2": 10, "tolerance_m": 1.0}
    o = parcels.unregistered_objects(p, near, cfg, res, tr, "EPSG:5186", fp=np.zeros_like(near), unreg_map=unreg_map,
                                     violt_map=unreg_map)
    o = gpd.GeoDataFrame(o, geometry="geometry", crs="EPSG:5186")
    cad = gpd.GeoDataFrame({"pnu": [P1, P2], "addr": "a", "jibun": ["1 전", "2 전"]},
                           geometry=[box(0, 70, 30, 100), box(30, 0, 100, 70)], crs="EPSG:5186")
    profile = {"height": 200, "width": 200, "transform": tr}
    t, objs = parcels.parcel_table(cad, {"t0": p, "t2": p}, profile, res, gpd.GeoDataFrame(geometry=[], crs="EPSG:5186"),
                                   o, cfg, "t0", "t2", masks=(np.zeros_like(near), near), map_fp=unreg_map)
    assert sorted(objs["tier"]) == ["R", "U"]
    assert t["unregistered_conf"].tolist() == ["독립", "대장 없음"]
    assert objs.loc[objs["tier"] == "R", "violt_frac"].iloc[0] > 0.9 and t["mapped_m2"].iloc[1] > 0


def test_screen_labels_exclusion_and_summary(tmp_path):
    cfg = {"interpretation": {"out_dir": str(tmp_path)}}
    p = screen.path(cfg)
    p.parent.mkdir(parents=True)
    pd.DataFrame({"sample_id": ["U001", "A01", "G01", "A02"], "stratum": ["screen_U", "screen_A", "screen_G", "screen_A"],
                  "kind": ["미등재(독립)", "신규 의심", "소멸 의심", "신규 의심"], "pnu": [P1, P2, P3, P4],
                  "label": ["structure", "not_structure", "uncertain", ""], "reason": ["", "그림자 위치 변화", "", ""]}
                 ).to_csv(p, index=False, encoding="utf-8-sig")
    assert screen.excluded(cfg) == {("A01", P2)}
    s = screen.summary(cfg)
    assert (s["total"], s["done"], s["structure"], s["not_structure"], s["uncertain"]) == (4, 3, 1, 1, 1)
    assert s["by_kind"]["신규 의심"] == {"total": 2, "structure": 0, "not_structure": 1, "uncertain": 0}
    assert s["reasons"] == {"그림자 위치 변화": 1}


def test_screen_without_sheet():
    cfg = {"interpretation": {"out_dir": "Z:/없는/폴더"}}
    assert screen.excluded(cfg) == set() and screen.summary(cfg) is None


def test_followup_commands_need_no_images():
    base = dict(report_only=False, survey_export=False, survey_import=None, sheets=False, screen_export=False)
    assert not addin_run._followup(Namespace(**base))
    for k, v in [("report_only", True), ("survey_import", "x.xlsx"), ("screen_export", True)]:
        assert addin_run._followup(Namespace(**{**base, k: v}))
