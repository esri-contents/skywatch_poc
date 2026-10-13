"""NDVI + BSI + 채석장 후보 + 필지 통합 확장 파이프라인 (기존 src.ndvi.pipeline 결과 위에서 동작).

기존 NDVI 산출물/로직은 수정하지 않는다. 복합 분석은 20m 격자(B11 해상도)이며 필지 필드에 해상도를 명시한다.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.features import shapes
from scipy import ndimage

from . import bsi as bsimod
from . import parcel_statistics as ps
from . import temporal
from .composite import CONF_NAMES, TYPE_CODES, TYPE_KO, TYPE_NAMES, classify_composite
from .processing import write_raster
from .quarry import QUARRY_CODES, QUARRY_KO, QUARRY_NAMES, classify_quarry, load_quarry_reference
from .context import load_lulc, load_vworld_context, parcel_context
from .reference import ensure_reference
from .swir import ensure_swir

logger = logging.getLogger(__name__)

PERSIST_NAMES = {0: "NONE", 1: "PERSISTENT", 2: "UNCONFIRMED", 3: "REVEGETATED", 4: "PARTIAL"}
BSI_LIMITATIONS = [
    "B11(SWIR1)의 원본 해상도는 20m이며 NDVI+BSI 복합 분석은 20m 격자에서 수행했다. 10m NDVI 결과와 면적 정확도가 다르므로 동일 정밀도로 비교하지 않는다.",
    "NDVI와 BSI만으로 채석장·도로·농경지·건설현장·나지를 확정 구분할 수 없다. 모든 유형은 '후보'이며 현장/고해상도 영상 검증이 필요하다.",
    "채석장 참조 경계는 OpenStreetMap(비공인)이며 허가구역 전체를 감싸 내부에 산림 등 비노출지를 포함할 수 있다. 참조 폴리곤 면적/노출지 면적을 채석장 면적으로 해석하지 않는다.",
    "참조 경계의 작성 시점을 알 수 없어 채석장 경계의 시계열 변화(면적 증감)는 산출하지 않았다.",
    "식생 감소 면적과 노출지 증가 면적은 중복된다(노출지 증가 ⊂ 식생 감소 후보). 합산하여 전체 변화 면적으로 해석하지 않는다.",
    "연도별 촬영일(DOY)·대기·센서 차이에 의한 BSI 계통 편이는 안정 픽셀 중앙값으로 근사 보정했으며 완전한 보정이 아니다.",
    "농경지/벌채/산불/일시 나지/그림자/수분 영향은 지목·용도지역·토지피복 맥락 플래그로만 경고하며 자동으로 구분하지 못해 '판단 불확실 변화' 또는 후보로 남는다.",
    "변화 탐지 자체의 정확도(혼동행렬)는 과거 시점 정답 자료가 없어 산출하지 못했다. 현재(2026) 노출/식생 상태에 대해서만 고해상도 영상 육안 판독(AI 보조, 표본 104개)으로 검증했으며, 변화 발생 시점·원인은 검증되지 않았다.",
    "20m 픽셀 2개 미만(800㎡ 미만) 필지는 복합 지표 판단을 보류한다.",
]


# ----------------------------------------------------------------------------- utils
def _polygonize(mask_label: np.ndarray, transform, attrs: pd.DataFrame, crs: str = "EPSG:5186") -> gpd.GeoDataFrame:
    lab = mask_label.astype("int32")
    geoms, ids = [], []
    for geom, v in shapes(lab, mask=lab > 0, transform=transform, connectivity=8):
        geoms.append(shapely.geometry.shape(geom))
        ids.append(int(v))
    if not ids:
        return gpd.GeoDataFrame(attrs.iloc[0:0].copy(), geometry=[], crs=crs)
    g = gpd.GeoDataFrame({"label": ids}, geometry=geoms, crs=crs)
    return g.merge(attrs, on="label", how="left").drop(columns="label")


def _candidate_vectors(mask: np.ndarray, comp: dict, qres: dict, transform, intervals: list[str], name: str,
                       pixel_area: float, first_key: str = "first_interval_idx") -> gpd.GeoDataFrame:
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    rows = []
    for i in range(1, n + 1):
        m = lab == i
        fi = comp[first_key][m]
        fi = fi[fi != 255]
        qc = np.bincount(qres["quarry_class"][m], minlength=7).argmax()
        pc = np.bincount(comp["persist"][m], minlength=5).argmax()
        rows.append({"label": i, "candidate": name, "n_pixels": int(m.sum()), "area_m2": float(m.sum() * pixel_area),
                     "first_observed_interval": intervals[int(fi.min())] if fi.size else None,
                     "persistence": PERSIST_NAMES[int(pc)], "quarry_class": QUARRY_NAMES.get(int(qc), "NONE"),
                     "confidence": CONF_NAMES[int(comp["confidence"][m].max())],
                     "min_dist_to_quarry_m": float(np.nanmin(qres["dist_to_quarry_m"][m]))
                     if np.isfinite(qres["dist_to_quarry_m"][m]).any() else None,
                     "resolution_m": abs(transform.a)})
    attrs = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["label"])
    return _polygonize(lab, transform, attrs)


def _wmean(wt: dict, n: int, flat: np.ndarray):
    v = flat[wt["f"]]
    ok = np.isfinite(v)
    va = np.bincount(wt["p"], weights=wt["a"] * ok, minlength=n)
    s = np.bincount(wt["p"], weights=np.where(ok, wt["a"] * v, 0.0), minlength=n)
    return np.where(va > 0, s / np.where(va > 0, va, 1), np.nan), va


def _area(wt: dict, n: int, mask_flat: np.ndarray) -> np.ndarray:
    return np.bincount(wt["p"], weights=wt["a"] * mask_flat[wt["f"]], minlength=n)


# ----------------------------------------------------------------------------- parcel level
def parcel_composite_table(parcels: gpd.GeoDataFrame, clipped: gpd.GeoSeries, stack: dict, comp: dict, qres: dict,
                           quarry: gpd.GeoDataFrame, bcfg: dict, years: list[int], intervals: list[str]) -> pd.DataFrame:
    tr, shape = stack["transform"], stack["shape"]
    n = len(parcels)
    wt = ps.pixel_weights(list(clipped.values), tr, shape)
    out = pd.DataFrame(index=range(n))
    out["composite_resolution_m"] = bcfg["grid_resolution_m"]
    out["ndvi_resolution_m"] = 10
    for i, y in enumerate(years):
        m, va = _wmean(wt, n, stack["bsi"][i].ravel())
        out[f"bsi_mean_{y}"] = m
        out[f"bsi_valid_area_{y}"] = va
        m2, _ = _wmean(wt, n, stack["ndvi20"][i].ravel())
        out[f"ndvi20_mean_{y}"] = m2
    first, last = years[0], years[-1]
    out["delta_bsi_first_last"] = out[f"bsi_mean_{last}"] - out[f"bsi_mean_{first}"]
    out["delta_ndvi20_first_last"] = out[f"ndvi20_mean_{last}"] - out[f"ndvi20_mean_{first}"]

    ev = _area(wt, n, comp["evaluable"].ravel())
    out["evaluable_area_20m_m2"] = ev
    out["veg_decline_area_20m_m2"] = _area(wt, n, (comp["loss_any"] | comp["expo_any"]).ravel())
    out["exposed_increase_area_20m_m2"] = _area(wt, n, comp["expo_any"].ravel())
    out["veg_recovery_area_20m_m2"] = _area(wt, n, comp["rec_any"].ravel())
    out["persistent_exposed_area_20m_m2"] = _area(wt, n, comp["persistent_exposed"].ravel())
    out["uncertain_area_20m_m2"] = _area(wt, n, comp["unc_any"].ravel())
    out["stable_vegetation_area_20m_m2"] = _area(wt, n, (comp["type"] == TYPE_CODES["STABLE_VEGETATION"]).ravel())
    out["exposed_persistent_area_20m_m2"] = _area(wt, n, (comp["expo_any"] & (comp["persist"] == 1)).ravel())
    out["exposed_revegetated_area_20m_m2"] = _area(wt, n, (comp["expo_any"] & (comp["persist"] == 3)).ravel())
    out["water_suspect_area_20m_m2"] = _area(wt, n, comp["water"].ravel())
    qc = qres["quarry_class"].ravel()
    for nm in ("POTENTIAL_QUARRY_EXPANSION", "EXPOSED_SURFACE_CHANGE", "POSSIBLE_REVEGETATION", "QUARRY_INTERNAL_CHANGE",
               "UNCLASSIFIED"):
        out[f"{nm.lower()}_area_20m_m2"] = _area(wt, n, qc == QUARRY_CODES[nm])
    out["note_area_overlap"] = "veg_decline ⊃ exposed_increase (중복, 합산 금지)"

    # 최초 관측 구간(노출/감소): 면적 기준
    fi = comp["first_interval_idx"].ravel()
    fl = []
    for k in range(len(intervals)):
        fl.append(_area(wt, n, (fi == k)))
    fl = np.stack(fl, 1)
    thr = bcfg["parcel"]["min_change_area_m2"] / 2
    out["first_change_interval_20m"] = [
        intervals[int(np.where(r >= thr)[0][0])] if (r >= thr).any() else None for r in fl]

    # 채석장 근접성(정확한 폴리곤 거리)
    if len(quarry):
        qu = shapely.union_all(quarry.geometry.values)
        out["dist_to_quarry_m"] = shapely.distance(parcels.geometry.values, qu)
        out["quarry_overlap_area_m2"] = [float(g.intersection(qu).area) for g in parcels.geometry.values]
        out["quarry_overlap_ratio"] = out["quarry_overlap_area_m2"] / parcels["area_total_m2"].to_numpy()
        out["quarry_adjacent"] = out["dist_to_quarry_m"] <= bcfg["quarry"]["adjacency_distance_m"]
    else:
        out["dist_to_quarry_m"] = np.nan
        out["quarry_overlap_area_m2"] = np.nan
        out["quarry_overlap_ratio"] = np.nan
        out["quarry_adjacent"] = False

    # --- 필지 복합 유형
    pc = bcfg["parcel"]
    area = parcels["area_in_aoi_m2"].to_numpy()
    ev_frac = np.where(area > 0, ev / area, 0)
    types, qctx, flags, conf = [], [], [], []
    for i in range(n):
        f = []
        if area[i] < pc["min_area_m2_for_composite"]:
            f.append("LOW_PIXEL_COUNT_20M")
        if ev_frac[i] < pc["min_evaluable_fraction"]:
            f.append("LOW_COVERAGE_20M")
        if out["water_suspect_area_20m_m2"].iat[i] > 0.1 * max(ev[i], 1):
            f.append("WATER_SUSPECT")
        miss = [y for y in years if not stack["available"][y]]
        if miss:
            f.append("BSI_YEAR_MISSING:" + ",".join(map(str, miss)))
        insufficient = ("LOW_PIXEL_COUNT_20M" in f) or ev[i] < pc["min_area_m2_for_composite"]
        def ok(a):
            return a >= pc["min_change_area_m2"] and a / max(ev[i], 1) >= pc["min_change_ratio"]
        if insufficient:
            t = "INSUFFICIENT_DATA"
        elif ok(out["exposed_increase_area_20m_m2"].iat[i]):
            t = "EXPOSED_SURFACE_INCREASE"
        elif ok(out["veg_decline_area_20m_m2"].iat[i]):
            t = "VEGETATION_LOSS"
        elif ok(out["veg_recovery_area_20m_m2"].iat[i]):
            t = "VEGETATION_RECOVERY"
        elif ok(out["persistent_exposed_area_20m_m2"].iat[i]):
            t = "PERSISTENT_EXPOSED_SURFACE"
        elif ok(out["uncertain_area_20m_m2"].iat[i]):
            t = "UNCERTAIN_CHANGE"
        elif out["stable_vegetation_area_20m_m2"].iat[i] / max(ev[i], 1) >= 0.5:
            t = "STABLE_VEGETATION"
        else:
            t = "NO_SIGNIFICANT_CHANGE"
        types.append(t)
        mn = pc["min_change_area_m2"]
        if insufficient:
            q = "NOT_EVALUATED"
        elif out["potential_quarry_expansion_area_20m_m2"].iat[i] >= mn:
            q = "POTENTIAL_QUARRY_EXPANSION"
        elif out["quarry_internal_change_area_20m_m2"].iat[i] >= mn:
            q = "QUARRY_INTERNAL_CHANGE"
        elif out["exposed_surface_change_area_20m_m2"].iat[i] >= mn:
            q = "EXPOSED_SURFACE_CHANGE"
        elif out["possible_revegetation_area_20m_m2"].iat[i] >= mn:
            q = "POSSIBLE_REVEGETATION"
        elif (out["quarry_overlap_ratio"].iat[i] or 0) >= 0.5:
            q = "VERIFIED_QUARRY_AREA"
        elif t == "VEGETATION_LOSS":
            q = "GENERAL_VEGETATION_LOSS"
        else:
            q = "NONE"
        qctx.append(q)
        flags.append(";".join(f) if f else "VALID")
        if insufficient:
            conf.append("NONE")
        elif f and any(x.startswith(("LOW_COVERAGE", "WATER")) for x in f):
            conf.append("LOW")
        elif t in ("EXPOSED_SURFACE_INCREASE",) and out["exposed_persistent_area_20m_m2"].iat[i] >= mn:
            conf.append("HIGH")
        elif t in ("STABLE_VEGETATION", "NO_SIGNIFICANT_CHANGE"):
            conf.append("HIGH")
        else:
            conf.append("MEDIUM" if t != "VEGETATION_LOSS" and t != "UNCERTAIN_CHANGE" else "LOW")
    out["composite_type"] = types
    out["composite_type_ko"] = [TYPE_KO.get(t, {"INSUFFICIENT_DATA": "자료 부족(판단 보류)",
                                              "NO_SIGNIFICANT_CHANGE": "뚜렷한 변화 없음"}.get(t, t)) for t in types]
    out["quarry_context"] = qctx
    out["composite_quality_flags"] = flags
    out["composite_confidence"] = conf
    out["evaluable_fraction_20m"] = ev_frac
    out["decline_ratio_20m"] = np.where(ev > 0, out["veg_decline_area_20m_m2"] / np.where(ev > 0, ev, 1), np.nan)
    out["exposed_ratio_20m"] = np.where(ev > 0, out["exposed_increase_area_20m_m2"] / np.where(ev > 0, ev, 1), np.nan)
    return out


def rank_tables(df: pd.DataFrame, cfg_base: dict, bcfg: dict, parcels_clip_out: pd.DataFrame) -> pd.DataFrame:
    """랭킹 A(철도 인접 현장조사, 채석장 내부 변화 제외) / B(채석장·노출지 변화 검토)."""
    out = df.copy()
    pr = cfg_base["ndvi"]["priority"]
    w = pr["weights"]
    ratio = out["decline_ratio_out_quarry"].fillna(0).to_numpy()
    out["A_score_decline_ratio"] = np.clip(ratio / pr["decline_ratio_full_score"], 0, 1) * 100
    out["A_score_persistence"] = out["persistence_status_out_quarry"].map(pr["persistence_scores"]).fillna(0).astype(float)
    d = out["rail_distance_m"].to_numpy(float)
    lo, hi = pr["rail_full_score_distance_m"], pr["rail_zero_score_distance_m"]
    out["A_score_rail_proximity"] = np.where(np.isfinite(d), np.clip((hi - d) / (hi - lo), 0, 1) * 100, 0.0)
    out["A_score"] = (w["decline_ratio"] * out["A_score_decline_ratio"] + w["persistence"] * out["A_score_persistence"]
                      + w["rail_proximity"] * out["A_score_rail_proximity"]) / sum(w.values())
    # 후보: 채석장 참조 폴리곤 밖의 NDVI 감소가 면적/비율 하한을 넘고 지속 또는 지속성 미확인인 필지
    candA = ((out["decline_area_out_quarry_m2"] >= pr["min_decline_area_m2"])
             & (out["decline_ratio_out_quarry"].fillna(0) >= cfg_base["ndvi"]["classification"]["decline_ratio_min"])
             & out["persistence_status_out_quarry"].isin(["PERSISTENT", "UNCONFIRMED"])
             & (out["change_type"] != "INSUFFICIENT_DATA") & (out["quality_status"] != "NOT_EVALUABLE"))
    out["A_candidate"] = candA
    out["A_rank"] = pd.array([pd.NA] * len(out), dtype="Int64")
    o = out[candA].sort_values(["A_score", "decline_area_out_quarry_m2"], ascending=False).index
    out.loc[o, "A_rank"] = np.arange(1, len(o) + 1)

    rb = bcfg["rankB"]
    wb = rb["weights"]
    out["B_score_exposed_ratio"] = np.clip(out["exposed_ratio_20m"].fillna(0) / rb["exposed_ratio_full_score"], 0, 1) * 100
    link = np.zeros(len(out))
    for k, v in rb["quarry_link_scores"].items():
        link = np.maximum(link, np.where(out["quarry_context"] == k, v, 0))
    out["B_score_quarry_link"] = link
    pers = np.zeros(len(out))
    pers = np.where(out["exposed_persistent_area_20m_m2"] > 0, rb["persistence_scores"]["PERSISTENT"], pers)
    pers = np.where((pers == 0) & (out["exposed_increase_area_20m_m2"] > 0) & (out["exposed_revegetated_area_20m_m2"] <= 0),
                    rb["persistence_scores"]["UNCONFIRMED"], pers)
    pers = np.where((pers == 0) & (out["exposed_revegetated_area_20m_m2"] > 0), rb["persistence_scores"]["REVEGETATED"], pers)
    out["B_score_persistence"] = pers
    out["B_score"] = (wb["exposed_ratio"] * out["B_score_exposed_ratio"] + wb["quarry_link"] * out["B_score_quarry_link"]
                      + wb["persistence"] * out["B_score_persistence"]) / sum(wb.values())
    candB = ((out["exposed_increase_area_20m_m2"] >= rb["min_exposed_area_m2"])
             | out["quarry_context"].isin(["POTENTIAL_QUARRY_EXPANSION", "QUARRY_INTERNAL_CHANGE"])) \
        & (out["composite_type"] != "INSUFFICIENT_DATA")
    out["B_candidate"] = candB
    out["B_rank"] = pd.array([pd.NA] * len(out), dtype="Int64")
    o = out[candB].sort_values(["B_score", "exposed_increase_area_20m_m2"], ascending=False).index
    out.loc[o, "B_rank"] = np.arange(1, len(o) + 1)
    warn = np.where(out["composite_confidence"].isin(["LOW", "NONE"]), "신뢰도 낮음: 추가 검증 필요", "")
    out["reliability_warning"] = warn
    return out


# ----------------------------------------------------------------------------- sensitivity
def _lulc_agreement(stack: dict, comp: dict, years: list[int], aoi_union, tr10, lulc: dict) -> dict:
    """20m 셀 중심에서 IO LULC(10m) 클래스를 읽어 (bare|built) vs 우리 노출 상태를 비교한다."""
    from rasterio.features import geometry_mask

    tr, (H, W) = stack["transform"], stack["shape"]
    rr, cc = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")
    xs, ys = tr * (cc + 0.5, rr + 0.5)
    r10, c10 = rasterio.transform.rowcol(tr10, xs.ravel(), ys.ravel())
    r10, c10 = np.asarray(r10).reshape(H, W), np.asarray(c10).reshape(H, W)
    inside = ~geometry_mask([aoi_union], out_shape=(H, W), transform=tr)
    out = {"reference": "IO LULC annual v02 (모델 산출물, 정답 아님)", "positive": "IO class bare(8) 또는 built(7)",
           "pred": "NDVI20<=0.25 and BSI>=0 (노출 상태)", "years": {}}
    for y, arr in lulc.items():
        if y not in years or not stack["available"].get(y):
            continue
        i = years.index(y)
        h10, w10 = arr.shape
        ok = (r10 >= 0) & (r10 < h10) & (c10 >= 0) & (c10 < w10)
        cls = np.zeros((H, W), dtype="uint8")
        cls[ok] = arr[np.clip(r10, 0, h10 - 1), np.clip(c10, 0, w10 - 1)][ok]
        valid = inside & ok & (cls > 0) & np.isfinite(stack["bsi"][i]) & np.isfinite(stack["ndvi20"][i])
        ref = np.isin(cls, (7, 8))
        pred = (stack["ndvi20"][i] <= 0.25) & (stack["bsi"][i] >= 0.0)
        tp = int((valid & ref & pred).sum()); fp = int((valid & ~ref & pred).sum())
        fn = int((valid & ref & ~pred).sum()); tn = int((valid & ~ref & ~pred).sum())
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        out["years"][str(y)] = {"n": tp + fp + fn + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": r,
                                "f1": (2 * p * r / (p + r)) if p and r else None,
                                "accuracy": (tp + tn) / max(tp + fp + fn + tn, 1)}
    # 연도(2016/2026)에 IO가 없을 때는 가장 가까운 연도를 사용했다는 기록 없음 -> 제외(위에서 skip)
    return out


def run_sensitivity(stack: dict, cfg_b: dict, quarry: gpd.GeoDataFrame, aoi_wt: dict, parcels_wt: dict, n_parcels: int,
                    consecutive: list[tuple[int, int]], base_comp: dict, base_q: dict) -> dict:
    sc = cfg_b["sensitivity"]
    px_ha = (stack["res"] ** 2) / 1e4
    tr, shape = stack["transform"], stack["shape"]
    min_area = cfg_b["parcel"]["min_change_area_m2"]

    def metrics(p):
        c = classify_composite(stack["ndvi20"], stack["bsi"], stack["mndwi"], p, consecutive, p.get("bias_correction", True))
        q = classify_quarry(c, quarry, tr, shape, p["quarry"]["adjacency_distance_m"], p["quarry"]["min_component_pixels"])
        expo = c["expo_any"] & c["evaluable"]
        pot = q["quarry_class"] == QUARRY_CODES["POTENTIAL_QUARRY_EXPANSION"]
        aw = aoi_wt
        inside = lambda m: float((aw["a"] * m.ravel()[aw["f"]]).sum() / 1e4)  # noqa: E731
        pe = np.bincount(parcels_wt["p"], weights=parcels_wt["a"] * expo.ravel()[parcels_wt["f"]], minlength=n_parcels)
        pp = np.bincount(parcels_wt["p"], weights=parcels_wt["a"] * pot.ravel()[parcels_wt["f"]], minlength=n_parcels)
        return {"exposed_ha": inside(expo), "veg_loss_ha": inside(c["loss_any"] | c["expo_any"]),
                "potential_expansion_ha": inside(pot), "recovery_ha": inside(c["rec_any"]),
                "parcels_exposed": int((pe >= min_area).sum()), "parcels_potential_expansion": int((pp >= min_area).sum())}, expo, pot

    base_m, base_expo, base_pot = metrics(cfg_b)
    rows = [{"parameter": "default", "value": "default", **base_m, "jaccard_exposed": 1.0, "jaccard_potential": 1.0}]
    def variant(param, val, mut):
        p = copy.deepcopy(cfg_b)
        mut(p, val)
        m, expo, pot = metrics(p)
        j = lambda a, b: float((a & b).sum() / max((a | b).sum(), 1))  # noqa: E731
        rows.append({"parameter": param, "value": val, **m, "jaccard_exposed": j(expo, base_expo),
                     "jaccard_potential": j(pot, base_pot) if (pot | base_pot).any() else None})
    for v in sc["ndvi_delta_max"]:
        variant("ndvi.delta_max", v, lambda p, x: p["ndvi"].__setitem__("delta_max", x))
    for v in sc["bsi_increase_min"]:
        variant("bsi.increase_min", v, lambda p, x: p["bsi"].__setitem__("increase_min", x))
    for v in sc["min_component_pixels"]:
        variant("quarry.min_component_pixels", v, lambda p, x: p["quarry"].__setitem__("min_component_pixels", x))
    for v in sc["min_followup_obs"]:
        variant("min_followup_obs", v, lambda p, x: p.__setitem__("min_followup_obs", x))
    for v in sc["adjacency_distance_m"]:
        variant("quarry.adjacency_distance_m", v, lambda p, x: p["quarry"].__setitem__("adjacency_distance_m", x))
    return {"rows": rows, "pixel_area_ha": px_ha}


# ----------------------------------------------------------------------------- main
def run_bsi_extension(base: dict, cfg: dict, out_root: Path, offline: bool = False, quarry_analysis: bool = True,
                      refresh_reference: bool = False) -> dict:
    """base: reporting.load_results/_analyze 결과(dict). 반환: 확장 결과 dict."""
    bc = cfg["bsi"]
    out = Path(bc["output_dir"])
    ras, vec, tab, meta_dir = out / "rasters", out / "vectors", out / "tables", out / "metadata"
    for d in (ras, vec, tab, meta_dir):
        d.mkdir(parents=True, exist_ok=True)
    from .processing import discover_scenes

    nc = cfg["ndvi"]
    years = base["years"]
    scenes = discover_scenes(cfg["paths"]["imagery_dir"], years, nc.get("scene_selection"), cfg["imagery"]["band_order"])
    status: dict = {}

    # --- 1. B11 확보 (같은 scene_id만)
    swir_report = ensure_swir(scenes, bc["swir_cache_dir"], offline=offline)
    status["swir"] = {y: e["status"] for y, e in swir_report.items()}

    # --- 2. BSI / 20m 스택
    stack = bsimod.build_composite_stack(scenes, bc["swir_cache_dir"], nc["reflectance_scale"],
                                         bc["aggregate_min_valid_10m"], bc["min_denominator"], bc["grid_resolution_m"])
    if stack["bsi"] is None:
        return {"status": "BSI_UNAVAILABLE", "swir": swir_report, "meta": {"swir": swir_report}}
    pairs = base["pairs"]
    files = bsimod.write_stack_rasters(stack, ras, pairs)
    tr, shape, res = stack["transform"], stack["shape"], stack["res"]
    T = len(years)
    consecutive = [(i, i + 1) for i in range(T - 1)]
    intervals = [f"{years[i]}→{years[i + 1]}" for i in range(T - 1)]

    # --- 3. 복합 변화탐지
    comp = classify_composite(stack["ndvi20"], stack["bsi"], stack["mndwi"], bc, consecutive, bc.get("bias_correction", True))
    for k, (a, b) in enumerate(consecutive):
        files.append(str(write_raster(ras / f"composite_event_{years[a]}_{years[b]}.tif", comp["events"][k], tr, "uint8", 255,
                                      "0 none,1 veg loss,2 exposed increase,3 recovery,4 uncertain,255 invalid")))
    files.append(str(write_raster(ras / "composite_change_type.tif", comp["type"], tr, "uint8", 255,
                                  "1 VEG_LOSS,2 EXPOSED_INCREASE,3 PERSISTENT_EXPOSED,4 RECOVERY,5 STABLE_VEG,6 UNCERTAIN")))
    files.append(str(write_raster(ras / "composite_confidence.tif", comp["confidence"], tr, "uint8", 255, "0 none,1 low,2 medium,3 high")))
    files.append(str(write_raster(ras / "composite_first_interval.tif", comp["first_interval_idx"], tr, "uint8", 255,
                                  "first observed change interval index (" + ",".join(intervals) + ")")))
    files.append(str(write_raster(ras / "exposed_persistence.tif", comp["persist"], tr, "uint8", 255,
                                  "1 persistent,2 unconfirmed,3 revegetated,4 partial")))

    # --- 4. 채석장 참조 및 후보
    aoi = base["aoi"]
    ref_prov = ensure_reference(aoi, bc["reference_dir"], offline=offline, refresh=refresh_reference)
    quarry, qmeta = load_quarry_reference(bc["reference_dir"], bc["quarry"]["reference_tags"])
    qmeta["provenance"] = ref_prov
    qa_on = quarry_analysis and len(quarry) > 0
    qres = classify_quarry(comp, quarry if qa_on else quarry.iloc[0:0], tr, shape, bc["quarry"]["adjacency_distance_m"],
                           bc["quarry"]["min_component_pixels"])
    files.append(str(write_raster(ras / "quarry_class.tif", qres["quarry_class"], tr, "uint8", 255,
                                  "1 verified quarry ref,2 potential expansion,3 exposed change,4 possible revegetation,5 unclassified,6 quarry internal")))
    pxa = res * res
    pot = qres["quarry_class"] == QUARRY_CODES["POTENTIAL_QUARRY_EXPANSION"]
    vecs = {
        "vegetation_loss_candidates": _candidate_vectors((comp["loss_any"] | comp["expo_any"]) & comp["evaluable"], comp, qres, tr, intervals, "VEGETATION_LOSS", pxa),
        "exposed_surface_candidates": _candidate_vectors(comp["expo_any"] & comp["evaluable"], comp, qres, tr, intervals, "EXPOSED_SURFACE_INCREASE", pxa, "first_exposed_idx"),
        "quarry_expansion_candidates": _candidate_vectors(pot, comp, qres, tr, intervals, "POTENTIAL_QUARRY_EXPANSION", pxa, "first_exposed_idx"),
        "vegetation_recovery_candidates": _candidate_vectors(comp["rec_any"] & comp["evaluable"], comp, qres, tr, intervals, "VEGETATION_RECOVERY", pxa),
    }
    for nm, g in vecs.items():
        g.to_file(vec / f"{nm}.gpkg", layer=nm, driver="GPKG")
        g.to_crs(4326).to_file(vec / f"{nm}.geojson", driver="GeoJSON")
        files += [str(vec / f"{nm}.gpkg"), str(vec / f"{nm}.geojson")]
    if len(quarry):
        quarry.to_file(vec / "quarry_reference_osm.gpkg", layer="quarry_reference_osm", driver="GPKG")
        files.append(str(vec / "quarry_reference_osm.gpkg"))

    # --- 5. 필지 통합
    parcels = base["parcels"]
    result: dict = {"stack": stack, "comp": comp, "qres": qres, "quarry": quarry, "vectors": vecs, "intervals": intervals,
                    "years": years, "files": files}
    aoi_union = aoi.geometry.union_all()
    sens = None
    if parcels is not None and len(parcels):
        clipped = parcels.geometry.intersection(aoi_union)
        table = parcel_composite_table(parcels, clipped, stack, comp, qres, quarry if qa_on else quarry.iloc[0:0], bc, years, intervals)
        # 농경지/토지이용 맥락(지목, VWorld 공식 레이어, IO LULC) - 혼동 위험 플래그
        ctxcfg = bc.get("context", {})
        vctx, vmeta = load_vworld_context(aoi, ctxcfg.get("cache_dir", "data/projects/kr_rail_danyang/context"), offline)
        shape10_ = base["ndvi"].shape[1:]
        lulc, lmeta = load_lulc(aoi, ctxcfg.get("lulc_years", [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]),
                                ctxcfg.get("cache_dir", "data/projects/kr_rail_danyang/context"), base["transform"], shape10_, offline)
        ctx = parcel_context(parcels.reset_index(drop=True), clipped.reset_index(drop=True), vctx, lulc, base["transform"], shape10_,
                             {"agri_share_min": ctxcfg.get("agri_share_min", 0.5)})
        table = pd.concat([table, ctx], axis=1)
        changed = table["composite_type"].isin(["EXPOSED_SURFACE_INCREASE", "VEGETATION_LOSS", "VEGETATION_RECOVERY", "UNCERTAIN_CHANGE"])
        developed = table["jimok"].isin(["대", "공장용지", "잡종지", "도로", "철도용지", "창고용지", "주유소", "주차장"])
        risk = []
        for i in range(len(table)):
            r = []
            if table["agri_context"].iat[i] and changed.iat[i]:
                r.append("FARMLAND_VARIATION_POSSIBLE")
            if changed.iat[i] and ((table["lulc_built_mean"].iat[i] or 0) >= 0.5 or developed.iat[i]):
                r.append("BUILT_OR_DEVELOPED_CONTEXT")
            if table["jimok"].iat[i] == "임야" and changed.iat[i]:
                r.append("FOREST_MANAGEMENT_OR_CLEARING_POSSIBLE")
            risk.append(";".join(r))
        table["confusion_risk"] = risk
        result["context_meta"] = {"vworld_layers": vmeta, "lulc": lmeta}
        # 랭킹 A: 채석장 폴리곤 내부 변화 제외 10m NDVI 감소
        th = nc["thresholds"]["default"]
        px = temporal.analyze_pixels(base["ndvi"], th, nc["min_followup_obs"], nc["min_valid_obs_pixel"])
        tr10, shape10 = base["transform"], base["ndvi"].shape[1:]
        qu = shapely.union_all(quarry.geometry.values) if (qa_on and len(quarry)) else None
        outside = clipped.map(lambda g: g.difference(qu) if qu is not None else g)
        wt10 = ps.pixel_weights(list(outside.values), tr10, shape10)
        first_labels = [f"{years[i]}_{years[i + 1]}" for i in range(T - 1)]
        d10 = ps.decline_stats(wt10, len(parcels), px, first_labels, nc["classification"]["first_change_min_area_m2"])
        tab_in = pd.DataFrame({"decline_area_out_quarry_m2": d10["decline_area_m2"].values,
                               "evaluable_area_out_quarry_m2": d10["evaluable_area_m2"].values})
        tab_in["decline_ratio_out_quarry"] = np.where(tab_in["evaluable_area_out_quarry_m2"] > 0,
                                                      tab_in["decline_area_out_quarry_m2"] / tab_in["evaluable_area_out_quarry_m2"].where(tab_in["evaluable_area_out_quarry_m2"] > 0), np.nan)
        ar = d10[["persistent_decline_area_m2", "temporary_decline_area_m2", "unconfirmed_decline_area_m2"]].to_numpy()
        names = np.array(["PERSISTENT", "TEMPORARY", "UNCONFIRMED"])
        tab_in["persistence_status_out_quarry"] = np.where(ar.sum(1) > 0, names[ar.argmax(1)], "NONE")
        base_cols = parcels.reset_index(drop=True)
        merged = pd.concat([base_cols.drop(columns="geometry"), table, tab_in], axis=1)
        merged = rank_tables(merged, cfg, bc, None)
        gdf = gpd.GeoDataFrame(merged, geometry=base_cols.geometry.values, crs=parcels.crs)
        result["parcels"] = gdf
        # 민감도
        aoi_wt = ps.pixel_weights([aoi_union], tr, shape)
        pwt = ps.pixel_weights(list(clipped.values), tr, shape)
        sens = run_sensitivity(stack, bc, quarry if qa_on else quarry.iloc[0:0], aoi_wt, pwt, len(parcels), consecutive, comp, qres)
        _write_parcel_outputs(gdf, years, vec, tab, files, bc)
    pd.DataFrame(sens["rows"]).to_csv(tab / "sensitivity_composite.csv", index=False, encoding="utf-8-sig") if sens else None
    result["sensitivity"] = sens

    # --- 5b. IO LULC(모델 산출물) 대비 노출 상태 일치도: 참조 정답이 아닌 '약한 참조'
    try:
        if "context_meta" in result:
            result["lulc_agreement"] = _lulc_agreement(stack, comp, years, aoi_union, base["transform"], lulc)
    except Exception as e:  # noqa: BLE001
        result["lulc_agreement"] = {"status": "FAILED", "reason": f"{type(e).__name__}: {e}"}

    # --- 6. 메타데이터
    aoi_ps = {}
    aw = ps.pixel_weights([aoi_union], tr, shape)
    for nm, m in (("exposed_increase", comp["expo_any"]), ("veg_decline", comp["loss_any"] | comp["expo_any"]),
                  ("veg_recovery", comp["rec_any"]), ("persistent_exposed", comp["persistent_exposed"]),
                  ("potential_quarry_expansion", pot), ("evaluable", comp["evaluable"]),
                  ("quarry_reference_in_aoi", qres["quarry_mask"])):
        aoi_ps[nm + "_ha"] = float((aw["a"] * m.ravel()[aw["f"]]).sum() / 1e4)
    meta = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "grid": {"epsg": 5186, "res_m": res, "shape": list(shape), "transform": list(tr)[:6]},
        "years_available_bsi": [y for y in years if stack["available"][y]],
        "years_unavailable_bsi": [y for y in years if not stack["available"][y]],
        "swir_provenance": swir_report, "swir_qa": stack["qa"],
        "thresholds": {k: bc[k] for k in ("ndvi", "bsi", "exposed_state", "min_persistent_exposed_obs", "min_valid_obs_pixel",
                                           "min_followup_obs", "water_mndwi_min", "bias_correction", "stable_dndvi",
                                           "aggregate_min_valid_10m", "quarry", "parcel", "rankB")},
        "bias_shifts_dbsi_by_interval": dict(zip(intervals, comp["bias_shifts"])),
        "bias_shifts_dndvi_by_interval": dict(zip(intervals, comp["ndvi_bias_shifts"])),
        "quarry_reference": qmeta, "quarry_analysis_performed": bool(qa_on),
        "aoi_summary": aoi_ps, "quarry_components": {"n_exposed_components": qres["n_components"],
                                                     "n_connected_pixels": qres["n_connected_pixels"]},
        "context": result.get("context_meta"), "lulc_agreement": result.get("lulc_agreement"),
        "sensitivity": sens, "limitations": BSI_LIMITATIONS,
        "accuracy_assessment": {"performed": False, "scope": "변화 탐지(시점·원인) 정확도",
                                "reason": "과거 시점 정답 자료 없음 - 변화 탐지 혼동행렬 미산출",
                                "state_validation": "현재(2026) 노출/식생 상태만 육안 판독으로 검증: outputs/kr_rail_danyang/validation/visual_validation_summary.json"},
    }
    (meta_dir / "bsi_metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    files.append(str(meta_dir / "bsi_metadata.json"))
    result.update(meta=meta, status="OK", files=files)
    return result


def _write_parcel_outputs(gdf: gpd.GeoDataFrame, years: list[int], vec: Path, tab: Path, files: list, bc: dict) -> None:
    gdf = gdf.copy()
    gdf["pnu"] = gdf["pnu"].astype("string")
    ts_cols = ["pnu", "jibun_display", "addr", "area_total_m2", "area_in_aoi_m2"] \
        + [f"ndvi_mean_{y}" for y in years] + [f"ndvi20_mean_{y}" for y in years] + [f"bsi_mean_{y}" for y in years] \
        + ["delta_ndvi_first_last" if "delta_ndvi_first_last" in gdf else "ndvi_delta_first_last", "delta_ndvi20_first_last",
           "delta_bsi_first_last", "composite_resolution_m", "ndvi_resolution_m"]
    p = tab / "parcel_ndvi_bsi_timeseries.csv"
    gdf[ts_cols].to_csv(p, index=False, encoding="utf-8-sig", float_format="%.4f")
    files.append(str(p))
    sum_cols = ["pnu", "jibun_display", "addr", "area_in_aoi_m2", "evaluable_area_20m_m2", "decline_area_m2", "decline_ratio",
                "veg_decline_area_20m_m2", "exposed_increase_area_20m_m2", "veg_recovery_area_20m_m2",
                "persistent_exposed_area_20m_m2", "uncertain_area_20m_m2", "change_type_ko", "composite_type",
                "composite_type_ko", "quarry_context", "dist_to_quarry_m", "quarry_overlap_ratio", "quarry_adjacent",
                "potential_quarry_expansion_area_20m_m2", "first_change_interval_20m", "composite_confidence",
                "composite_quality_flags", "quality_flags", "composite_resolution_m", "ndvi_resolution_m", "rail_distance_m",
                "jimok", "zoning", "agri_context", "agri_evidence", "lulc_crops_mean", "lulc_built_mean", "lulc_bare_mean",
                "forest_site_cropland_share", "agri_disadvantaged_share", "confusion_risk"]
    gdf[sum_cols].to_csv(tab / "parcel_composite_summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    files.append(str(tab / "parcel_composite_summary.csv"))
    A = gdf[gdf["A_candidate"]].sort_values("A_rank")
    A[["A_rank", "pnu", "jibun_display", "addr", "decline_area_out_quarry_m2", "decline_ratio_out_quarry",
       "persistence_status_out_quarry", "rail_distance_m", "A_score", "A_score_decline_ratio", "A_score_persistence",
       "A_score_rail_proximity", "quarry_context", "analysis_confidence", "quality_flags", "confusion_risk", "jimok", "agri_context"]].to_csv(
        tab / "priority_A_rail_adjacent_review.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    B = gdf[gdf["B_candidate"]].sort_values("B_rank")
    B[["B_rank", "pnu", "jibun_display", "addr", "exposed_increase_area_20m_m2", "exposed_ratio_20m", "quarry_context",
       "dist_to_quarry_m", "composite_type_ko", "first_change_interval_20m", "B_score", "B_score_exposed_ratio",
       "B_score_quarry_link", "B_score_persistence", "composite_confidence", "composite_quality_flags", "reliability_warning",
       "confusion_risk", "jimok", "agri_context",
       "rail_distance_m", "composite_resolution_m"]].to_csv(
        tab / "priority_B_quarry_exposed_review.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    files += [str(tab / "priority_A_rail_adjacent_review.csv"), str(tab / "priority_B_quarry_exposed_review.csv")]
    # 변화 유형별 요약
    rows = []
    for col, label in (("composite_type", "복합 변화 유형"), ("quarry_context", "채석장 맥락"), ("change_type", "NDVI 변화 유형(10m)")):
        g = gdf.groupby(col).agg(n=("pnu", "size"), area_in_aoi_m2=("area_in_aoi_m2", "sum"),
                                 exposed_area_20m_m2=("exposed_increase_area_20m_m2", "sum"),
                                 veg_decline_area_20m_m2=("veg_decline_area_20m_m2", "sum")).reset_index()
        for r in g.itertuples(index=False):
            rows.append((label, r[0], r.n, round(r.area_in_aoi_m2, 1), round(r.exposed_area_20m_m2, 1), round(r.veg_decline_area_20m_m2, 1)))
    pd.DataFrame(rows, columns=["구분", "유형", "필지 수", "AOI 내 필지 면적 합(㎡)", "노출지 증가 면적 합(20m,㎡)",
                                "식생 감소 면적 합(20m,㎡)"]).to_csv(tab / "change_type_summary.csv", index=False, encoding="utf-8-sig")
    files.append(str(tab / "change_type_summary.csv"))
    # GeoPackage
    keep = [c for c in gdf.columns if c != "note_area_overlap"]
    g = gdf[keep]
    g.to_file(vec / "parcel_ndvi_bsi_stats.gpkg", layer="parcel_ndvi_bsi_stats", driver="GPKG")
    g.to_crs(4326).to_file(vec / "parcel_ndvi_bsi_stats.geojson", driver="GeoJSON")
    files += [str(vec / "parcel_ndvi_bsi_stats.gpkg"), str(vec / "parcel_ndvi_bsi_stats.geojson")]
