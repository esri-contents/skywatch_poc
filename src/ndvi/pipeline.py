"""Sentinel-2 NDVI x 연속지적도 필지 변화 모니터링 CLI.

    python -m src.ndvi.pipeline --project config/projects/kr_rail_danyang.yaml

SkyWatch 주문/유료 API는 호출하지 않는다. 기존 로컬 Sentinel-2 GeoTIFF만 사용한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml

from . import classification as clf
from . import parcel_statistics as ps
from . import scoring, temporal
from .cadastre import load_cadastre
from .processing import build_ndvi_stack, discover_scenes, write_raster

logger = logging.getLogger("ndvi")

PIPELINE_VERSION = "1.0"
LIMITATIONS = [
    "Sentinel-2 10m 영상의 NDVI 변화를 이용한 탐색적 분석이며 개별 건축물의 신축·철거, 지장물 존재 여부, 토지보상 적격성을 확정하지 않는다.",
    "분석 AOI는 법정리 행정경계 기반 잠정 경계이며 공식 철도사업구역/보상구역이 아니다.",
    "철도선은 OpenStreetMap 기반 비공식 자료이다. 철도 거리는 참고 지표이며 보상 대상 선정 근거가 아니다.",
    "연속지적도는 확보 시점의 필지 경계를 과거 영상에 중첩한 것이며 과거 필지 분할·합병 이력을 반영하지 않는다.",
    "관측 시점 간격이 약 2년이므로 변화 발생 시점은 추정하지 않고 최초 관측 구간만 제시한다.",
    "채석장 운영, 농경지 경작/수확, 벌채, 그림자, 수분·대기 상태 및 촬영일(DOY)·센서 차이로 인한 오탐 가능성이 있다.",
    "10m 픽셀은 소형 필지에서 인접 피복과 혼합되며, 면적가중 통계로 경계 기여를 보정해도 혼합 반응 자체는 제거되지 않는다.",
    "임계값·가중치는 검증된 과학적/행정적 기준이 아니라 초기 탐색용 설정값이다.",
    "NDVI 보정(촬영일/센서 상대 정규화)은 수행하지 않았다.",
]


def load_cfg(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _hash_inputs(cfg_path: Path, files: list[Path], cad: Path | None) -> str:
    h = hashlib.sha256()
    h.update(cfg_path.read_bytes())
    for f in files + ([cad] if cad else []):
        if f.exists():
            st = f.stat()
            h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    h.update(PIPELINE_VERSION.encode())
    return h.hexdigest()


def pair_label(a: int, b: int) -> str:
    return f"{a}_{b}"


def run(project: str | Path, offline: bool = False, cadastre: str | None = None, fetch_cadastre: bool = False,
        report_only: bool = False, force: bool = False, dry_run: bool = False) -> dict:
    project = Path(project)
    cfg = load_cfg(project)
    nc = cfg["ndvi"]
    out = Path(nc["output_dir"])
    ras, vec, tab, meta_dir = out / "rasters", out / "vectors", out / "tables", out / "metadata"
    t0 = datetime.now().astimezone()
    manifest: dict = {"pipeline": "src.ndvi.pipeline", "version": PIPELINE_VERSION,
                      "started_at": t0.isoformat(timespec="seconds"), "project": cfg["project"]["id"],
                      "options": {"offline": offline, "cadastre": cadastre, "fetch_cadastre": fetch_cadastre,
                                  "report_only": report_only, "force": force, "dry_run": dry_run},
                      "skywatch_api_called": False, "steps": {}, "outputs": [], "status": "RUNNING"}

    def step(name: str, status: str, **kw) -> None:
        manifest["steps"][name] = {"status": status, **kw}
        logger.info("[%s] %s %s", name, status, kw.get("detail", ""))

    years = list(cfg["imagery"]["years"])
    imagery_dir = Path(cfg["paths"]["imagery_dir"])
    aoi = gpd.read_file(cfg["paths"]["aoi"])
    if aoi.crs is None:
        aoi = aoi.set_crs(5186)
    aoi = aoi.to_crs(5186)
    scenes = discover_scenes(imagery_dir, years, nc.get("scene_selection"), cfg["imagery"]["band_order"])
    manifest["inputs"] = {"project_yaml": str(project), "aoi": cfg["paths"]["aoi"], "railway": cfg["paths"]["railway"],
                          "sentinel2": [str(s.path) for s in scenes]}
    if dry_run:
        step("dry_run", "OK", detail=f"입력 {len(scenes)}개 연도 확인, 출력 없음")
        manifest["status"] = "DRY_RUN"
        print(json.dumps({"status": "DRY_RUN", "scenes": [(s.year, s.date, s.path.name, s.band_order,
                                                          s.processing_baseline, s.boa_offset_removed_dn)
                                                         for s in scenes], "output_dir": str(out)},
                         ensure_ascii=False, indent=1, default=str))
        return manifest

    out.mkdir(parents=True, exist_ok=True)
    cad_cache = Path(nc["cadastre"]["cache_dir"]) / f"{nc['cadastre']['primary_layer']}_raw.geojson"
    sig = _hash_inputs(project, [s.path for s in scenes] + [Path(cfg["paths"]["aoi"]), Path(cfg["paths"]["railway"])],
                       Path(cadastre) if cadastre else cad_cache)
    manifest_path = meta_dir / "run_manifest.json"
    skip_analysis = report_only
    if not force and not report_only and manifest_path.exists():
        try:
            prev = json.loads(manifest_path.read_text(encoding="utf-8"))
            if prev.get("input_signature") == sig and prev.get("status") == "SUCCESS" and not fetch_cadastre:
                skip_analysis = True
                logger.info("입력/설정이 이전 성공 실행과 동일 - 분석을 건너뛰고 보고서만 재생성(--force로 재계산)")
        except Exception:  # noqa: BLE001
            pass
    manifest["input_signature"] = sig

    from . import reporting

    try:
        if skip_analysis:
            res = reporting.load_results(out)
            step("analysis", "REUSED_EXISTING_OUTPUTS" if not report_only else "SKIPPED_REPORT_ONLY")
        else:
            res = _analyze(cfg, nc, aoi, scenes, years, cadastre, offline, fetch_cadastre, out,
                           ras, vec, tab, meta_dir, manifest, step)
        rep = reporting.build_report(res, cfg, out)
        manifest["outputs"].extend(rep)
        step("report", "OK", detail=str(rep[0]) if rep else "")
        manifest["status"] = "SUCCESS" if res["meta"]["cadastre"]["status"] == "ok" else "PARTIAL_NO_CADASTRE"
    except Exception as e:  # noqa: BLE001
        manifest["status"] = "FAILED"
        manifest["error"] = f"{type(e).__name__}: {e}"
        manifest["traceback"] = traceback.format_exc()
        logger.error("실행 실패: %s", manifest["error"])
    manifest["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    meta_dir.mkdir(parents=True, exist_ok=True)
    if not report_only:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest


def _analyze(cfg, nc, aoi, scenes, years, cadastre, offline, fetch_cadastre, out, ras, vec, tab, meta_dir,
             manifest, step) -> dict:
    res_m = float(nc["grid_resolution_m"])
    pxa = res_m * res_m
    # ---------------- PHASE 1: NDVI
    stack = build_ndvi_stack(scenes, res_m, nc["reflectance_scale"], nc.get("grid_snap", True),
                             nc.get("resampling", "nearest"), nc.get("reflectance_warn_max", 1.5))
    T, H, W = stack.ndvi.shape
    outs: list[str] = []
    for i, y in enumerate(years):
        outs.append(str(write_raster(ras / f"ndvi_{y}.tif", stack.ndvi[i], stack.transform, descr=f"NDVI {y}")))
        outs.append(str(write_raster(ras / f"valid_{y}.tif", np.isfinite(stack.ndvi[i]).astype("uint8"),
                                     stack.transform, "uint8", 255, f"valid {y}")))
    pairs = [(years[i], years[i + 1]) for i in range(T - 1)] + [(years[0], years[-1])]
    pair_idx = [(i, i + 1) for i in range(T - 1)] + [(0, T - 1)]
    th = nc["thresholds"]["default"]
    for (a, b), (ia, ib) in zip(pairs, pair_idx):
        d = temporal.delta_ndvi(stack.ndvi[ia], stack.ndvi[ib])
        outs.append(str(write_raster(ras / f"delta_ndvi_{pair_label(a, b)}.tif", d, stack.transform,
                                     descr=f"NDVI {b} - NDVI {a}")))
        dec = temporal.pair_decline(stack.ndvi[ia], stack.ndvi[ib], th)
        both = np.isfinite(d)
        code = np.where(both, dec.astype("uint8"), 255).astype("uint8")
        outs.append(str(write_raster(ras / f"decline_candidate_{pair_label(a, b)}.tif", code, stack.transform,
                                     "uint8", 255, f"vegetation decline candidate {a}->{b}")))
    step("ndvi", "OK", detail=f"{T}개 연도, grid {H}x{W}, EPSG:5186 {res_m:g}m")

    px = temporal.analyze_pixels(stack.ndvi, th, nc["min_followup_obs"], nc["min_valid_obs_pixel"])
    cls_out = np.where(px["evaluable"], px["persistence_class"], 255).astype("uint8")
    outs.append(str(write_raster(ras / "persistence_class.tif", cls_out, stack.transform, "uint8", 255,
                                 "0 none,1 persistent,2 temporary,3 unconfirmed")))
    outs.append(str(write_raster(ras / "persistent_decline.tif", (px["persistence_class"] == 1).astype("uint8"),
                                 stack.transform, "uint8", 255, "persistent vegetation decline")))
    outs.append(str(write_raster(ras / "change_frequency.tif", np.where(px["evaluable"], px["frequency"], 255).astype("uint8"),
                                 stack.transform, "uint8", 255, "decline event count")))
    outs.append(str(write_raster(ras / "evaluable_mask.tif", px["evaluable"].astype("uint8"), stack.transform,
                                 "uint8", 255, f"valid obs >= {nc['min_valid_obs_pixel']}")))
    outs.append(str(write_raster(ras / "n_valid_obs.tif", px["n_valid"], stack.transform, "uint8", 255, "valid obs count")))
    step("pixel_change", "OK", detail=f"감소 픽셀 {int(px['any_decline'].sum())}")

    # ---------------- PHASE 2: cadastre
    gdf, clipped, cad_meta = load_cadastre(cfg, aoi, cadastre, offline, fetch_cadastre)
    parcels_ok = gdf is not None and len(gdf) > 0
    step("cadastre", "OK" if parcels_ok else "UNAVAILABLE", detail=cad_meta.get("error") or cad_meta.get("source", ""))

    rail, rail_meta = scoring.load_railway(cfg["paths"]["railway"], nc["railway"]["include_railway_values"],
                                           nc["railway"].get("official_boundary_path"))
    aoi_geom = aoi.geometry.union_all()
    aoi_wt = ps.pixel_weights([aoi_geom], stack.transform, (H, W))

    # AOI 수준 요약 및 민감도(필지와 무관)
    prof = nc["thresholds"]
    sens = temporal.sensitivity_summary(stack.ndvi, prof, pxa, nc["min_followup_obs"], nc["min_valid_obs_pixel"])
    for name, th_p in prof.items():
        r = temporal.analyze_pixels(stack.ndvi, th_p, nc["min_followup_obs"], nc["min_valid_obs_pixel"])
        w = aoi_wt["a"]
        f = aoi_wt["f"]
        sens[name]["aoi_decline_area_m2"] = float((w * r["any_decline"].ravel()[f]).sum())
        sens[name]["aoi_persistent_area_m2"] = float((w * (r["persistence_class"].ravel()[f] == 1)).sum())
        sens[name]["aoi_evaluable_area_m2"] = float((w * r["evaluable"].ravel()[f]).sum())
    aoi_summary = {
        "aoi_area_m2": float(aoi_geom.area),
        "aoi_evaluable_area_m2": sens["default"]["aoi_evaluable_area_m2"],
        "aoi_decline_area_m2": sens["default"]["aoi_decline_area_m2"],
        "aoi_persistent_decline_area_m2": sens["default"]["aoi_persistent_area_m2"],
        "aoi_decline_ratio": sens["default"]["aoi_decline_area_m2"] / sens["default"]["aoi_evaluable_area_m2"]
        if sens["default"]["aoi_evaluable_area_m2"] else None,
        "per_year_aoi_ndvi_mean": {},
    }
    for i, y in enumerate(years):
        v = stack.ndvi[i].ravel()[aoi_wt["f"]]
        ok = np.isfinite(v)
        aoi_summary["per_year_aoi_ndvi_mean"][str(y)] = float((aoi_wt["a"][ok] * v[ok]).sum() / aoi_wt["a"][ok].sum()) if ok.any() else None
        aoi_summary.setdefault("per_year_aoi_valid_fraction", {})[str(y)] = float(aoi_wt["a"][ok].sum() / aoi_wt["a"].sum())

    parcels_df = None
    qa_checks: dict = {}
    if parcels_ok:
        # ---------------- PHASE 2-3: parcel stats
        n = len(gdf)
        wt = ps.pixel_weights(list(clipped.values), stack.transform, (H, W))
        flat = [stack.ndvi[i].ravel() for i in range(T)]
        cols = [gdf[["parcel_uid", "pnu", "jibun_display", "jibun", "addr", "emd_nm", "li_nm", "jimok_abbr",
                     "area_total_m2", "area_in_aoi_m2", "geom_repaired", "pnu_missing", "pnu_len_ok",
                     "jiga", "gosi_year", "gosi_month"]].reset_index(drop=True)]
        for i, y in enumerate(years):
            cols.append(ps.year_stats(wt, n, flat[i], str(y)))
        for (a, b), (ia, ib) in zip(pairs, pair_idx):
            cols.append(ps.pair_delta_stats(wt, n, flat[ia], flat[ib], pair_label(a, b)))
        first_labels = [f"{years[i]}_{years[i + 1]}" for i in range(T - 1)]
        cols.append(ps.decline_stats(wt, n, px, first_labels, nc["classification"]["first_change_min_area_m2"]))
        df = pd.concat(cols, axis=1)
        df["ndvi_delta_first_last"] = df[f"delta_mean_{pair_label(years[0], years[-1])}"]
        df = clf.assess_quality(df, years, nc["parcel_quality"], pxa)
        aoi_means = {y: aoi_summary["per_year_aoi_ndvi_mean"][str(y)] for y in years}
        df = clf.classify_parcel_types(df, years, nc["classification"], nc["parcel_quality"], pxa, aoi_means)
        rd = scoring.rail_distance(gdf.geometry, rail, nc["railway"]["distance_bands_m"])
        df = pd.concat([df, rd.reset_index(drop=True)], axis=1)
        df = scoring.priority_scores(df, nc["priority"])
        df["rail_reference_kind"] = rail_meta["kind"]
        parcels_df = df

        # 검증 지표
        tol = 1e-6
        qa_checks = {
            "n_parcels": int(n),
            "decline_area_exceeds_evaluable": int((df["decline_area_m2"] > df["evaluable_area_m2"] + tol).sum()),
            "evaluable_exceeds_analysis_area": int((df["evaluable_area_m2"] > df["area_in_aoi_m2"] + 1e-3).sum()),
            "pixel_weight_area_vs_polygon_area_max_abs_err_m2": float(np.max(np.abs(
                np.bincount(wt["p"], weights=wt["a"], minlength=n) - df["area_in_aoi_m2"].to_numpy()))),
            "pixel_weight_area_vs_polygon_area_max_rel_err": float(np.max(np.abs(
                np.bincount(wt["p"], weights=wt["a"], minlength=n) / df["area_in_aoi_m2"].to_numpy() - 1))),
            "ndvi_mean_in_range": bool(np.nanmin(df[[f"ndvi_mean_{y}" for y in years]].to_numpy()) >= -1
                                       and np.nanmax(df[[f"ndvi_mean_{y}" for y in years]].to_numpy()) <= 1),
            "pnu_string_preserved_leading_zero_count": int(df["pnu"].dropna().str.startswith("0").sum()),
            "pnu_unique": bool(df["pnu"].dropna().is_unique),
        }
        # 감소 면적/클래스 면적 합 일관성
        s = df["persistent_decline_area_m2"] + df["temporary_decline_area_m2"] + df["unconfirmed_decline_area_m2"]
        qa_checks["class_area_sum_mismatch"] = int((np.abs(s - df["decline_area_m2"]) > 1e-6).sum())
        # 필지별 민감도: 프로파일별 유형 개수
        sens_parcel = {}
        for name, th_p in prof.items():
            r = temporal.analyze_pixels(stack.ndvi, th_p, nc["min_followup_obs"], nc["min_valid_obs_pixel"])
            dsp = ps.decline_stats(wt, n, r, first_labels, nc["classification"]["first_change_min_area_m2"])
            tmp = df.copy()
            for c in ["decline_area_m2", "persistent_decline_area_m2", "temporary_decline_area_m2",
                      "unconfirmed_decline_area_m2", "evaluable_area_m2"]:
                tmp[c] = dsp[c].values
            tmp = clf.classify_parcel_types(tmp, years, nc["classification"], nc["parcel_quality"], pxa, aoi_means)
            sens_parcel[name] = {
                "type_counts": tmp["change_type"].value_counts().to_dict(),
                "parcels_with_decline": int((tmp["decline_area_m2"] > 0).sum()),
                "parcels_persistent_decline": int((tmp["change_type"] == "PERSISTENT_DECLINE").sum()),
            }
        for name in sens:
            sens[name]["parcel_level"] = sens_parcel.get(name)
        step("parcel_stats", "OK", detail=f"필지 {n}개")
    else:
        step("parcel_stats", "NOT_PERFORMED", detail="연속지적도 미확보 - 필지별 결과 미완료")

    # ---------------- outputs
    gdf_out = None
    if parcels_ok:
        gdf_out = gpd.GeoDataFrame(parcels_df, geometry=gdf.geometry.values, crs=gdf.crs)
        keep_ts = ["pnu", "jibun_display", "addr", "emd_nm", "li_nm", "jimok_abbr", "area_total_m2", "area_in_aoi_m2"] \
            + [f"ndvi_mean_{y}" for y in years] + [f"ndvi_median_{y}" for y in years] \
            + [f"valid_area_{y}" for y in years] + [f"delta_mean_{pair_label(a, b)}" for a, b in pairs]
        cols_summary = ["pnu", "jibun_display", "addr", "emd_nm", "li_nm", "jimok_abbr", "area_total_m2",
                        "area_in_aoi_m2", "evaluable_area_m2", "evaluable_fraction", "decline_area_m2", "decline_ratio",
                        "persistent_decline_area_m2", "temporary_decline_area_m2", "unconfirmed_decline_area_m2",
                        "change_frequency_mean", "persistence_status", "first_change_interval", "ndvi_delta_first_last",
                        "change_type", "change_type_ko", "analysis_confidence", "quality_status", "quality_flags",
                        "rail_distance_m", "rail_distance_surface_m", "rail_nearest_is_tunnel", "within_100m", "within_300m", "within_500m",
                        "ndvi_rel_delta_first_last",
                        "score_decline_ratio", "score_persistence", "score_rail_proximity", "priority_score",
                        "priority_rank", "priority_tier", "is_review_candidate", "null_reason"]
        gdf_out["pnu"] = gdf_out["pnu"].astype("string")

        def to_csv(frame: pd.DataFrame, name: str) -> None:
            tab.mkdir(parents=True, exist_ok=True)
            p = tab / name
            frame.to_csv(p, index=False, encoding="utf-8-sig", float_format="%.4f")
            outs.append(str(p))

        # 엑셀에서 PNU 앞자리/지수 표기 훼손을 막기 위해 문자열 그대로 저장(quoting은 pandas 기본)
        to_csv(gdf_out[keep_ts], "parcel_ndvi_timeseries.csv")
        to_csv(gdf_out[cols_summary], "parcel_change_summary.csv")
        pri = gdf_out[gdf_out["is_review_candidate"]].sort_values("priority_rank")
        to_csv(pri[["priority_rank", "priority_tier", "pnu", "jibun_display", "addr", "decline_area_m2", "decline_ratio",
                    "persistence_status", "change_type_ko", "rail_distance_m", "priority_score", "score_decline_ratio",
                    "score_persistence", "score_rail_proximity", "first_change_interval", "analysis_confidence",
                    "quality_flags", "area_in_aoi_m2"]], "priority_parcels.csv")

        vec.mkdir(parents=True, exist_ok=True)

        def to_vec(frame: gpd.GeoDataFrame, stem: str) -> None:
            frame.to_file(vec / f"{stem}.gpkg", layer=stem, driver="GPKG")
            frame.to_crs(4326).to_file(vec / f"{stem}.geojson", driver="GeoJSON")
            outs.extend([str(vec / f"{stem}.gpkg"), str(vec / f"{stem}.geojson")])

        to_vec(gdf_out.drop(columns=["geom_repaired", "pnu_missing", "pnu_len_ok"]), "parcel_ndvi_stats")
        to_vec(gdf_out[["pnu", "jibun_display", "addr", "area_in_aoi_m2", "change_type", "change_type_ko",
                        "persistence_status", "decline_area_m2", "decline_ratio", "first_change_interval",
                        "analysis_confidence", "quality_flags", "geometry"]], "parcel_change_types")
        to_vec(gdf_out[gdf_out["is_review_candidate"]].sort_values("priority_rank")[
            ["priority_rank", "priority_tier", "pnu", "jibun_display", "addr", "area_in_aoi_m2", "decline_area_m2",
             "decline_ratio", "persistence_status", "rail_distance_m", "priority_score", "change_type_ko",
             "analysis_confidence", "quality_flags", "geometry"]], "priority_parcels")

        # analysis_quality_summary.csv: 구분/항목/값 형태
        rows = []
        for k, v in gdf_out["quality_status"].value_counts().items():
            rows.append(("필지 품질 상태", k, int(v)))
        for k, v in gdf_out["change_type"].value_counts().items():
            rows.append(("필지 변화 유형", k, int(v)))
        for k, v in gdf_out["analysis_confidence"].value_counts().items():
            rows.append(("분석 신뢰도", k, int(v)))
        for y in years:
            q = stack.qa["per_year"][y]
            rows.append(("연도별 유효 픽셀 비율(격자)", str(y), round(q["valid_fraction_of_grid"], 4)))
            rows.append(("연도별 AOI 내 유효 면적비", str(y), round(aoi_summary["per_year_aoi_valid_fraction"][str(y)], 4)))
        for k, v in qa_checks.items():
            rows.append(("검증 지표", k, v))
        pd.DataFrame(rows, columns=["구분", "항목", "값"]).to_csv(tab / "analysis_quality_summary.csv", index=False,
                                                                 encoding="utf-8-sig")
        outs.append(str(tab / "analysis_quality_summary.csv"))
    else:
        tab.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([("연속지적도", "상태", cad_meta.get("status")), ("연속지적도", "사유", cad_meta.get("error"))],
                     columns=["구분", "항목", "값"]).to_csv(tab / "analysis_quality_summary.csv", index=False,
                                                          encoding="utf-8-sig")
        outs.append(str(tab / "analysis_quality_summary.csv"))

    meta = {
        "title": "Sentinel-2 NDVI 및 연속지적도 기반 철도사업 주변 필지별 토지피복 변화 모니터링",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                        "libs": _lib_versions()},
        "grid": stack.qa["grid"],
        "scenes": [{"year": s.year, "date": s.date, "scene_id": s.scene_id, "platform": s.platform,
                    "processing_baseline": s.processing_baseline, "boa_offset_removed_dn": s.boa_offset_removed_dn,
                    "scene_cloud_cover_pct": s.scene_cloud_cover_pct, "aoi_clear_fraction": s.aoi_clear_fraction,
                    "file": s.path.name} for s in scenes],
        "ndvi_method": "NDVI=(B08-B04)/(B08+B04), 밴드는 GeoTIFF band description으로 판별. 반사율=DN/10000, "
                       "BOA offset은 scene_selection.json 기준 이미 제거됨(재적용 안 함). 원본 0값(NoData/SCL 마스크)은 NaN. "
                       "UTM52N->EPSG:5186 nearest 재투영.",
        "thresholds": {"profiles": prof, "min_followup_obs": nc["min_followup_obs"],
                       "min_valid_obs_pixel": nc["min_valid_obs_pixel"], "parcel_quality": nc["parcel_quality"],
                       "classification": nc["classification"], "priority": nc["priority"],
                       "note": "초기 탐색용 가설이며 검증된 임계값이 아님"},
        "raster_qa": stack.qa,
        "cadastre": {k: v for k, v in cad_meta.items()},
        "railway": rail_meta,
        "aoi": {"path": cfg["paths"]["aoi"], "status": cfg["aoi"]["status"], "source": cfg["aoi"]["source"],
                "summary": aoi_summary},
        "sensitivity": sens,
        "parcel_validation": qa_checks,
        "limitations": LIMITATIONS,
    }
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "analysis_metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str),
                                                     encoding="utf-8")
    outs.append(str(meta_dir / "analysis_metadata.json"))
    (meta_dir / "sensitivity_analysis.json").write_text(json.dumps(sens, ensure_ascii=False, indent=2, default=str),
                                                        encoding="utf-8")
    outs.append(str(meta_dir / "sensitivity_analysis.json"))
    manifest["outputs"].extend(outs)
    vec.mkdir(parents=True, exist_ok=True)
    rail.to_crs(4326).to_file(vec / "railway_used.geojson", driver="GeoJSON")
    aoi.to_crs(4326).to_file(vec / "aoi.geojson", driver="GeoJSON")
    manifest["outputs"].extend([str(vec / "railway_used.geojson"), str(vec / "aoi.geojson")])
    return {"ndvi": stack.ndvi, "transform": stack.transform, "persistence_class": cls_out, "parcels": gdf_out,
            "meta": meta, "years": years, "pairs": pairs, "rail": rail, "aoi": aoi}


def run_extension(project: str | Path, offline: bool = False, quarry: bool = True, refresh_reference: bool = False) -> dict:
    """NDVI 결과 위에 BSI/복합/채석장/필지 확장 분석과 확장 보고서를 만든다(기존 산출물은 수정하지 않음)."""
    from . import reporting, reporting_bsi
    from .bsi_pipeline import run_bsi_extension

    cfg = load_cfg(project)
    out_root = Path(cfg["ndvi"]["output_dir"])
    man: dict = {"pipeline": "src.ndvi.bsi_pipeline", "version": PIPELINE_VERSION,
                 "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "skywatch_api_called": False, "paid_api_called": False, "status": "RUNNING"}
    try:
        base = reporting.load_results(out_root)
        ext = run_bsi_extension(base, cfg, out_root, offline=offline, quarry_analysis=quarry,
                                refresh_reference=refresh_reference)
        if ext["status"] != "OK":
            man.update(status="BSI_UNAVAILABLE", swir=ext.get("swir"))
        else:
            rep = reporting_bsi.build_bsi_report(base, ext, cfg, out_root)
            from .reporting_simple import build_simple_report

            rep += build_simple_report(base, ext, cfg, out_root, offline)   # 쉬운 물건조사 선별 보고서 + 건축물 윤곽
            from .ai_handoff import build_ai_handoff

            rep += build_ai_handoff(cfg["bsi"]["output_dir"], Path(cfg["bsi"]["output_dir"]).parent / "ai_handoff")  # AI 연계 전달 패키지
            miss = ext["meta"]["years_unavailable_bsi"]
            man.update(status="PARTIAL_BSI_YEARS_MISSING" if miss else "SUCCESS", outputs=ext["files"] + rep,
                       years_available=ext["meta"]["years_available_bsi"], years_unavailable=miss,
                       swir={y: e["status"] for y, e in ext["meta"]["swir_provenance"].items()},
                       quarry_reference_status=ext["meta"]["quarry_reference"].get("status"),
                       quarry_analysis_performed=ext["meta"]["quarry_analysis_performed"])
    except Exception as e:  # noqa: BLE001
        man.update(status="FAILED", error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
        logger.error("BSI 확장 실패: %s", man["error"])
    man["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    md = Path(cfg["bsi"]["output_dir"]) / "metadata"
    md.mkdir(parents=True, exist_ok=True)
    (md / "run_manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return man


def _lib_versions() -> dict:
    import importlib

    v = {}
    for m in ("numpy", "pandas", "rasterio", "geopandas", "shapely", "pyproj"):
        try:
            v[m] = importlib.import_module(m).__version__
        except Exception:  # noqa: BLE001
            v[m] = None
    return v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sentinel-2 NDVI x 연속지적도 필지 변화 모니터링")
    ap.add_argument("--project", default="config/projects/kr_rail_danyang.yaml")
    ap.add_argument("--offline", action="store_true", help="네트워크를 쓰지 않음(로컬 지적도 캐시/파일만 사용)")
    ap.add_argument("--cadastre", help="로컬 연속지적도 파일(GPKG/GeoJSON/SHP). PNU/jibun 컬럼 필요")
    ap.add_argument("--fetch-cadastre", action="store_true", help="VWorld WFS에서 연속지적도를 새로 수집")
    ap.add_argument("--report-only", action="store_true", help="기존 산출물로 보고서만 재생성")
    ap.add_argument("--force", action="store_true", help="입력이 동일해도 전체 재계산")
    ap.add_argument("--dry-run", action="store_true", help="입력 점검만 수행하고 아무것도 쓰지 않음")
    ap.add_argument("--enable-bsi", action="store_true",
                    help="B11(같은 Scene ID) 확보 후 BSI/NDVI+BSI 복합 변화탐지와 필지 통합을 수행")
    ap.add_argument("--enable-quarry-analysis", action="store_true",
                    help="OSM 채석장 참조 폴리곤 기반 채석장 관련 후보 분류(--enable-bsi 포함)")
    ap.add_argument("--generate-report", action="store_true", help="확장(NDVI+BSI+채석장) 보고서 생성(--enable-bsi 포함)")
    ap.add_argument("--refresh-reference", action="store_true", help="채석장 참조자료(OSM)를 다시 수집")
    ap.add_argument("--composite-analysis", action="store_true",
                    help="계절 창 다중 장면 중앙값 합성으로 같은 분석을 재실행하고 단일 장면 결과와 비교(--enable-bsi 이후)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    m = run(a.project, a.offline, a.cadastre, a.fetch_cadastre, a.report_only, a.force, a.dry_run)
    print(f"status={m['status']}")
    ok = m["status"] in ("SUCCESS", "DRY_RUN", "PARTIAL_NO_CADASTRE")
    if ok and not a.dry_run and (a.enable_bsi or a.enable_quarry_analysis or a.generate_report):
        em = run_extension(a.project, a.offline, quarry=True if a.enable_quarry_analysis or a.generate_report or a.enable_bsi else False,
                           refresh_reference=a.refresh_reference)
        print(f"bsi_extension_status={em['status']}")
        ok = ok and em["status"] in ("SUCCESS", "PARTIAL_BSI_YEARS_MISSING")
        if ok and a.composite_analysis:
            import geopandas as gpd

            from .comparison import run_composite_analysis

            cfg = load_cfg(a.project)
            cr = run_composite_analysis(a.project, cfg, gpd.read_file(cfg["paths"]["aoi"]), offline=a.offline)
            print(f"composite_analysis_status={cr['status']}")
            if cr["status"] == "OK":
                # 합성 비교 결과(강건 후보)를 보고서에 반영하도록 확장 보고서를 다시 만든다
                run_extension(a.project, a.offline, quarry=True)
            ok = ok and cr["status"] == "OK"
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
