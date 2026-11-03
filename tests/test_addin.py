"""ArcGIS Pro Add-in 실행 경로(src.interpretation.ingest, addin_run, 보고서 회차 선택) 테스트."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import yaml
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import box

from src.evaluation.generate_interpretation_official import _latest, _rn
from src.interpretation import addin_run, ingest

ORIGIN = (190000.0, 540000.0)


def _texture(h: int, w: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    g = ndimage.gaussian_filter(rng.random((h, w)), 3)
    g = (g - g.min()) / (g.max() - g.min()) * 200 + 30
    return np.stack([g, g * 0.9, g * 0.8]).astype(np.uint8)


def _write(path: Path, a: np.ndarray, transform: Affine | None, crs: str | None = "EPSG:5186") -> Path:
    prof = {"driver": "GTiff", "dtype": "uint8", "count": a.shape[0], "height": a.shape[1], "width": a.shape[2]}
    if transform is not None:
        prof["transform"] = transform
    if crs:
        prof["crs"] = crs
    with rasterio.open(path, "w", **prof) as d:
        d.write(a)
    return path


def _aoi(bounds) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[box(*bounds)], crs="EPSG:5186")


def test_georef_state(tmp_path):
    a = _texture(64, 64)
    t = Affine(0.25, 0, ORIGIN[0], 0, -0.25, ORIGIN[1])
    aoi = (ORIGIN[0] - 10, ORIGIN[1] - 30, ORIGIN[0] + 30, ORIGIN[1] + 10)
    cases = {"crs": _write(tmp_path / "a.tif", a, t), "coords": _write(tmp_path / "b.tif", a, t, crs=None),
             "none": _write(tmp_path / "c.tif", a, None, crs=None)}
    for want, p in cases.items():
        with rasterio.open(p) as d:
            assert ingest.georef_state(d, aoi) == want
    with rasterio.open(cases["coords"]) as d:  # 좌표가 지구와 안 겹치면 좌표 없음으로 본다
        assert ingest.georef_state(d, (0, 0, 10, 10)) == "none"


def test_pixel_m_units(tmp_path):
    a = _texture(16, 16)
    geo = _write(tmp_path / "geo.tif", a, Affine(1e-5, 0, 126.85, 0, -1e-5, 37.45), crs="EPSG:4326")
    proj = _write(tmp_path / "proj.tif", a, Affine(0.25, 0, ORIGIN[0], 0, -0.25, ORIGIN[1]))
    bare = _write(tmp_path / "bare.tif", a, None, crs=None)
    with rasterio.open(geo) as g, rasterio.open(proj) as p, rasterio.open(bare) as b:
        assert 0.85 < ingest.pixel_m(g) < 0.92  # 1e-5도 ≈ 위도 37.45°에서 0.88m
        assert abs(ingest.pixel_m(p) - 0.25) < 1e-9
        assert ingest.pixel_m(b) is None


def test_gdal_convert_downscales_by_ratio(tmp_path):
    """Pro 파이썬 형식 변환: 경위도 원본도 미터 기준 배율로 줄이고 좌표는 원본 그대로 둔다."""
    import json
    import subprocess
    import pytest
    pro = ingest.pro_python()
    if pro is None:
        pytest.skip("ArcGIS Pro 파이썬 없음")
    src = _write(tmp_path / "geo.tif", _texture(400, 400), Affine(1e-6, 0, 126.85, 0, -1e-6, 37.45), crs="EPSG:4326")
    dst = tmp_path / "out.tif"
    p = subprocess.run([str(pro), "-I", str(ingest.CONVERT), str(src), str(dst), "0.25"], capture_output=True, text=True,
                       encoding="utf-8", env=ingest._clean_env())
    assert p.returncode == 0, p.stderr
    info = json.loads(p.stdout.strip().splitlines()[-1])
    assert info["georef"] and 0.08 < info["src_res"] < 0.1
    with rasterio.open(dst) as d:
        assert d.width < 160 and d.count == 3 and d.crs.to_epsg() == 4326  # 0.088m → 0.25m: 약 1/2.8
        assert abs(d.bounds.left - 126.85) < 1e-9 and abs(d.bounds.top - 37.45) < 1e-9


def test_tile_shifts_sign_restores_alignment(tmp_path):
    """영상 좌표가 동쪽 1m·남쪽 0.5m 어긋나 있으면 (dx, dy) = (-1, +0.5)로 되돌린다."""
    a = _texture(1200, 1200)
    t = Affine(0.25, 0, ORIGIN[0], 0, -0.25, ORIGIN[1])
    ref = _write(tmp_path / "ref.tif", a, t)
    moved = _write(tmp_path / "moved.tif", a, Affine.translation(1.0, -0.5) * t)
    aoi = _aoi((ORIGIN[0] + 20, ORIGIN[1] - 280, ORIGIN[0] + 280, ORIGIN[1] - 20))
    sh = ingest.tile_shifts(str(moved), str(ref), aoi, step_m=60)
    assert len(sh) >= 4
    f = ingest.fine_shift(sh)
    assert abs(f["dx"] + 1.0) < 0.05 and abs(f["dy"] - 0.5) < 0.05
    assert f["rmse_m"] < 0.05


def test_fine_shift_drops_outliers():
    sh = np.array([[0.5, -0.2, 0]] * 10 + [[6.0, 4.0, 0], [-5.0, 3.0, 0]], dtype=float)
    f = ingest.fine_shift(sh)
    assert f["n"] == 10 and f["tiles"] == 12
    assert abs(f["dx"] - 0.5) < 1e-9 and abs(f["dy"] + 0.2) < 1e-9


def test_coarse_register_recovers_transform(tmp_path):
    """좌표 없는 잘라낸 영상의 화소→지도 변환을 기준 영상에서 찾아낸다."""
    a = _texture(1600, 1600, seed=3)
    t = Affine(0.5, 0, ORIGIN[0], 0, -0.5, ORIGIN[1])
    ref = _write(tmp_path / "ref.tif", a, t)
    r0, c0 = 300, 400
    src = _write(tmp_path / "src.tif", a[:, r0:r0 + 1000, c0:c0 + 1000], None, crs=None)
    bounds = (ORIGIN[0], ORIGIN[1] - 800, ORIGIN[0] + 800, ORIGIN[1])
    full, stats = ingest.coarse_register(str(src), str(ref), bounds, res=2.0, log=lambda *_: None)
    x, y = full * (0, 0)
    assert abs(x - (ORIGIN[0] + c0 * 0.5)) < 1.0 and abs(y - (ORIGIN[1] - r0 * 0.5)) < 1.0
    assert abs(stats["pixel_m"] - 0.5) < 0.01


def test_window_bounds():
    assert addin_run.window_start("2022-12-01") == "2022-12-01"
    assert addin_run.window_start("2024") == "2024-01-01"
    assert addin_run.window_end("2024") == "2024-12-31"
    assert addin_run.window_end("2024-02") == "2024-02-29"
    assert addin_run.window_end("2024-05-10") == "2024-05-10"


def test_derive_config_keeps_repo_data_and_isolates_outputs(tmp_path):
    base = yaml.safe_load(addin_run.PROJECT.read_text(encoding="utf-8"))
    imgs = {e: {"path": str(tmp_path / f"{e}.tif"), "source": f"C:/x/{e}.ecw", "spec": f"spec {e}"} for e in ("t0", "t2")}
    cfg = addin_run.derive_config(base, tmp_path, imgs, {"t0": "2023-03-01", "t2": "2025"}, change=True)
    for k in ("aoi", "buildings", "cadastre"):
        assert Path(cfg["paths"][k]).is_absolute() and Path(cfg["paths"][k]).exists()
    assert cfg["paths"]["output_root"] == str(tmp_path)
    assert Path(cfg["interpretation"]["out_dir"]).parent == tmp_path
    assert cfg["interpretation"]["qa"]["round"] == addin_run.REVIEW_ROUND
    assert "report_notes" not in cfg["interpretation"]
    assert cfg["interpretation"]["reference_window"] == ["2023-03-01", "2025-12-31"]
    assert [e["spec"] for e in cfg["imagery"]["epochs"]] == ["spec t0", "spec t2"]
    assert base["interpretation"]["qa"]["round"] == "round4"  # 원 설정은 그대로
    assert addin_run.derive_config(base, tmp_path, imgs, {"t0": "2023", "t2": "2025"}, change=False)["analysis"]["comparison_pairs"] == []


def test_reuse_matches_repo_imagery():
    base = yaml.safe_load(addin_run.PROJECT.read_text(encoding="utf-8"))
    e = base["imagery"]["epochs"][1]
    hit = addin_run.reuse(str(addin_run.ROOT / e["original"]), base)
    assert hit and hit["reused_epoch"] == e["id"] and hit["spec"] == e["spec"]
    assert addin_run.reuse(str(addin_run.ROOT / "README.md"), base) is None


def test_appendix1_shapefile_matches_table(tmp_path):
    from src.interpretation.deliver import appendix1, write_appendix1_shp
    g = gpd.GeoDataFrame({
        "pnu": ["4121010300100020000", "4121010300100010000", "4121010300100030000"],
        "addr": ["경기도 광명시 노온사동 2", "경기도 광명시 노온사동 1", "경기도 광명시 노온사동 3"],
        "jimok": ["전", "답", "대"], "unregistered_conf": ["독립", "독립", "부속 의심"],
        "unregistered_n": [2, 1, 1], "unregistered_m2": [55.25, 30.0, 12.0], "registered_m2": [0.0, 80.0, 40.0],
        "status_t0": ["있음(판독만)", "있음(등재+판독)", "있음(등재+판독)"],
    }, geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(20, 0, 30, 10)], crs="EPSG:5186")
    assert appendix1(g)["pnu"].tolist() == ["4121010300100010000", "4121010300100020000"]  # 독립만, PNU순
    z = write_appendix1_shp(g, "status_t0", tmp_path / "a1")
    t = gpd.read_file(tmp_path / "a1" / "appendix1_unregistered_parcels.shp")
    assert t["SEQ"].tolist() == [1, 2] and t["PNU"].tolist() == appendix1(g)["pnu"].tolist()
    assert t["ADDR"].iloc[0] == "경기도 광명시 노온사동 1" and t["STATUS"].iloc[1] == "있음(판독만)"
    assert t.crs.to_epsg() == 5186 and abs(t["PARCEL_M2"].iloc[0] - 100) < 1e-6
    assert (tmp_path / "a1" / "appendix1_unregistered_parcels.cpg").read_text().strip().upper().replace("-", "") == "UTF8"
    assert z.exists()


def test_file_hash_memo_and_analysis_key(tmp_path):
    f = tmp_path / "img.bin"
    f.write_bytes(b"abc" * 1000)
    memo = tmp_path / "memo.json"
    h = addin_run.file_hash(f, memo)
    assert len(h) == 64 and memo.exists()
    assert addin_run.file_hash(f, memo) == h  # 기억한 값
    f.write_bytes(b"abd" * 1000)
    assert addin_run.file_hash(f, memo) != h  # 내용이 바뀌면 다시 계산
    base = yaml.safe_load(addin_run.PROJECT.read_text(encoding="utf-8"))
    k = addin_run.analysis_key(["a", "b"], base, ["d"], True)
    assert k == addin_run.analysis_key(["a", "b"], base, ["d"], True)
    assert k != addin_run.analysis_key(["a", "c"], base, ["d"], True)
    assert k != addin_run.analysis_key(["a", "b"], base, ["d"], False)
    other = yaml.safe_load(addin_run.PROJECT.read_text(encoding="utf-8"))
    other["interpretation"]["qa"]["round"] = "x"  # 검수 설정은 분석 결과에 영향 없음
    assert addin_run.analysis_key(["a", "b"], other, ["d"], True) == k
    other["interpretation"]["segmentation"]["scale"] = 1
    assert addin_run.analysis_key(["a", "b"], other, ["d"], True) != k


def test_link_shares_file(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x")
    addin_run._link(a, tmp_path / "sub" / "b.txt")
    assert (tmp_path / "sub" / "b.txt").read_text() == "x"


def test_survey_import_and_summary(tmp_path):
    from openpyxl import Workbook
    from src.interpretation import survey
    wb = Workbook()
    ws = wb.active
    ws.title = "조사표"
    ws.append(["조사번호", "구분", "PNU", "소재지", "지목", "판독 면적(㎡)", "위도", "경도", "근거 영상"] + [c for c, _ in survey.RESULT_FIELDS.values()])
    rows = [("U001", "미등재(독립)", "있음", "창고", "아니오"), ("U002", "미등재(독립)", "없음", "", ""),
            ("U003", "미등재(독립)", "있음", "주택", "예"), ("U004", "미등재(독립)", "", "", ""), ("G01", "소멸 의심", "확인불가", "", "")]
    for sid, kind, e, u, r in rows:
        ws.append([sid, kind, "1", "", "", 1, 0, 0, "", e, u, r, "홍길동", "", "", ""])
    x = tmp_path / "s.xlsx"
    wb.save(x)
    cfg = {"interpretation": {"out_dir": str(tmp_path / "interp")}}
    out = survey.import_results(cfg, x, log=lambda *_: None)
    s = survey.summarize(out).set_index("kind")
    u = s.loc["미등재(독립)"]
    assert (u.targets, u.surveyed, u.exists, u.absent, u.missing_in_register) == (4, 3, 2, 1, 1)
    assert abs(u.precision - 2 / 3) < 1e-9
    g = s.loc["소멸 의심"]
    assert g.unknown == 1 and g.precision != g.precision  # 판단 가능 0건이면 NaN


def test_round_selection_for_report():
    lab = lambda rows: pd.DataFrame(rows, columns=["stratum", "label"])
    rounds = {"round3": lab([("new_A", "structure"), ("unreg_대", "structure")]),
              "round4": lab([("unreg_대", "not_structure"), ("unreg_attached", "structure")]),
              "review1": lab([("unreg_대", None)])}
    rounds = dict(sorted(rounds.items()))
    assert _latest(rounds, lambda q: q["stratum"].eq("new_A")) == "round3"
    assert _latest(rounds, lambda q: q["stratum"].eq("unreg_대")) == "round4"  # 라벨 없는 review1은 건너뜀
    assert _latest(rounds, lambda q: q["stratum"].eq("gone_AB")) is None
    assert _rn("round4") == "4차" and _rn("review1") == "검수 1차" and _rn(None) == "-"
