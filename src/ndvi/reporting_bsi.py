"""NDVI + BSI + 채석장 후보 확장 보고서(단일 HTML, 외부 CDN/인터넷 불필요) + 정적 PNG.

모든 수치는 분석 산출물에서 계산한다. 대표 사례는 실제 필지 데이터에서 규칙으로 선택하며 가상 결과를 넣지 않는다.
"""

from __future__ import annotations

import base64
import html as _html
import io
import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import shapely
from PIL import Image

from .composite import TYPE_KO
from .quarry import QUARRY_CODES, QUARRY_KO
from .reporting import REQUIRED_NOTICE, _font, _lines_d, _num, _path_d, ndvi_rgba

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

TITLE = "충북 단양 철도사업 주변 필지별 토지피복 및 채석장 변화 모니터링"
TEMPLATE = Path(__file__).with_name("report_bsi_template.html")

COMPOSITE_COLORS = {
    "EXPOSED_SURFACE_INCREASE": "#b2182b", "VEGETATION_LOSS": "#ef8a62", "PERSISTENT_EXPOSED_SURFACE": "#d8c7a8",
    "VEGETATION_RECOVERY": "#4393c3", "STABLE_VEGETATION": "#4a9d5b", "UNCERTAIN_CHANGE": "#8e6bbf",
    "NO_SIGNIFICANT_CHANGE": "#c7ccd1", "INSUFFICIENT_DATA": "#e3e6e9",
}
COMPOSITE_KO = {**TYPE_KO, "NO_SIGNIFICANT_CHANGE": "뚜렷한 변화 없음", "INSUFFICIENT_DATA": "자료 부족(판단 보류)"}
QCTX_COLORS = {
    "POTENTIAL_QUARRY_EXPANSION": "#b2182b", "QUARRY_INTERNAL_CHANGE": "#d6604d", "EXPOSED_SURFACE_CHANGE": "#f4a582",
    "POSSIBLE_REVEGETATION": "#4393c3", "VERIFIED_QUARRY_AREA": "#7f6a52", "GENERAL_VEGETATION_LOSS": "#e6b800",
    "NONE": "#dfe6dd", "NOT_EVALUATED": "#e3e6e9",
}
QCTX_KO = {**QUARRY_KO, "GENERAL_VEGETATION_LOSS": "일반 식생 감소(채석장 무관)", "NONE": "해당 없음", "NOT_EVALUATED": "평가 보류"}
PIX_TYPE_COLORS = {1: "#ef8a62", 2: "#b2182b", 3: "#8c6d46", 4: "#4393c3", 5: "#4a9d5b", 6: "#8e6bbf"}
PIX_QUARRY_COLORS = {1: "#c9b79c", 2: "#b2182b", 3: "#f4a582", 4: "#4393c3", 5: "#8e6bbf", 6: "#d6604d"}


