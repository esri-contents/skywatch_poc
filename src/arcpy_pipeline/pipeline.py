"""전체 파이프라인 진입점 (arcpy) - LH 운영 경로.

Baseline(`src/pipeline.py`)과 동일한 STEP 9~14(변화탐지→후처리→polygon화→
overlay→행정검증→분류→점수화→공간통계)를 arcpy로 수행한 뒤, LH가 요청한
11개 기능(REQ01~REQ11, `config/lh_requirements.yaml` 참고)을 이어 붙인다.

```text
T1/T2 원본 밴드
  -> raster_ops.build_stacked_scene (재투영+clip+스택, T1/T2 grid 강제 일치)
  -> raster_ops.verify_alignment (STEP 8, FFT 위상상관)
  -> change_detect.run_change_detection -> clean_mask -> polygonize
     -> add_brightness_delta                                    [REQ01]
  -> buildings.overlay_buildings_with_changes
  -> buildings.join_building_register + compute_administrative_uncertainty
  -> buildings.classify_building_changes / classify_unmatched_changes
  -> buildings.merge_results -> compute_priority_score
  -> spatial_stats.compute_global_moran / compute_hotspots
  -> compensation.apply_to_featureclass                         [REQ03]
  -> illegal_screen.apply_to_featureclass                       [REQ02]
  -> cadastre_link.link_results_to_parcels                      [REQ08]
  -> field_survey.collect_sites -> order_route -> export_work_order [REQ05]
  -> progress_monitor.summarize_progress (다시기 입력 시)         [REQ04, REQ09]
  -> reality_3d.select_reality_review_candidates + build_capture_plan [REQ11]
  -> imagery_tasking.availability_report + build_tasking_request  [REQ06]
  -> report_builder / webmap_publish (조건부, 자격증명/템플릿 있을 때만) [REQ10]
  -> lh_traceability.md 생성                                     [REQ07]
```

**partial 항목을 건너뛰는 방식**: REQ06 신규촬영, REQ10 Web Map, REQ11 실제
Reality 처리는 외부 자원(상용 계약, AGOL 계정, 드론 영상)이 있어야 완결된다.
이 파이프라인은 그 부분을 조용히 건너뛰지 않는다 - 시도하고, 실패 사유를
`lh_traceability.md`에 정확히 기록한다. "왜 안 됐는지"가 남아야 다음 사람이
막힌 지점을 바로 알 수 있기 때문이다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import arcpy

from . import (
    buildings,
    cadastre_link,
    change_detect,
    compensation,
    field_survey,
    illegal_screen,
    imagery_tasking,
    progress_monitor,
    raster_ops,
    reality_3d,
    spatial_stats,
)
from .env import (
    arcpy_session,
    ensure_gdb,
    env_report,
    fc_path,
    load_config,
    load_requirements,
    resolve_path,
    setup_logging,
)

logger = logging.getLogger("arcpy_pipeline.pipeline")


def _parse_date(s) -> date | None:
    if s is None:
        return None
    if isinstance(s, date):
        return s
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y"):
        try:
            return datetime.strptime(str(s), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"[PIPELINE] 날짜 형식을 인식할 수 없습니다: {s}")


def run_change_intelligence(
    t1_bands: dict[str, str],
    t2_bands: dict[str, str],
    aoi_path: str,
    buildings_path: str,
    parcel_path: str | None = None,
    building_register_path: str | None = None,
    config_path: str = "config/config.yaml",
    requirements_path: str = "config/lh_requirements.yaml",
    t1_date: str = "2022-05-17",
    t2_date: str = "2024-05-31",
    out_gdb: str | None = None,
    additional_epochs: list[dict] | None = None,
) -> dict:
    """LH 통합 파이프라인을 실행하고 STEP 결과 + REQ별 산출물 현황을 반환한다.

    Args:
        t1_bands / t2_bands: {"B02": path, ...} 원본 밴드 (재투영 전).
        aoi_path: AOI Feature Class/GPKG.
        buildings_path: 건물 footprint (AOI clip 완료본).
        parcel_path: 연속지적도 필지 레이어 (없으면 REQ08 건너뜀).
        building_register_path: 건축물대장 JSON (없으면 STEP13/REQ02/REQ03 제한).
        config_path / requirements_path: 설정 파일 경로.
        t1_date / t2_date: 촬영일(ISO).
        out_gdb: 결과 FileGDB 경로. None이면 config.yaml의 arcgis_pro.output_gdb.
        additional_epochs: [{"label","t1_bands","t2_bands","t1","t2"}] 형태로
            추가 시기를 주면 progress_monitor(REQ04/09)가 다시기 분석을 수행한다.

    Returns:
        각 REQ별 실행 결과 요약 dict (lh_traceability.md 생성에도 쓰인다).
    """
    setup_logging()
    cfg = load_config(config_path)
    requirements = load_requirements(requirements_path)

    aoi_path = resolve_path(aoi_path)
    buildings_path = resolve_path(buildings_path)
    parcel_path = resolve_path(parcel_path) if parcel_path else None
    building_register_path = resolve_path(building_register_path) if building_register_path else None

    out_gdb = resolve_path(out_gdb or cfg.get("arcgis_pro", {}).get("output_gdb", "outputs/arcgis/changneung.gdb"))
    gdb = ensure_gdb(out_gdb)
    reports_dir = Path(resolve_path("outputs/reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)

    t1_d, t2_d = _parse_date(t1_date), _parse_date(t2_date)
    status: dict[str, dict] = {}

    with arcpy_session(gdb, extensions=("Spatial", "3D"), extent=aoi_path):
        # --- STEP 9-11: raster 전처리 + 정합 ---
        t1_stack = raster_ops.build_stacked_scene(
            t1_bands, aoi_path, str(Path(out_gdb).parent / "t1_stack.tif"), cell_size=10.0
        )
        t2_stack = raster_ops.build_stacked_scene(
            t2_bands, aoi_path, str(Path(out_gdb).parent / "t2_stack.tif"), cell_size=10.0, snap_raster=t1_stack
        )
        a1, _ = raster_ops.read_stack(t1_stack)
        a2, _ = raster_ops.read_stack(t2_stack)
        align = raster_ops.verify_alignment(
            raster_ops.to_grayscale(a1), raster_ops.to_grayscale(a2), pixel_size_m=10.0
        )
        logger.info("[PIPELINE] STEP8 정합: %s", align)

        # --- STEP 9-12: 변화탐지 -> 후처리 -> polygon화 -> 밝기방향 [REQ01/REQ07] ---
        cd_cfg = cfg.get("change_detection", {})
        pp_cfg = cfg.get("postprocess", {})
        prob_path = str(Path(out_gdb).parent / "rasters" / "change_probability.tif")
        mask_path = str(Path(out_gdb).parent / "rasters" / "change_mask.tif")
        _, _, used_threshold, cd_stats = change_detect.run_change_detection(
            t1_stack, t2_stack, prob_path, mask_path,
            threshold_method=cd_cfg.get("threshold_method", "fixed"),
            mask_threshold=cd_cfg.get("mask_threshold", 0.5),
        )
        cleaned_path = str(Path(out_gdb).parent / "rasters" / "change_mask_clean.tif")
        change_detect.clean_mask(
            mask_path, cleaned_path,
            opening_cells=pp_cfg.get("morphology", {}).get("opening_kernel", 3) // 3 or 1,
            closing_cells=pp_cfg.get("morphology", {}).get("closing_kernel", 3) // 3 or 1,
            min_component_area_m2=pp_cfg.get("min_component_area_m2", 25),
        )
        change_fc = fc_path(gdb, "change_polygons")
        change_detect.polygonize(cleaned_path, prob_path, change_fc, t1_date, t2_date)
        change_detect.add_brightness_delta(change_fc, t1_stack, t2_stack, str(gdb))
        n_change = int(arcpy.management.GetCount(change_fc)[0])
        status["REQ01"] = {"status": "ok", "change_polygons": n_change, **cd_stats}

        # --- 건물 overlay + 대장검증 + 분류 + 점수화 ---
        overlaid_fc = fc_path(gdb, "buildings_overlaid")
        buildings.overlay_buildings_with_changes(
            buildings_path, change_fc, overlaid_fc,
            buffer_m=cfg["classification"]["buffer_distances_m"][0],
        )

        register_stats = None
        if building_register_path and Path(building_register_path).exists():
            register_stats = buildings.join_building_register(overlaid_fc, building_register_path)
            buildings.compute_administrative_uncertainty(overlaid_fc, t1_d, t2_d)
        else:
            logger.warning("[PIPELINE] 건축물대장 미제공 - STEP13/REQ02/REQ03 정확도가 낮아집니다")

        buildings.classify_building_changes(
            overlaid_fc,
            new_building_ratio_min=cfg["classification"]["change_ratio_new_building_min"],
            t1_date=t1_d, t2_date=t2_d,
        )
        unmatched_fc = fc_path(gdb, "changes_unmatched")
        buildings.classify_unmatched_changes(change_fc, buildings_path, unmatched_fc)

        results_fc = fc_path(gdb, "building_change_results")
        buildings.merge_results(overlaid_fc, unmatched_fc, results_fc)
        buildings.compute_priority_score(
            results_fc, weights=cfg["priority_scoring"]["weights"],
            high_threshold=cfg["priority_scoring"]["thresholds"]["high"],
            medium_threshold=cfg["priority_scoring"]["thresholds"]["medium"],
        )

        # --- 공간통계 (arcpy.stats 네이티브) ---
        sp_cfg = cfg.get("spatial_statistics", {})
        n_results = int(arcpy.management.GetCount(results_fc)[0])
        k = min(sp_cfg.get("k", 8), max(n_results - 1, 1))
        moran = spatial_stats.compute_global_moran(results_fc, "priority_score", k=k)
        spatial_stats.compute_hotspots(results_fc, "priority_score", k=k)
        status["spatial_statistics"] = moran

        # --- REQ03 보상기준일 ---
        baseline_date = _parse_date(cfg.get("compensation", {}).get("baseline_date"))
        if baseline_date:
            comp_counts = compensation.apply_to_featureclass(results_fc, baseline_date, t1_d, t2_d)
            status["REQ03"] = {"status": "ok", "baseline_date": baseline_date.isoformat(), **comp_counts}
        else:
            status["REQ03"] = {
                "status": "skipped",
                "reason": "config.yaml의 compensation.baseline_date가 설정되지 않음 "
                          "(사업부서가 확정한 보상 기준일 필요)",
            }

        # --- REQ02 불법·무허가 의심 스크리닝 ---
        ill_cfg = cfg.get("illegal_screening", {})
        ill_counts = illegal_screen.apply_to_featureclass(
            results_fc,
            tolerance_ratio=ill_cfg.get("tolerance_ratio", 1.3),
            strong_change_ratio=ill_cfg.get("strong_change_ratio", 0.5),
            strong_confidence=ill_cfg.get("strong_confidence", 0.6),
        )
        status["REQ02"] = {"status": "ok", **ill_counts}

        # --- REQ08 지적 연계 ---
        if parcel_path and arcpy.Exists(parcel_path):
            parcel_fc = fc_path(gdb, "parcel_change_summary")
            cadastre_link.link_results_to_parcels(results_fc, parcel_path, parcel_fc)
            filled = cadastre_link.attach_parcel_id_to_results(results_fc, parcel_path)
            status["REQ08"] = {"status": "ok", "linked_candidates": filled}
        else:
            status["REQ08"] = {"status": "skipped", "reason": "parcel_path(연속지적도)가 제공되지 않음"}

        # --- REQ05 현장조사 우선순위 + 야장 ---
        sites = field_survey.collect_sites(results_fc)
        ordered = field_survey.order_route(sites)
        sites_fc = fc_path(gdb, "survey_sites")
        field_survey.write_sites_featureclass(ordered, sites_fc, arcpy.Describe(results_fc).spatialReference)
        work_order_csv = reports_dir / "field_survey_workorder.csv"
        field_survey.export_work_order(ordered, work_order_csv, arcpy.Describe(results_fc).spatialReference)
        status["REQ05"] = {
            "status": "ok", "candidate_count": n_results, "site_count": len(sites),
            "reduction_pct": round(100 * (1 - len(sites) / n_results), 1) if n_results else 0,
        }

        # --- REQ04/REQ09 개발 진행 모니터링 (다시기 입력이 있을 때만) ---
        pm_cfg = cfg.get("progress_monitor", {})
        if additional_epochs:
            grid_fc = fc_path(gdb, "monitoring_grid")
            progress_monitor.build_monitoring_grid(
                aoi_path, grid_fc, cell_size_m=pm_cfg.get("grid_cell_size_m", 250.0)
            )
            epochs = [{"label": "current", "results_fc": results_fc, "t1": t1_d, "t2": t2_d}] + additional_epochs
            progress_fc = fc_path(gdb, "progress_by_block")
            progress_monitor.summarize_progress(grid_fc, epochs, progress_fc)
            status["REQ04"] = {"status": "ok", "epochs": len(epochs)}
            status["REQ09"] = {"status": "ok", "note": "다시기 결과로 과거 시점 진행상황 소급 복원됨"}
        else:
            status["REQ04"] = {"status": "partial", "reason": "단일 시기만 제공됨 - 다시기 비교 없이는 '진행 추세'를 알 수 없음"}
            status["REQ09"] = {"status": "partial", "reason": "과거 시점 아카이브 가용성만 확인 (아래 REQ06 참고), 실제 소급분석은 미실행"}

        # --- REQ11 3D 정밀검토 대상 선정 ---
        r3_cfg = cfg.get("reality_3d", {})
        review_fc = fc_path(gdb, "reality_review_aoi")
        reality_3d.select_reality_review_candidates(
            results_fc, review_fc, buffer_m=r3_cfg.get("review_buffer_m", 20.0)
        )
        blocks_fc = fc_path(gdb, "interim_3d_blocks")
        reality_3d.build_interim_3d_blocks(
            overlaid_fc, blocks_fc,
            floor_height_m=r3_cfg.get("floor_height_m", 3.0),
        )
        reality_3d.build_capture_plan(
            reports_dir / "reality_capture_plan.md", review_fc,
            target_gsd_cm=r3_cfg.get("target_gsd_cm", 3.0),
            overlap_pct=r3_cfg.get("overlap_pct", 75),
        )
        status["REQ11"] = {"status": "partial", "reason": "정밀검토 대상 선정·임시 3D·촬영계획까지 완료. 실제 Reality 처리는 고해상 촬영 확보 후 별도 실행"}

    # --- REQ06 위성영상 확보 (arcpy 세션 밖 - 네트워크 I/O) ---
    it_cfg = cfg.get("imagery_tasking", {})
    sat_cfg = cfg.get("satellite", {})
    bbox = sat_cfg.get("search_bbox_wgs84")
    try:
        avail = imagery_tasking.availability_report(
            bbox, years=list(range(t1_d.year, date.today().year + 1)),
            max_cloud_pct=sat_cfg.get("max_cloud_cover_pct", 20),
            season=tuple(it_cfg.get("season_months", [4, 6])),
        )
        latest = imagery_tasking.latest_available(bbox, within_days=it_cfg.get("recent_days_window", 90))
        imagery_tasking.build_tasking_request(
            reports_dir / "tasking_request.md", "고양 창릉지구",
            _aoi_area_km2(aoi_path),
            {"mean_change_area_m2": _mean_area(change_fc), "polygon_count": n_change, "gsd_m": 10.0},
            target_dates=[f"{date.today().year}-Q4", f"{date.today().year + 1}-Q2"],
            baseline_date=cfg.get("compensation", {}).get("baseline_date"),
        )
        status["REQ06"] = {
            "status": "ok" if latest else "partial",
            "latest_available": latest["datetime"][:10] if latest else None,
            "latest_age_days": latest.get("age_days") if latest else None,
            "note": "아카이브 조회는 완료. 신규촬영(tasking)은 상용 계약 필요 - tasking_request.md 참고",
        }
    except Exception as e:
        logger.warning("[PIPELINE] REQ06 아카이브 조회 실패 (네트워크?): %s", e)
        status["REQ06"] = {"status": "failed", "reason": str(e)}

    # --- REQ10 보고서 / Web Map (자격증명·템플릿 없으면 건너뛰되 사유 기록) ---
    status["REQ10"] = _try_publish(cfg, gdb, results_fc, change_fc, aoi_path, sites_fc, reports_dir, status)

    status["REQ07"] = {"status": "ok", "note": "본 실행 자체가 T1/T2 자동 비교 파이프라인이다"}

    write_traceability_report(requirements, status, reports_dir / "lh_traceability.md")
    return status


def _aoi_area_km2(aoi_path: str) -> float:
    return sum(r[0] for r in arcpy.da.SearchCursor(aoi_path, ["SHAPE@AREA"])) / 1e6


def _mean_area(change_fc: str) -> float | None:
    areas = [r[0] for r in arcpy.da.SearchCursor(change_fc, ["change_area_m2"]) if r[0] is not None]
    return round(sum(areas) / len(areas), 1) if areas else None


def _try_publish(cfg, gdb, results_fc, change_fc, aoi_path, sites_fc, reports_dir, status) -> dict:
    """REQ10 - 가능하면 PDF/Web Map까지 발행하고, 안 되면 사유를 남긴다."""
    from . import report_builder, webmap_publish

    result = {}
    ap_cfg = cfg.get("arcgis_pro", {})
    template = resolve_path(ap_cfg.get("template_aprx", "outputs/arcgis/templates/blank_template.aprx"))
    try:
        aprx_out = str(Path(gdb).parent / "changneung_poc.aprx")
        report_builder.build_map_document(template, aprx_out, aoi_path, change_fc, results_fc, sites_fc)
        report_builder.export_field_report_pdf(
            aprx_out, reports_dir / "changneung_field_report.pdf",
            {"변화후보": status.get("REQ01", {}).get("change_polygons"),
             "현장수": status.get("REQ05", {}).get("site_count")},
        )
        result["pdf_report"] = "ok"
    except report_builder.MissingProjectError as e:
        result["pdf_report"] = "skipped"
        result["pdf_reason"] = str(e)
    except Exception as e:
        result["pdf_report"] = "failed"
        result["pdf_reason"] = str(e)

    try:
        exports = {
            "aoi": Path(gdb).parent / "aoi_wgs84.geojson",
            "change_polygons": Path(gdb).parent / "change_polygons_wgs84.geojson",
            "building_change_results": Path(gdb).parent / "results_wgs84.geojson",
            "survey_sites": Path(gdb).parent / "sites_wgs84.geojson",
        }
        webmap_publish.export_wgs84_geojson(aoi_path, exports["aoi"])
        webmap_publish.export_wgs84_geojson(change_fc, exports["change_polygons"])
        webmap_publish.export_wgs84_geojson(results_fc, exports["building_change_results"])
        webmap_publish.export_wgs84_geojson(sites_fc, exports["survey_sites"])
        items = webmap_publish.publish_all({k: str(v) for k, v in exports.items()})
        result["web_map"] = "ok"
        result["web_map_url"] = items.get("web_map").homepage if items.get("web_map") else None
    except Exception as e:
        result["web_map"] = "skipped"
        result["web_map_reason"] = f"{type(e).__name__}: {e}"

    result["status"] = "ok" if result.get("pdf_report") == "ok" or result.get("web_map") == "ok" else "partial"
    return result


def write_traceability_report(requirements: dict, status: dict, out_path: Path) -> Path:
    """REQ07 - LH 요구사항 매트릭스 대비 실행 결과를 markdown으로 기록한다.

    "반영했다"는 주장이 아니라, 각 요구사항이 이번 실행에서 실제로 무엇을
    만들었는지(또는 왜 못 만들었는지)를 산출물과 함께 남긴다.
    """
    lines = [
        "# LH 요구사항 반영 현황 (자동 생성)",
        "",
        f"- 생성 시각: {datetime.now().isoformat(timespec='seconds')}",
        "- 이 문서는 매 실행마다 다시 생성된다 - 과거 실행 기록이 아니라 "
        "**이번 실행의 실제 결과**다.",
        "",
        "| 순위 | 요구 기능 | 상태 | 비고 |",
        "|---|---|---|---|",
    ]
    for req in requirements.get("requirements", []):
        rid = req["id"]
        run_status = status.get(rid, {})
        state = run_status.get("status", req.get("status", "unknown"))
        icon = {"ok": "완료", "partial": "부분", "skipped": "건너뜀", "failed": "실패"}.get(state, state)
        note = run_status.get("reason") or run_status.get("note") or req.get("note", "")
        lines.append(f"| {req['rank']} | {req['title']} | {icon} | {note} |")

    lines += [
        "",
        "## 상세 실행 결과",
        "",
        "```",
    ]
    for key, val in status.items():
        lines.append(f"{key}: {val}")
    lines += ["```", ""]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[PIPELINE] LH 추적 리포트 생성: %s", out_path)
    return out_path
