"""물건조사 1차 선별용 '쉬운' 보고서(단일 HTML).

목적: 어느 필지를 고해상도 영상/현장에서 먼저 확인할지 정한다. 지장물의 위치·형태·규모나 연도별 생성·변경·소멸 이력을
위성영상(10~20m)으로 확인하는 자료가 아니다. 이 한계는 보고서에 그대로 적는다.
"""

from __future__ import annotations

import html as _html
import json
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

from . import parcel_statistics as ps
from . import temporal
from .buildings import link_to_parcels, load_buildings
from .reporting import _lines_d, _num, _path_d

TITLE = "필지별 변화 흔적과 현재 건축물 — 물건조사 1차 선별 자료"
TEMPLATE = Path(__file__).with_name("report_simple_template.html")
STATUS_KO = {"none": "변화 없음", "veg_loss": "식생 감소", "exposed": "맨땅 증가", "recovery": "식생 회복", "na": "판단 불가"}
TIER_KO = {1: "먼저 확인", 2: "확인 권장", 3: "변화 흔적 없음", 4: "판단 보류"}


def _interval_status(base: dict, ext: dict, parcels, clipped, cfg) -> tuple[list[list[str]], pd.DataFrame]:
    nc = cfg["ndvi"]
    years = base["years"]
    K = len(years) - 1
    n = len(parcels)
    px = temporal.analyze_pixels(base["ndvi"], nc["thresholds"]["default"], nc["min_followup_obs"], nc["min_valid_obs_pixel"])
    wt10 = ps.pixel_weights(list(clipped.values), base["transform"], base["ndvi"].shape[1:])
    wt20 = ps.pixel_weights(list(clipped.values), ext["stack"]["transform"], ext["stack"]["shape"])
    comp = ext["comp"]
    area = parcels["area_in_aoi_m2"].to_numpy(float)

    def a(wt, mask):
        return np.bincount(wt["p"], weights=wt["a"] * mask.ravel()[wt["f"]], minlength=n)

    ev10 = a(wt10, px["evaluable"])
    ev20 = a(wt20, comp["evaluable"])
    status = [["na"] * K for _ in range(n)]
    amounts = np.zeros((n, K, 3))     # 식생 감소(10m) / 맨땅 증가(20m) / 식생 회복(20m) 면적
    for k in range(K):
        d10 = a(wt10, px["events"][k])
        ex = a(wt20, comp["events"][k] == 2)
        lo = a(wt20, comp["events"][k] == 1)
        rc = a(wt20, comp["events"][k] == 3)
        amounts[:, k, 0], amounts[:, k, 1], amounts[:, k, 2] = np.maximum(d10, lo), ex, rc
        for i in range(n):
            ok10 = ev10[i] >= 400
            ok20 = ev20[i] >= 800
            if not ok10 and not ok20:
                continue
            thr20 = max(200.0, 0.10 * ev20[i])
            thr10 = max(200.0, 0.10 * ev10[i])
            if ok20 and ex[i] >= thr20:
                status[i][k] = "exposed"
            elif (ok10 and d10[i] >= thr10) or (ok20 and lo[i] >= thr20):
                status[i][k] = "veg_loss"
            elif ok20 and rc[i] >= thr20:
                status[i][k] = "recovery"
            else:
                status[i][k] = "none"
    detail = pd.DataFrame({"ev10": ev10, "ev20": ev20})
    return status, detail, amounts