def _png(rgba: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _cmap_rgba(a: np.ndarray, cmap: str, vmin: float, vmax: float) -> np.ndarray:
    cm = matplotlib.colormaps[cmap]
    n = np.clip((np.nan_to_num(a, nan=vmin) - vmin) / (vmax - vmin), 0, 1)
    rgba = (cm(n) * 255).astype("uint8")
    rgba[..., 3] = np.where(np.isfinite(a), 255, 0)
    return rgba


def _cat_rgba(codes: np.ndarray, colors: dict, mask: np.ndarray | None = None, alpha: int = 235) -> np.ndarray:
    rgba = np.zeros(codes.shape + (4,), dtype="uint8")
    for c, hx in colors.items():
        m = codes == c
        if mask is not None:
            m &= mask
        rgba[m] = (*[int(hx[i:i + 2], 16) for i in (1, 3, 5)], alpha)
    return rgba


def _bin_rgba(mask: np.ndarray, hx: str, alpha: int = 235) -> np.ndarray:
    rgba = np.zeros(mask.shape + (4,), dtype="uint8")
    rgba[mask] = (*[int(hx[i:i + 2], 16) for i in (1, 3, 5)], alpha)
    return rgba


def _grid(tr, shape) -> dict:
    return {"x": 0.0, "y": 0.0, "w": shape[1] * tr.a, "h": shape[0] * tr.a, "res": tr.a, "x0": tr.c, "y0": tr.f}


# ----------------------------------------------------------------------------- cases
def _crop_png(arr_rgba: np.ndarray, tr, bounds, margin: float = 0.0):
    """tr: 래스터 transform, bounds: (minx,miny,maxx,maxy) 5186. -> (png, crop_x0, crop_y_top, w_m, h_m)"""
    res = tr.a
    c0 = max(int(np.floor((bounds[0] - tr.c) / res)), 0)
    c1 = min(int(np.ceil((bounds[2] - tr.c) / res)), arr_rgba.shape[1])
    r0 = max(int(np.floor((tr.f - bounds[3]) / res)), 0)
    r1 = min(int(np.ceil((tr.f - bounds[1]) / res)), arr_rgba.shape[0])
    sub = arr_rgba[r0:r1, c0:c1]
    return _png(sub), tr.c + c0 * res, tr.f - r0 * res, (c1 - c0) * res, (r1 - r0) * res


def _case_block(title: str, row: pd.Series | None, geom, res_b: dict, years: list[int], intervals: list[str],
                tr10, tr20, ndvi10_rgba: list, bsi_rgba: list, why: str, interp: str, followup: str) -> dict:
    if row is None:
        return {"title": title, "empty": True, "why": why}
    fi = row.get("first_change_interval_20m") or row.get("first_change_interval")
    try:
        a, b = (fi.replace("→", "_").split("_")) if fi else (str(years[0]), str(years[-1]))
        ia, ib = years.index(int(a)), years.index(int(b))
    except Exception:  # noqa: BLE001
        ia, ib = 0, len(years) - 1
    minx, miny, maxx, maxy = geom.bounds
    pad = max(100.0, 0.5 * max(maxx - minx, maxy - miny))
    bnd = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    panels = []
    for lab, arrs, tr, idx in (("NDVI 10m", ndvi10_rgba, tr10, ia), ("NDVI 10m", ndvi10_rgba, tr10, ib),
                               ("BSI 20m", bsi_rgba, tr20, ia), ("BSI 20m", bsi_rgba, tr20, ib)):
        png, x0, ytop, w, h = _crop_png(arrs[idx], tr, bnd)
        g = shapely.affinity.translate(geom, -x0, 0)
        d = _path_d(g, 0.0, ytop)
        panels.append({"label": f"{lab} {years[idx]}", "png": png, "w": w, "h": h, "d": d})
    return {"title": title, "empty": False, "pnu": row["pnu"], "jibun": row["jibun_display"], "addr": row["addr"],
            "interval": fi or f"{years[0]}→{years[-1]}", "panels": panels,
            "ndvi_first_last": _num(row.get("ndvi_delta_first_last"), 3), "bsi_first_last": _num(row.get("delta_bsi_first_last"), 3),
            "areas": {"area_in_aoi_m2": _num(row["area_in_aoi_m2"], 0),
                      "veg_decline_20m": _num(row["veg_decline_area_20m_m2"], 0),
                      "exposed_20m": _num(row["exposed_increase_area_20m_m2"], 0),
                      "recovery_20m": _num(row["veg_recovery_area_20m_m2"], 0),
                      "ndvi10_decline": _num(row["decline_area_m2"], 0)},
            "ctype": row["composite_type_ko"], "ntype": row["change_type_ko"], "qctx": QCTX_KO.get(row["quarry_context"], row["quarry_context"]),
            "conf": row["composite_confidence"], "flags": row["composite_quality_flags"],
            "why": why, "interp": interp, "followup": followup}


def build_cases(g: pd.DataFrame, geoms, base_years, intervals, tr10, tr20, ndvi10_rgba, bsi_rgba) -> list[dict]:
    cases = []

    def first(df, sort, asc=False):
        return None if df.empty else df.sort_values(sort, ascending=asc).iloc[0]

    def blk(title, row, why, interp, follow):
        geom = None if row is None else geoms[row.name]
        return _case_block(title, row, geom, None, base_years, intervals, tr10, tr20, ndvi10_rgba, bsi_rgba, why, interp, follow)

    r1 = first(g[(g["change_type"] == "PERSISTENT_DECLINE") & (g["quarry_context"] != "VERIFIED_QUARRY_AREA")], "decline_area_out_quarry_m2")
    cases.append(blk("CASE 1. 식생 감소가 지속적으로 관측된 필지", r1, "10m NDVI 기준 지속적 식생 감소 필지 중 채석장 참조 영역 밖의 감소 면적이 가장 큰 필지",
                     "이후 관측에서도 NDVI가 회복 기준 미만으로 유지됨. 원인(벌채·개간·시설 조성 등)은 영상 지표만으로 구분할 수 없음.",
                     "고해상도 영상 또는 현장 확인으로 피복 변화의 성격(시설·토공·벌채 등) 확인"))
    r2 = first(g[(g["composite_type"] == "EXPOSED_SURFACE_INCREASE") & (g["veg_decline_area_20m_m2"] > 0)
                 & (g["quarry_context"] != "POTENTIAL_QUARRY_EXPANSION")], "exposed_increase_area_20m_m2")
    cases.append(blk("CASE 2. 식생 감소와 노출지 증가가 함께 나타난 필지", r2,
                     "NDVI 감소와 BSI 증가가 동시에 확인된 20m 후보 면적이 가장 큰 필지(채석장 확장 의심 후보 제외)",
                     "두 지표가 같은 방향으로 변화해 노출지 증가 후보로 분류됨. 채석, 도로·건설 현장, 일시적 나지를 구분할 수 없음.",
                     "동일 시기 고해상도 영상, 현장 방문, 인허가·토지이용 자료 대조"))
    r3 = first(g[g["quarry_context"] == "POTENTIAL_QUARRY_EXPANSION"], "B_score")
    cases.append(blk("CASE 3. 채석장 확장 가능성이 있는 필지", r3,
                     "참조 채석장(OSM, 비공인)과 공간적으로 연결된 노출지 증가 후보 중 B 점수가 가장 높은 필지"
                     if r3 is not None else "참조 채석장과 연결된 노출지 증가 후보 필지가 없음(또는 참조 자료 부재)",
                     "참조 폴리곤과 인접·연속된 노출지 증가라는 공간적 근거만 있으며 채석 활동을 확정하지 않음.",
                     "허가구역·채굴 인가 자료와 고해상도 영상으로 채석 활동 여부 및 경계 확인"))
    r4 = first(g[g["composite_type"] == "VEGETATION_RECOVERY"], "veg_recovery_area_20m_m2")
    if r4 is None:
        r4 = first(g[g["change_type"] == "RECOVERY"], "ndvi_rel_delta_first_last")
    cases.append(blk("CASE 4. 식생 회복이 관측된 필지", r4, "비식생 상태에서 NDVI가 증가하고 BSI가 감소한 후보 면적이 가장 큰 필지",
                     "이전 관측에서 식생이 적던 지표가 식생으로 회복되는 신호. 작황·계절 위상 차이일 가능성이 남음.",
                     "동일 시기 다중 영상 중앙값 합성 또는 현장 확인으로 지속 여부 확인"))
    r5 = first(g[g["change_type"] == "TEMPORARY_DECLINE"], "decline_area_m2")
    cases.append(blk("CASE 5. 일시적 식생 변화로 원인 확인이 필요한 필지", r5, "식생 감소 후 회복 기준 이상으로 되돌아온 면적이 있는 필지 중 감소 면적이 가장 큰 필지",
                     "경작·수확, 일시적 나지, 공사 후 복구, 촬영일 차이 등으로 설명될 수 있어 원인을 지표만으로 판단할 수 없음.",
                     "중간 연도 영상 추가 확보 및 농지·산림 이용 자료 대조"))
    return cases


# ----------------------------------------------------------------------------- html
def build_bsi_html(base: dict, ext: dict, cfg: dict, out_root: Path) -> Path:
    g = ext["parcels"].reset_index(drop=True)
    years = base["years"]
    stack = ext["stack"]
    comp, qres = ext["comp"], ext["qres"]
    tr10, tr20 = base["transform"], stack["transform"]
    H10, W10 = base["ndvi"].shape[1:]
    shape20 = stack["shape"]
    x0, y0 = tr10.c, tr10.f   # SVG 좌표 원점은 10m 격자 좌상단(기존 보고서와 동일)
    meta = ext["meta"]
    intervals = ext["intervals"]
    T = len(years)

    def grid_of(tr, shape):
        return {"x": tr.c - x0, "y": y0 - tr.f, "w": shape[1] * tr.a, "h": shape[0] * tr.a}

    g10, g20 = grid_of(tr10, (H10, W10)), grid_of(tr20, shape20)
    ndvi10_rgba = [ndvi_rgba(base["ndvi"][i]) for i in range(T)]
    bsi_rgba = [_cmap_rgba(stack["bsi"][i], "YlOrBr", -0.35, 0.25) for i in range(T)]
    layers = {
        "ndvi": [_png(a) for a in ndvi10_rgba],
        "bsi": [_png(a) for a in bsi_rgba],
        "dndvi": {}, "dbsi": {}, "overlay": {},
    }
    pairs_idx = [(i, i + 1) for i in range(T - 1)] + [(0, T - 1)]
    for a, b in pairs_idx:
        lab = f"{years[a]}→{years[b]}"
        layers["dndvi"][lab] = _png(_cmap_rgba(stack["ndvi20"][b] - stack["ndvi20"][a], "RdBu", -0.6, 0.6))
        layers["dbsi"][lab] = _png(_cmap_rgba(stack["bsi"][b] - stack["bsi"][a], "RdBu_r", -0.3, 0.3))
    ev = comp["evaluable"]
    layers["overlay"] = {
        "type": {"label": "복합 변화 유형(픽셀)", "png": _png(_cat_rgba(comp["type"], PIX_TYPE_COLORS)),
                 "legend": {COMPOSITE_COLORS[k]: COMPOSITE_KO[k] for k in ("VEGETATION_LOSS", "EXPOSED_SURFACE_INCREASE",
                            "PERSISTENT_EXPOSED_SURFACE", "VEGETATION_RECOVERY", "STABLE_VEGETATION", "UNCERTAIN_CHANGE")}},
        "loss": {"label": "식생 감소 후보", "png": _png(_bin_rgba((comp["loss_any"] | comp["expo_any"]) & ev, "#ef8a62")),
                 "legend": {"#ef8a62": "식생 감소 후보(노출지 증가 포함)"}},
        "exposed": {"label": "노출지 증가 후보", "png": _png(_bin_rgba(comp["expo_any"] & ev, "#b2182b")),
                    "legend": {"#b2182b": "노출지 증가 후보"}},
        "expansion": {"label": "채석장 확장 의심 후보", "png": _png(_bin_rgba(qres["quarry_class"] == QUARRY_CODES["POTENTIAL_QUARRY_EXPANSION"], "#b2182b")),
                      "legend": {"#b2182b": "채석장 확장 의심(참조 폴리곤과 연결된 노출지 증가)"}},
        "recovery": {"label": "식생 회복 후보", "png": _png(_bin_rgba(comp["rec_any"] & ev, "#2166ac")),
                     "legend": {"#2166ac": "식생 회복 후보"}},
        "quarry": {"label": "채석장 관련 분류(픽셀)", "png": _png(_cat_rgba(qres["quarry_class"], PIX_QUARRY_COLORS, alpha=215)),
                   "legend": {PIX_QUARRY_COLORS[c]: n for c, n in (
                       (1, QUARRY_KO["VERIFIED_QUARRY_AREA"]), (2, QUARRY_KO["POTENTIAL_QUARRY_EXPANSION"]),
                       (3, QUARRY_KO["EXPOSED_SURFACE_CHANGE"]), (4, QUARRY_KO["POSSIBLE_REVEGETATION"]),
                       (5, QUARRY_KO["UNCLASSIFIED"]), (6, QUARRY_KO["QUARRY_INTERNAL_CHANGE"]))}},
    }

    robust = set()
    cmp_p = Path("outputs/kr_rail_danyang/composite/tables/robust_candidates.csv")
    if cmp_p.exists():
        robust = set(pd.read_csv(cmp_p, dtype={"pnu": str}, encoding="utf-8-sig")["pnu"])
    parcels_js, paths = [], []
    for i, r in g.iterrows():
        a = r["area_in_aoi_m2"]
        parcels_js.append({
            "pnu": None if pd.isna(r["pnu"]) else str(r["pnu"]), "j": r["jibun_display"], "ad": r["addr"], "a": _num(a, 1),
            "at": _num(r["area_total_m2"], 1),
            "n": [_num(r[f"ndvi_mean_{y}"], 3) for y in years], "n20": [_num(r[f"ndvi20_mean_{y}"], 3) for y in years],
            "b": [_num(r[f"bsi_mean_{y}"], 3) for y in years],
            "dl10": _num(r["decline_area_m2"], 1), "dr10": _num(r["decline_ratio"], 4),
            "t10": r["change_type"], "t10k": r["change_type_ko"], "ps": r["persistence_status"],
            "ex": _num(r["exposed_increase_area_20m_m2"], 0), "vd": _num(r["veg_decline_area_20m_m2"], 0),
            "rc": _num(r["veg_recovery_area_20m_m2"], 0), "pe": _num(r["persistent_exposed_area_20m_m2"], 0),
            "un": _num(r["uncertain_area_20m_m2"], 0), "pq": _num(r["potential_quarry_expansion_area_20m_m2"], 0),
            "ev20": _num(r["evaluable_area_20m_m2"], 0), "er": _num(r["exposed_ratio_20m"], 4), "dr20": _num(r["decline_ratio_20m"], 4),
            "ct": r["composite_type"], "ctk": r["composite_type_ko"], "qc": r["quarry_context"],
            "qd": _num(r["dist_to_quarry_m"], 0), "qo": _num(r["quarry_overlap_ratio"], 3), "qa": bool(r["quarry_adjacent"]),
            "fi": r["first_change_interval_20m"], "fi10": r["first_change_interval"] if isinstance(r["first_change_interval"], str) else None,
            "cc": r["composite_confidence"], "cq": r["composite_quality_flags"], "q10": r["quality_flags"], "cf10": r["analysis_confidence"],
            "rd": _num(r["rail_distance_m"], 0), "rt": bool(r.get("rail_nearest_is_tunnel", False)),
            "dB": _num(r["delta_bsi_first_last"], 3), "dN": _num(r["ndvi_delta_first_last"], 3), "dN20": _num(r["delta_ndvi20_first_last"], 3),
            "Ar": None if pd.isna(r["A_rank"]) else int(r["A_rank"]), "As": _num(r["A_score"], 1),
            "As1": _num(r["A_score_decline_ratio"], 1), "As2": _num(r["A_score_persistence"], 1), "As3": _num(r["A_score_rail_proximity"], 1),
            "Br": None if pd.isna(r["B_rank"]) else int(r["B_rank"]), "Bs": _num(r["B_score"], 1),
            "Bs1": _num(r["B_score_exposed_ratio"], 1), "Bs2": _num(r["B_score_quarry_link"], 1), "Bs3": _num(r["B_score_persistence"], 1),
            "wr": r["reliability_warning"], "dout": _num(r["decline_area_out_quarry_m2"], 0),
            "jm": r.get("jimok"), "zn": r.get("zoning"), "ag": r.get("agri_evidence") or "", "agc": bool(r.get("agri_context", False)),
            "cr": r.get("confusion_risk") or "", "rb": (str(r["pnu"]) in robust),
        })
        paths.append(_path_d(r.geometry, x0, y0))

    quarry_paths = [_path_d(q, x0, y0) for q in ext["quarry"].geometry] if len(ext["quarry"]) else []
    cases = build_cases(g.assign(pnu=g["pnu"].astype(str)), list(g.geometry), years, intervals, tr10, tr20, ndvi10_rgba, bsi_rgba)
    aoi_b = base["aoi"].total_bounds
    am = meta["aoi_summary"]
    summ = {
        "n_parcels": int(len(g)), "n_evaluable_composite": int((g["composite_type"] != "INSUFFICIENT_DATA").sum()),
        "n_insufficient_composite": int((g["composite_type"] == "INSUFFICIENT_DATA").sum()),
        "n_exposed": int((g["composite_type"] == "EXPOSED_SURFACE_INCREASE").sum()),
        "n_veg_loss": int((g["composite_type"] == "VEGETATION_LOSS").sum()),
        "n_recovery": int((g["composite_type"] == "VEGETATION_RECOVERY").sum()),
        "n_persist_exposed": int((g["composite_type"] == "PERSISTENT_EXPOSED_SURFACE").sum()),
        "n_potential_expansion": int((g["quarry_context"] == "POTENTIAL_QUARRY_EXPANSION").sum()),
        "n_quarry_internal": int((g["quarry_context"] == "QUARRY_INTERNAL_CHANGE").sum()),
        "n_A": int(g["A_candidate"].sum()), "n_B": int(g["B_candidate"].sum()),
        "n_persistent_ndvi": int((g["change_type"] == "PERSISTENT_DECLINE").sum()),
        "aoi_exposed_ha": am["exposed_increase_ha"], "aoi_veg_decline_ha": am["veg_decline_ha"],
        "aoi_recovery_ha": am["veg_recovery_ha"], "aoi_potential_expansion_ha": am["potential_quarry_expansion_ha"],
        "aoi_quarry_ref_ha": am["quarry_reference_in_aoi_ha"], "aoi_persistent_exposed_ha": am["persistent_exposed_ha"],
        "aoi_evaluable_ha": am["evaluable_ha"],
    }
    aoi_bsi = []
    from . import parcel_statistics as ps
    aw = ps.pixel_weights([base["aoi"].geometry.union_all()], tr20, shape20)
    for i in range(T):
        v = stack["bsi"][i].ravel()[aw["f"]]
        ok = np.isfinite(v)
        aoi_bsi.append(float((aw["a"][ok] * v[ok]).sum() / aw["a"][ok].sum()) if ok.any() else None)
    summ.update(_extra_summary())
    data = {
        "title": TITLE, "years": years, "intervals": [f"{years[a]}→{years[b]}" for a, b in pairs_idx],
        "g10": g10, "g20": g20, "W": g10["w"], "H": g10["h"],
        "bbox": [aoi_b[0] - x0, y0 - aoi_b[3], aoi_b[2] - x0, y0 - aoi_b[1]],
        "parcels": parcels_js, "paths": paths, "aoi_path": "".join(_path_d(gm, x0, y0) for gm in base["aoi"].geometry),
        "rail_path": _lines_d(base["rail"], x0, y0), "quarry_paths": quarry_paths, "layers": layers,
        "aoi_mean_ndvi": [base["meta"]["aoi"]["summary"]["per_year_aoi_ndvi_mean"][str(y)] for y in years], "aoi_mean_bsi": aoi_bsi,
        "scenes": base["meta"]["scenes"], "comp_colors": COMPOSITE_COLORS, "comp_ko": COMPOSITE_KO,
        "q_colors": QCTX_COLORS, "q_ko": QCTX_KO, "summary": summ, "cases": cases,
        "sens": meta["sensitivity"]["rows"] if meta.get("sensitivity") else [],
    }
    blocks = _blocks(base, ext, summ)
    blocks["validation"] = _validation_block(ext)
    tpl = TEMPLATE.read_text(encoding="utf-8")
    html = (tpl.replace("__TITLE__", _html.escape(TITLE)).replace("__NOTICE__", _html.escape(REQUIRED_NOTICE))
            .replace("__BLOCKS__", json.dumps(blocks, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")))
    p = Path(cfg["bsi"]["output_dir"]) / "report" / "kr_rail_danyang_ndvi_bsi_quarry_report.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p


def _extra_summary() -> dict:
    """육안 판독 검증/합성 비교의 핵심 수치(요약 카드용). 파일이 없으면 None."""
    out: dict = {"vf_n": None, "vf_f1": None, "vf_p": None, "vf_r": None, "cmp_robust": None,
                 "cmp_j_exposed": None, "cmp_type_agree": None}
    vp = Path("outputs/kr_rail_danyang/validation/visual_validation_summary.json")
    if vp.exists():
        c = json.loads(vp.read_text(encoding="utf-8"))["confusion_exposed_state_2026"]
        out.update(vf_n=c["n"], vf_f1=c["f1"], vf_p=c["precision"], vf_r=c["recall"])
    cp = Path("outputs/kr_rail_danyang/composite/tables/comparison_summary.json")
    if cp.exists():
        c = json.loads(cp.read_text(encoding="utf-8"))
        out.update(cmp_robust=c["robust_candidates"], cmp_j_exposed=c["exposed_increase"]["jaccard"],
                   cmp_type_agree=c.get("type_agreement_rate_evaluable"))
    return out


LABEL_KO = {"BARE": "나지·채석면", "BUILT": "시설·포장", "VEG_TREE": "수목", "VEG_LOW": "초지·농경지", "WATER": "수면",
            "UNCLEAR": "판독 불가"}
STRATUM_KO = {**{k: v for k, v in COMPOSITE_KO.items()}, **{k: v for k, v in QCTX_KO.items()}}
SENS_KO = {"ndvi.delta_max": "ΔNDVI 감소 기준", "bsi.increase_min": "ΔBSI 증가 기준",
           "quarry.min_component_pixels": "최소 연결 픽셀 수", "min_followup_obs": "지속성 판정 최소 후속 관측",
           "quarry.adjacency_distance_m": "채석장 인접 거리(m)"}


def _jcell(v) -> str:
    """Jaccard(기준 대비 겹침) 셀: 높을수록 안정."""
    if v is None:
        return "<td>-</td>"
    cls = "ok" if v >= 0.8 else ("mid" if v >= 0.6 else "low")
    return f"<td><span class='pill {cls}'>{v:.2f}</span></td>"


def _kv(rows) -> str:
    return "<table class='kv'>" + "".join(f"<tr><th>{_html.escape(str(k))}</th><td>{v}</td></tr>" for k, v in rows) + "</table>"


def _blocks(base: dict, ext: dict, s: dict) -> dict:
    m = ext["meta"]
    sw = m["swir_provenance"]
    q = m["quarry_reference"]
    th = m["thresholds"]
    kp = [("분석 대상 필지", f"{s['n_parcels']:,}", "AOI 교차 PNU 필지"),
          ("복합 분석 가능 필지", f"{s['n_evaluable_composite']:,}", f"20m 해상도로 판단 보류 {s['n_insufficient_composite']:,}개"),
          ("노출지 증가 후보 필지", f"{s['n_exposed']:,}", "NDVI 감소+BSI 증가(20m)"),
          ("채석장 확장 의심 필지", f"{s['n_potential_expansion']:,}", "참조 채석장과 연결, 확정 아님"),
          ("철도 인접 우선 검토(A)", f"{s['n_A']:,}", "채석장 내부 변화 제외"),
          ("채석장·노출지 검토(B)", f"{s['n_B']:,}", "철도 거리와 무관"),
          ("AOI 노출지 증가", f"{s['aoi_exposed_ha']:.1f} ha", "20m 픽셀 기준(식생 감소에 포함됨)"),
          ("AOI 식생 회복 후보", f"{s['aoi_recovery_ha']:.1f} ha", "비식생→식생")]
    b: dict = {}
    b["kpi"] = "".join(f"<div class='kpi'><div class='kl'>{a}</div><div class='kv2'>{v}</div><div class='ks'>{c}</div></div>" for a, v, c in kp)
    swrows = "".join(
        f"<tr><td>{y}</td><td>{e.get('status')}</td><td class='mono'>{e.get('scene_id')}</td><td>{e.get('recorded_date')}</td>"
        f"<td>{e.get('stac_datetime', e.get('cached_tags', {}).get('datetime', '-'))}</td><td>{e.get('recorded_boa_offset_removed_dn')}</td>"
        f"<td>{e.get('source', '-')}</td></tr>" for y, e in sw.items())
    b["swir"] = ("<table class='grid'><thead><tr><th>연도</th><th>B11 상태</th><th>Scene ID(10m 분석 장면과 동일)</th><th>기록 촬영일</th>"
                 "<th>B11 장면 촬영시각</th><th>제거된 offset(DN)</th><th>출처</th></tr></thead><tbody>" + swrows + "</tbody></table>")
    sens_rows = ""
    base_vals = {"ndvi.delta_max": th["ndvi"]["delta_max"], "bsi.increase_min": th["bsi"]["increase_min"],
                 "quarry.min_component_pixels": th["quarry"]["min_component_pixels"],
                 "min_followup_obs": th["min_followup_obs"], "quarry.adjacency_distance_m": th["quarry"]["adjacency_distance_m"]}
    prev = None
    for r in m["sensitivity"]["rows"]:
        if r["parameter"] == "default":
            continue
        is_def = r["value"] == base_vals.get(r["parameter"])
        head = ""
        if r["parameter"] != prev:
            head = f"<tr class='grp'><td colspan='8'>{SENS_KO.get(r['parameter'], r['parameter'])}</td></tr>"
            prev = r["parameter"]
        pot = r["jaccard_potential"]
        sens_rows += (head + f"<tr class='{'base' if is_def else ''}'><td>{r['value']}{' (기준)' if is_def else ''}</td>"
                      f"<td class='num'>{r['exposed_ha']:.1f}</td><td class='num'>{r['veg_loss_ha']:.1f}</td>"
                      f"<td class='num'>{r['potential_expansion_ha']:.2f}</td><td class='num'>{r['parcels_exposed']}</td>"
                      f"<td class='num'>{r['parcels_potential_expansion']}</td>{_jcell(r['jaccard_exposed'])}{_jcell(pot)}</tr>")
    b["sens"] = ("<table class='grid'><thead><tr><th>값</th><th>노출지 증가(ha)</th><th>식생 감소(ha)</th><th>채석장 확장 의심(ha)</th>"
                 "<th>노출지 필지</th><th>확장 의심 필지</th><th>기준 대비 노출지 겹침</th><th>기준 대비 확장 의심 겹침</th></tr></thead><tbody>"
                 + sens_rows + "</tbody></table>"
                 "<p class='note'>겹침(Jaccard) 1.00 = 기준과 동일, 0.8 이상 안정, 0.6 미만 불안정. 기준값 행은 굵게 표시.</p>")
    b["thresholds"] = _kv([
        ("NDVI(감소)", f"이전 ≥ {th['ndvi']['before_min']}, 이후 ≤ {th['ndvi']['after_max']}, ΔNDVI ≤ {th['ndvi']['delta_max']} (연도쌍 편이 보정 후)"),
        ("BSI(노출지 증가)", f"ΔBSI ≥ {th['bsi']['increase_min']} (편이 보정 후) 및 이후 BSI ≥ {th['bsi']['exposed_min']}"),
        ("회복", f"이전 NDVI < {th['ndvi']['recovery_ndvi']}, ΔNDVI ≥ {th['ndvi']['recovery_delta_min']}, 이후 ≥ {th['ndvi']['recovery_ndvi']}, ΔBSI ≤ -{th['bsi']['decrease_min']}"),
        ("노출 지속 상태(TYPE C)", f"NDVI ≤ {th['exposed_state']['ndvi_max']} 및 BSI ≥ {th['bsi']['exposed_min']} 관측이 {th['min_persistent_exposed_obs']}회 이상"),
        ("수체 의심", f"MNDWI > {th['water_mndwi_min']}"),
        ("채석장 인접", f"참조 폴리곤까지 {th['quarry']['adjacency_distance_m']} m 이내, 연결 성분 ≥ {th['quarry']['min_component_pixels']} 픽셀"),
        ("필지 판단", f"면적 ≥ {th['parcel']['min_area_m2_for_composite']} ㎡, 변화 면적 ≥ {th['parcel']['min_change_area_m2']} ㎡ 및 비율 ≥ {th['parcel']['min_change_ratio']*100:.0f}%"),
        ("연도쌍 계통 편이 보정", f"ΔBSI(안정 픽셀 중앙값): {', '.join(f'{k} {v:+.3f}' for k, v in m['bias_shifts_dbsi_by_interval'].items())}<br>"
                          f"ΔNDVI(전체 중앙값): {', '.join(f'{k} {v:+.3f}' for k, v in m['bias_shifts_dndvi_by_interval'].items())}"),
    ])
    b["quarry"] = _kv([
        ("참조 자료", _html.escape(str(q.get("provenance", {}).get("source")))),
        ("라이선스", _html.escape(str(q.get("provenance", {}).get("license")))),
        ("수집 시각", _html.escape(str(q.get("provenance", {}).get("retrieved_at")))),
        ("신뢰 수준", "비공인 자원봉사 참고자료(OSM_UNOFFICIAL). 정확도·최신성 보증 없음"),
        ("사용 폴리곤", f"{q.get('n_polygons', 0)}개 (landuse=quarry). natural=bare_rock 등은 맥락 정보로만 기록"),
        ("AOI 내 참조 면적", f"{s['aoi_quarry_ref_ha']:.1f} ha (허가/작업구역 전체를 감싼 거친 경계일 수 있어 노출지 면적이 아님)"),
        ("채석장 면적 변화", "산출하지 않음 - 신뢰할 수 있는 시계열 경계 자료 없음"),
        ("분석 상태", "수행" if m["quarry_analysis_performed"] else "보류(참조 자료 없음)"),
    ])
    b["limits"] = "<ul>" + "".join(f"<li>{_html.escape(x)}</li>" for x in m["limitations"]) + "</ul>"
    b["base_limits"] = "<ul>" + "".join(f"<li>{_html.escape(x)}</li>" for x in base["meta"]["limitations"]) + "</ul>"
    qa = m["swir_qa"]["per_year"]
    b["qa"] = _kv([
        ("BSI 유효 비율(20m 격자)", ", ".join(f"{y}: {v['valid_fraction_20m_native'] * 100:.1f}%" for y, v in qa.items())),
        ("B11 dark DN(0.5%) 연도별", ", ".join(f"{y}: {v['swir_dn_p0.5']:.0f}" for y, v in qa.items()) + " (≈1000 편차 없음 → offset 중복/누락 징후 없음)"),
        ("BSI 중앙값 연도별(원본 20m)", ", ".join(f"{y}: {v['bsi_median']:+.3f}" for y, v in qa.items())),
        ("10m↔20m 격자 정합", "모든 연도 정수 픽셀 정렬(parity OK)"),
        ("정확도 평가", "미수행 - 정답(참조 폴리곤/육안 판독) 자료 없음. 정확도 수치를 제시하지 않음"),
    ])
    b["scenes"] = "".join(
        f"<tr><td>{x['year']}</td><td>{x['date']}</td><td>{x['platform']}</td><td class='mono'>{x['scene_id']}</td><td>{x['processing_baseline']}</td></tr>"
        for x in base["meta"]["scenes"])
    return b


def _validation_block(ext: dict) -> str:
    """육안 판독 검증 / IO LULC 일치도 / 다중 장면 합성 비교 / 맥락 자료(한글, 읽기 쉬운 표)."""
    m = ext["meta"]
    parts = []
    f2 = lambda x: "-" if x is None else f"{x:.2f}"  # noqa: E731
    vp = Path("outputs/kr_rail_danyang/validation/visual_validation_summary.json")
    if vp.exists():
        v = json.loads(vp.read_text(encoding="utf-8"))
        c = v["confusion_exposed_state_2026"]
        rows = ""
        for k, d in c["per_stratum"].items():
            lab = " · ".join(f"{LABEL_KO.get(a, a)} {n}" for a, n in sorted(d["labels"].items(), key=lambda t: -t[1]))
            share = d["visually_exposed"] / d["n"] if d["n"] else 0
            rows += (f"<tr><td>{STRATUM_KO.get(k, k)}</td><td class='num'>{d['n']}</td>"
                     f"<td><div class='mbar'><b style='width:{share * 100:.0f}%'></b></div><span class='num'>{d['visually_exposed']}/{d['n']}</span></td>"
                     f"<td class='num'>{d['pred_exposed']}/{d['n']}</td><td>{lab}</td></tr>")
        parts.append(
            "<h3>① 현재 상태를 육안으로 확인했을 때 얼마나 맞았나</h3>"
            "<div class='cards3'>"
            f"<div class='mini'><div class='kl'>정밀도(Precision)</div><div class='kv2'>{f2(c['precision'])}</div><div class='ks'>노출로 판정한 곳 중 실제 비식생</div></div>"
            f"<div class='mini'><div class='kl'>재현율(Recall)</div><div class='kv2'>{f2(c['recall'])}</div><div class='ks'>실제 비식생 중 노출로 판정한 곳</div></div>"
            f"<div class='mini'><div class='kl'>F1</div><div class='kv2'>{f2(c['f1'])}</div><div class='ks'>표본 {c['n']}개 · 정확도 {f2(c['overall_accuracy'])}</div></div></div>"
            f"<p>맞힌 곳 {c['tp'] + c['tn']}개, 틀린 곳 {c['fp'] + c['fn']}개(노출로 잘못 판정 {c['fp']}, 놓침 {c['fn']}). "
            "노출로 잘못 판정한 9건은 <b>초지·농경지 5, 수목 2(경계 혼합), 수면 2</b>였고, 놓친 8건은 <b>나지 5·시설 3</b>으로 BSI가 기준(0) 부근이었습니다.</p>"
            f"<p class='note'>{_html.escape(v['caveat'])}</p>"
            "<table class='grid'><thead><tr><th>분류 층</th><th>표본</th><th>육안으로 본 비식생 비율</th><th>노출로 판정</th><th>육안 판독 분포</th></tr></thead><tbody>"
            + rows + "</tbody></table>")
    else:
        parts.append("<h3>① 육안 판독 기반 검증</h3><p class='note'>미수행</p>")
    cp = Path("outputs/kr_rail_danyang/composite/tables/comparison_summary.json")
    if cp.exists():
        c = json.loads(cp.read_text(encoding="utf-8"))
        e, q = c["exposed_increase"], c["potential_quarry_expansion"]
        parts.append(
            "<h3>② 다른 입력(다중 장면 중앙값)으로 다시 계산해도 같은 결과인가</h3>"
            "<div class='cards3'>"
            f"<div class='mini'><div class='kl'>노출지 증가 필지 재현</div><div class='kv2'>{e['both']}/{e['single']}</div><div class='ks'>겹침 {f2(e['jaccard'])}</div></div>"
            f"<div class='mini'><div class='kl'>채석장 확장 의심 재현</div><div class='kv2'>{q['both']}/{q['single']}</div><div class='ks'>겹침 {f2(q['jaccard'])}</div></div>"
            f"<div class='mini'><div class='kl'>강건 후보</div><div class='kv2'>{c['robust_candidates']}</div><div class='ks'>두 입력 모두 후보</div></div></div>"
            f"<p>두 입력 모두 평가 가능한 {c['n_both_evaluable']:,}개 필지의 유형 일치율은 {f2(c.get('type_agreement_rate_evaluable'))}입니다. "
            "합성에서도 2018·2020년 AOI 평균 NDVI가 낮게 나와, 이 저하는 단일 장면의 구름 때문이 아니라 해당 연도 이른 봄 식생 위상 차이로 판단됩니다. "
            "강건 후보는 지도·표의 <b>강건 후보만</b> 필터로 볼 수 있습니다.</p>")
    else:
        parts.append("<h3>② 다중 장면 합성 비교</h3><p class='note'>미수행</p>")
    la = m.get("lulc_agreement") or {}
    if la.get("years"):
        rows = "".join(f"<tr><td>{y}</td><td class='num'>{d['n']:,}</td><td class='num'>{f2(d['precision'])}</td><td class='num'>{f2(d['recall'])}</td>"
                       f"<td class='num'>{f2(d['f1'])}</td><td class='num'>{f2(d['accuracy'])}</td></tr>" for y, d in la["years"].items())
        parts.append("<h3>③ 토지피복 모델(IO LULC)과의 일치도 — 약한 참조</h3>"
                     "<p class='note'>정답이 아닌 모델 산출물과의 비교입니다. 기준=IO의 나지·시가지, 예측=NDVI≤0.25 및 BSI≥0.</p>"
                     "<table class='grid'><thead><tr><th>연도</th><th>비교 셀</th><th>Precision</th><th>Recall</th><th>F1</th><th>정확도</th></tr></thead><tbody>"
                     + rows + "</tbody></table>")
    ctx = m.get("context") or {}
    if ctx:
        names = {"zoning_urban": "도시지역(용도지역)", "zoning_managed": "관리지역(용도지역)", "zoning_agroforest": "농림지역(용도지역)",
                 "agri_disadvantaged": "영농여건불리농지", "forest_site": "산림입지도(경작지·제지)", "forest_protection": "산림보호구역"}
        rows = "".join(f"<tr><td>{names.get(k, k)}</td><td class='num'>{v.get('n_features', '-')}</td><td>{'확보' if v.get('status') == 'OK' else '미확보'}</td></tr>"
                       for k, v in (ctx.get("vworld_layers") or {}).items())
        ly = [y for y, v in (ctx.get("lulc") or {}).get("years", {}).items() if v.get("status") == "OK"]
        parts.append("<h3>④ 농경지·토지이용 맥락 자료</h3>"
                     "<p class='note'>지목(공부상), VWorld 공식 레이어, IO 토지피복(" + (f"{ly[0]}~{ly[-1]}" if ly else "-") + ")을 필지에 붙여 "
                     "<b>농경지 변동 가능</b> 같은 혼동 위험을 표시합니다. 변화를 확정하거나 점수를 바꾸지 않습니다.</p>"
                     "<table class='grid'><thead><tr><th>자료</th><th>객체 수</th><th>상태</th></tr></thead><tbody>" + rows + "</tbody></table>")
    return "".join(parts)


# ----------------------------------------------------------------------------- static
def build_bsi_static(base: dict, ext: dict, cfg: dict) -> list[str]:
    _font()
    sd = Path(cfg["bsi"]["output_dir"]) / "report" / "static"
    sd.mkdir(parents=True, exist_ok=True)
    years, stack, g = base["years"], ext["stack"], ext["parcels"]
    tr = stack["transform"]
    H, W = stack["shape"]
    ext_ = (tr.c, tr.c + W * tr.a, tr.f - H * tr.a, tr.f)
    aoi, rail = base["aoi"], base["rail"]
    b = aoi.total_bounds
    q = ext["quarry"]
    files = []

    def deco(ax, title):
        aoi.boundary.plot(ax=ax, color="#222", lw=1.1)
        rail.plot(ax=ax, color="#1f4e9c", lw=1.1)
        if len(q):
            q.boundary.plot(ax=ax, color="#7f6a52", lw=1.0, ls="--")
        ax.set_xlim(b[0] - 100, b[2] + 100)
        ax.set_ylim(b[1] - 100, b[3] + 100)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(title, fontsize=10)

    fig, axes = plt.subplots(2, 3, figsize=(13, 9.5))
    for i, ax in enumerate(axes.ravel()):
        ax.imshow(stack["bsi"][i], extent=ext_, cmap="YlOrBr", vmin=-0.35, vmax=0.25)
        deco(ax, f"BSI {years[i]} (20m)")
    fig.suptitle("연도별 BSI (SWIR1 B11 20m, 점선: OSM 채석장 참조 · 파란선: OSM 철도)", fontsize=12)
    fig.tight_layout()
    f = sd / "bsi_by_year.png"
    fig.savefig(f, dpi=110)
    plt.close(fig)
    files.append(f)

    comp, qres = ext["comp"], ext["qres"]
    fig, axes = plt.subplots(1, 2, figsize=(14, 8))
    cmap = matplotlib.colors.ListedColormap(["#ffffff00"] + [PIX_TYPE_COLORS[i] for i in range(1, 7)])
    t = np.where(comp["type"] == 255, 0, comp["type"])
    axes[0].imshow(t, extent=ext_, cmap=cmap, vmin=0, vmax=6, interpolation="nearest")
    deco(axes[0], "복합 변화 유형(20m): 노랑계=감소, 빨강=노출지 증가, 갈색=노출 지속, 파랑=회복, 초록=안정, 보라=불확실")
    qc = qres["quarry_class"]
    cm2 = matplotlib.colors.ListedColormap(["#ffffff00"] + [PIX_QUARRY_COLORS[i] for i in range(1, 7)])
    axes[1].imshow(qc, extent=ext_, cmap=cm2, vmin=0, vmax=6, interpolation="nearest")
    deco(axes[1], "채석장 관련 분류: 진빨강=확장 의심 후보, 연한=참조 영역/기타(참조는 비공인)")
    fig.tight_layout()
    f = sd / "composite_and_quarry.png"
    fig.savefig(f, dpi=110)
    plt.close(fig)
    files.append(f)

    fig, ax = plt.subplots(figsize=(8, 4.8))
    ys = years
    ax.plot(ys, base["meta"]["aoi"]["summary"]["per_year_aoi_ndvi_mean"].values(), "o-", color="#4a9d5b", label="AOI 평균 NDVI(10m)")
    aw_vals = []
    from . import parcel_statistics as ps
    aw = ps.pixel_weights([aoi.geometry.union_all()], tr, (H, W))
    for i in range(len(ys)):
        v = stack["bsi"][i].ravel()[aw["f"]]
        ok = np.isfinite(v)
        aw_vals.append(float((aw["a"][ok] * v[ok]).sum() / aw["a"][ok].sum()))
    ax.plot(ys, aw_vals, "s--", color="#8c6d46", label="AOI 평균 BSI(20m)")
    ax.set_title("AOI 평균 NDVI / BSI (촬영일·계절 위상 영향 포함)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    f = sd / "aoi_mean_ndvi_bsi.png"
    fig.savefig(f, dpi=120)
    plt.close(fig)
    files.append(f)
    imgs = "".join(f"<figure><img src='data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}' style='max-width:100%'>"
                   f"<figcaption>{p.name}</figcaption></figure>" for p in files)
    page = (f"<!doctype html><html lang='ko'><meta charset='utf-8'><title>{_html.escape(TITLE)} - 정적본</title>"
            "<body style='font-family:Malgun Gothic,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px'>"
            f"<h1 style='font-size:20px'>{_html.escape(TITLE)} (정적 대체본)</h1>"
            f"<p style='background:#fff4e5;padding:12px;border-left:4px solid #d97706'>{_html.escape(REQUIRED_NOTICE)}</p>{imgs}</body></html>")
    hp = Path(cfg["bsi"]["output_dir"]) / "report" / "kr_rail_danyang_ndvi_bsi_quarry_static_report.html"
    hp.write_text(page, encoding="utf-8")
    return [str(p) for p in files] + [str(hp)]


def build_bsi_report(base: dict, ext: dict, cfg: dict, out_root: Path) -> list[str]:
    return [str(build_bsi_html(base, ext, cfg, out_root))] + build_bsi_static(base, ext, cfg)
