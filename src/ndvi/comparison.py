"""다중 장면 중앙값 합성 입력으로 같은 분석을 다시 실행하고 단일 장면 결과와 비교한다.

두 입력(단일 장면 / 계절 창 중앙값)에서 모두 같은 후보로 나온 필지는 구름 잔여물·촬영일 편차·단일 시점 노이즈의 영향을
덜 받는 '강건 후보'로 표시한다. 합성에도 계절 위상·센서 편차·작황 차이는 남는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .composites import build_all


def make_composite_config(project: Path, out_dir: Path, prov: dict, cfg: dict) -> Path:
    """합성 입력용 설정(파일)을 만든다. 원 설정은 수정하지 않는다."""
    c = json.loads(json.dumps(cfg))   # deep copy
    root = Path("data/projects/kr_rail_danyang/raw")
    c["paths"]["imagery_dir"] = str(root / "sentinel2_composite")
    c["ndvi"]["output_dir"] = str(out_dir / "ndvi")
    sel = {}
    for y, v in prov.items():
        if v.get("status") != "OK":
            continue
        sel[y] = {"year": int(y), "date": v["date"], "provider": "median composite of Sentinel-2 L2A scenes",
                  "scene_id": v["scene_id"], "platform": f"median of {v['n_scenes_used']} scenes",
                  "processing_baseline": "mixed", "boa_offset_removed_dn": None, "scene_cloud_cover_pct": None,
                  "aoi_clear_fraction": v.get("valid_fraction_10m")}
    sp = out_dir / "scene_selection_composite.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")
    c["ndvi"]["scene_selection"] = str(sp)
    c["bsi"]["output_dir"] = str(out_dir / "bsi")
    c["bsi"]["swir_cache_dir"] = str(root / "sentinel2_composite_swir")
    out = out_dir / "config_composite.yaml"
    out.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def compare_runs(single_bsi_dir: Path, comp_bsi_dir: Path, out_dir: Path) -> dict:
    s = pd.read_csv(single_bsi_dir / "tables" / "parcel_composite_summary.csv", dtype={"pnu": str}, encoding="utf-8-sig")
    c = pd.read_csv(comp_bsi_dir / "tables" / "parcel_composite_summary.csv", dtype={"pnu": str}, encoding="utf-8-sig")
    m = s.merge(c, on="pnu", suffixes=("_single", "_comp"), how="inner")
    both_eval = (m["composite_type_single"] != "INSUFFICIENT_DATA") & (m["composite_type_comp"] != "INSUFFICIENT_DATA")
    m["type_agree"] = m["composite_type_single"] == m["composite_type_comp"]
    res = {"n_parcels": int(len(m)), "n_both_evaluable": int(both_eval.sum()),
           "type_agreement_rate_evaluable": float(m.loc[both_eval, "type_agree"].mean()) if both_eval.any() else None}
    for key, col in (("exposed_increase", "composite_type"), ("potential_quarry_expansion", "quarry_context")):
        tgt = "EXPOSED_SURFACE_INCREASE" if key == "exposed_increase" else "POTENTIAL_QUARRY_EXPANSION"
        a = m[col + "_single"] == tgt
        b = m[col + "_comp"] == tgt
        u = (a | b).sum()
        res[key] = {"single": int(a.sum()), "composite": int(b.sum()), "both": int((a & b).sum()),
                    "jaccard": float((a & b).sum() / u) if u else None}
    m["robust_exposed_increase"] = (m["composite_type_single"] == "EXPOSED_SURFACE_INCREASE") & (m["composite_type_comp"] == "EXPOSED_SURFACE_INCREASE")
    m["robust_quarry_expansion"] = (m["quarry_context_single"] == "POTENTIAL_QUARRY_EXPANSION") & (m["quarry_context_comp"] == "POTENTIAL_QUARRY_EXPANSION")
    m["robust_any"] = m["robust_exposed_increase"] | m["robust_quarry_expansion"]
    # 면적 상관(노출지 증가 면적)
    ok = both_eval & np.isfinite(m["exposed_increase_area_20m_m2_single"]) & np.isfinite(m["exposed_increase_area_20m_m2_comp"])
    if ok.sum() > 3:
        res["exposed_area_pearson_r"] = float(np.corrcoef(m.loc[ok, "exposed_increase_area_20m_m2_single"],
                                                           m.loc[ok, "exposed_increase_area_20m_m2_comp"])[0, 1])
    # 연도별 AOI 평균 NDVI 편차(두 입력)
    ms = json.loads((single_bsi_dir.parent / "ndvi" / "metadata" / "analysis_metadata.json").read_text(encoding="utf-8"))
    mc = json.loads((comp_bsi_dir.parent / "ndvi" / "metadata" / "analysis_metadata.json").read_text(encoding="utf-8"))
    res["aoi_mean_ndvi"] = {"single": ms["aoi"]["summary"]["per_year_aoi_ndvi_mean"], "composite": mc["aoi"]["summary"]["per_year_aoi_ndvi_mean"]}
    res["aoi_decline_ha"] = {"single": ms["aoi"]["summary"]["aoi_decline_area_m2"] / 1e4,
                             "composite": mc["aoi"]["summary"]["aoi_decline_area_m2"] / 1e4}
    cols = ["pnu", "jibun_display_single", "area_in_aoi_m2_single", "composite_type_single", "composite_type_comp",
            "quarry_context_single", "quarry_context_comp", "exposed_increase_area_20m_m2_single",
            "exposed_increase_area_20m_m2_comp", "type_agree", "robust_exposed_increase", "robust_quarry_expansion", "robust_any"]
    out_dir.mkdir(parents=True, exist_ok=True)
    m[cols].to_csv(out_dir / "single_vs_composite_parcels.csv", index=False, encoding="utf-8-sig", float_format="%.3f")
    m[m["robust_any"]][cols].to_csv(out_dir / "robust_candidates.csv", index=False, encoding="utf-8-sig", float_format="%.3f")
    res["robust_candidates"] = int(m["robust_any"].sum())
    (out_dir / "comparison_summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def run_composite_analysis(project: str | Path, cfg: dict, aoi, offline: bool = False, season=("04-20", "05-20")) -> dict:
    """합성 생성 -> 같은 파이프라인(NDVI+BSI) 실행 -> 비교."""
    from .pipeline import run, run_extension

    project = Path(project)
    out_dir = Path("outputs/kr_rail_danyang/composite")
    root = Path("data/projects/kr_rail_danyang/raw")
    prov = build_all(cfg["imagery"]["years"], Path(cfg["paths"]["imagery_dir"]), aoi, root, season) if not offline else {}
    if offline:
        for y in cfg["imagery"]["years"]:
            p = root / f"composite_provenance_{y}.json"
            if p.exists():
                prov[str(y)] = json.loads(p.read_text(encoding="utf-8"))
    ok_years = [y for y, v in prov.items() if v.get("status") == "OK"]
    if len(ok_years) != len(cfg["imagery"]["years"]):
        return {"status": "INCOMPLETE", "years_ok": ok_years, "provenance": prov}
    cpath = make_composite_config(project, out_dir, prov, cfg)
    m1 = run(cpath, offline=True, force=True)
    if m1["status"] not in ("SUCCESS",):
        return {"status": "BASE_FAILED", "base": m1.get("error"), "provenance": prov}
    m2 = run_extension(cpath, offline=True, quarry=True)
    if m2["status"] not in ("SUCCESS", "PARTIAL_BSI_YEARS_MISSING"):
        return {"status": "BSI_FAILED", "ext": m2.get("error"), "provenance": prov}
    comp = compare_runs(Path(cfg["bsi"]["output_dir"]), out_dir / "bsi", out_dir / "tables")
    return {"status": "OK", "comparison": comp, "provenance": prov}