def build_simple_report(base: dict, ext: dict, cfg: dict, out_root: Path, offline: bool = False) -> list[str]:
    g = ext["parcels"].reset_index(drop=True)
    years = base["years"]
    intervals = [f"{years[i]}→{years[i + 1]}" for i in range(len(years) - 1)]
    aoi = base["aoi"]
    aoi_u = aoi.geometry.union_all()
    clipped = g.geometry.intersection(aoi_u)

    status, det, amounts = _interval_status(base, ext, g, clipped, cfg)
    bcfg = cfg["bsi"].get("context", {})
    bld, bmeta = load_buildings(aoi, bcfg.get("cache_dir", "data/projects/kr_rail_danyang/context"),
                                offline=offline)
    link = link_to_parcels(bld, g)

    tr = base["transform"]
    x0, y0 = tr.c, tr.f
    H, W = base["ndvi"].shape[1:]
    robust = set()
    rp = Path("outputs/kr_rail_danyang/composite/tables/robust_candidates.csv")
    if rp.exists():
        robust = set(pd.read_csv(rp, dtype={"pnu": str}, encoding="utf-8-sig")["pnu"])

    rows, paths = [], []
    for i, r in g.iterrows():
        st = status[i]
        changed = [k for k, s in enumerate(st) if s in ("exposed", "veg_loss", "recovery")]
        has_exposed = "exposed" in st
        persistent = r["change_type"] == "PERSISTENT_DECLINE"
        all_na = all(s == "na" for s in st)
        if all_na:
            tier = 4
        elif has_exposed or persistent:
            tier = 1
        elif changed:
            tier = 2
        else:
            tier = 3
        # 쉬운 한 줄 판단
        parts = []
        for k in changed:
            s = st[k]
            what = {"exposed": "맨땅이 늘어난 흔적", "veg_loss": "식생이 줄어든 흔적", "recovery": "식생이 회복된 흔적"}[s]
            a_ = amounts[i, k, {"veg_loss": 0, "exposed": 1, "recovery": 2}[s]]
            parts.append(f"{years[k]}년과 {years[k + 1]}년 영상 사이에 {what}(약 {a_:,.0f}㎡)")
        if tier == 4:
            head = "필지가 작거나 영상이 부족해 위성영상으로는 판단하기 어렵습니다."
        elif parts:
            head = "; ".join(parts[:3]) + (f" 외 {len(parts) - 3}개 시기에서도 변화 흔적." if len(parts) > 3 else ".")
        else:
            head = "비교한 어느 시기에서도 뚜렷한 변화 흔적이 없습니다."
        nb, bf = int(link.at[i, "bld_count"]), float(link.at[i, "bld_footprint_m2"])
        bld_txt = f"현재 건축물 {nb}동(윤곽 합계 약 {bf:,.0f}㎡)이 있습니다." if nb else "현재 건축물 윤곽 자료는 없습니다(가설건축물·비닐하우스 등은 이 자료에 없을 수 있음)."
        action = {1: "고해상도 영상 또는 현장에서 먼저 확인하세요.", 2: "필요 시 고해상도 영상으로 확인하세요.",
                  3: "위성영상상 변화 흔적은 없습니다. (지장물이 없다는 뜻은 아닙니다.)", 4: "위성영상으로는 판단할 수 없어 별도 확인이 필요합니다."}[tier]
        rows.append({
            "pnu": None if pd.isna(r["pnu"]) else str(r["pnu"]), "j": r["jibun_display"], "ad": r["addr"], "jm": r.get("jimok"),
            "a": _num(r["area_in_aoi_m2"], 0), "st": st, "tier": tier,
            "am": [[_num(v, 0) for v in amounts[i, k]] for k in range(amounts.shape[1])],
            "nb": nb, "bf": _num(bf, 0), "bn": link.at[i, "bld_names"], "bi": link.at[i, "bld_idx"],
            "head": head, "bld": bld_txt, "act": action,
            "rb": (str(r["pnu"]) in robust), "qc": r["quarry_context"], "ag": bool(r.get("agri_context", False)),
            "rd": _num(r["rail_distance_m"], 0),
            "sc": _num(float(amounts[i, :, 1].sum() + amounts[i, :, 0].sum() * 0.5), 0),
        })
        paths.append(_path_d(r.geometry, x0, y0))
    bpaths = [_path_d(geom, x0, y0) for geom in bld.geometry]
    bnames = [None if pd.isna(v) else str(v) for v in bld.get("buld_nm", pd.Series([None] * len(bld)))]
    bfl = [int(v) if pd.notna(v) else None for v in bld.get("gro_flo_co", pd.Series([None] * len(bld)))]
    bfp = [_num(v, 0) for v in bld["footprint_m2"]] if len(bld) else []

    cnt = {t: sum(1 for r in rows if r["tier"] == t) for t in (1, 2, 3, 4)}
    aoi_b = aoi.total_bounds
    data = {
        "title": TITLE, "years": years, "intervals": intervals, "W": W * tr.a, "H": H * tr.a,
        "bbox": [aoi_b[0] - x0, y0 - aoi_b[3], aoi_b[2] - x0, y0 - aoi_b[1]],
        "parcels": rows, "paths": paths, "aoi_path": "".join(_path_d(gm, x0, y0) for gm in aoi.geometry),
        "rail_path": _lines_d(base["rail"], x0, y0), "b_paths": bpaths, "b_names": bnames, "b_floors": bfl, "b_fp": bfp,
        "status_ko": STATUS_KO, "tier_ko": TIER_KO, "counts": cnt,
        "n_parcels": len(rows), "n_buildings": int(len(bld)), "n_parcels_with_bld": int((link["bld_count"] > 0).sum()),
        "n_robust_tier1": sum(1 for r in rows if r["tier"] == 1 and r["rb"]),
        "bld_meta": bmeta, "generated": ext["meta"]["generated_at"],
        "detail_report": "kr_rail_danyang_ndvi_bsi_quarry_report.html",
    }
    tpl = TEMPLATE.read_text(encoding="utf-8")
    html = (tpl.replace("__TITLE__", _html.escape(TITLE))
            .replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")))
    outp = Path(cfg["bsi"]["output_dir"]) / "report" / "kr_rail_danyang_survey_screening_report.html"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(html, encoding="utf-8")
    # 필지별 CSV(쉬운 컬럼)
    tab = Path(cfg["bsi"]["output_dir"]) / "tables" / "survey_screening_parcels.csv"
    out = pd.DataFrame({
        "지번": [r["j"] for r in rows], "PNU": [r["pnu"] for r in rows], "주소": [r["ad"] for r in rows],
        "AOI내_면적_㎡": [r["a"] for r in rows], "확인_구분": [TIER_KO[r["tier"]] for r in rows],
        **{f"{intervals[k]}": [STATUS_KO[r["st"][k]] for r in rows] for k in range(len(intervals))},
        "현재_건축물_수": [r["nb"] for r in rows], "현재_건축물_윤곽합_㎡": [r["bf"] for r in rows],
        "한줄_판단": [r["head"] for r in rows], "권고": [r["act"] for r in rows],
        "두방법_모두_확인": [r["rb"] for r in rows], "채석장_맥락": [r["qc"] for r in rows], "농경지_맥락": [r["ag"] for r in rows]})
    out.sort_values(["확인_구분"], key=lambda s: s.map({v: k for k, v in TIER_KO.items()}), kind="stable").to_csv(
        tab, index=False, encoding="utf-8-sig")
    if len(bld):
        bld_out = bld.copy()
        bld_out.to_file(Path(cfg["bsi"]["output_dir"]) / "vectors" / "buildings_current_spbd.gpkg", layer="buildings", driver="GPKG")
    return [str(outp), str(tab)]
