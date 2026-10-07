"""sentinel2_baseline_report.md 생성 (다중시점 Screening 결과 -> 사람이 읽는 보고서).

보고서는 파이프라인 산출물(scene_selection, pair summary, hotspot, ROI, 촬영연도 제안)을 그대로
옮겨 적는다 - 수치/날짜를 새로 만들지 않고, 비용/가격/법적 기준일은 다루지 않는다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd

REQUIRED_LIMITATIONS = [
    "Sentinel-2 공간해상도는 10m이다 (1 pixel = 100 m²).",
    "개별 지장물/소형 건물 판독용이 아니다.",
    "모든 변화 후보는 고해상도 영상(SkyWatch 50cm 등)으로 검증해야 한다.",
    "change candidate ≠ 보상 대상 (지장물 존재/보상 여부 판정 아님).",
    "change candidate ≠ 불법/무허가 구조물.",
    "계절·식생(경운/작황), 그림자, 영상 정합오차, 센서/궤도 차이가 오탐을 유발할 수 있다.",
    "'변화 없음'이 아니라 'Sentinel-2에서는 유의미한 변화가 검출되지 않음'으로 해석한다.",
]


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_(없음)_\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r.tolist()) + " |")
    return "\n".join(lines) + "\n"


def write_baseline_report(
    cfg: dict, pairs: list[dict], results: dict, records: dict[int, dict], figures: dict[str, Path], phase: str
) -> Path:
    out_root = Path(cfg["paths"]["output_root"])
    rep_dir = out_root / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    crs = cfg["project"]["analysis_crs"]
    aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(crs)
    aoi_km2 = aoi.geometry.union_all().area / 1e6
    hs, roi, summary = results["hotspots"], results["roi"], results["pair_summary"]
    acq = results["acquisition"]
    used_years = sorted({y for p in pairs for y in (p["year_from"], p["year_to"])})
    ctx = cfg["project"].get("context", {})

    L: list[str] = []
    L.append(f"# Sentinel-2 Baseline Screening Report - {cfg['project']['short_label']}\n")
    L.append(f"_생성일 {date.today().isoformat()} · Phase {phase.upper()} · "
             f"Sentinel-2 change candidate screening (Decision Support, 지장물/보상 판정 자료 아님)_\n")

    L.append("## Project\n")
    L.append(f"- 사업: 한국철도공단 토지보상 Demo / {ctx.get('railway_project', '')}")
    L.append(f"- 대상 지역: {cfg['project']['location']}")
    if ctx:
        L.append(f"- 착공(context): {', '.join(f'{k} {v}' for k, v in ctx.get('construction_start', {}).items())}")
        L.append(f"- 복선 개통(context): {', '.join(f'{k} {v}' for k, v in ctx.get('double_track_opening', {}).items())}")
        L.append("- 위 날짜는 사업 개요 context이며 보상 기준일/법적 기준일/지장물 판정 기준일이 아니다.")
    L.append("")

    L.append("## Objective\n")
    L.append("SkyWatch 50cm 아카이브 영상을 구매하기 전에 Sentinel-2 무료 영상으로 변화 Screening을 먼저 수행해 "
             "(1) 의미 있는 변화가 있었던 구역(hotspot), (2) 고해상도 영상을 확인할 ROI, (3) 구매를 검토할 촬영연도를 "
             "좁힌다.\n")
    L.append("- Sentinel-2 : **어디가 바뀌었는가?**\n- SkyWatch 50cm(향후) : **무엇이 생겼고 없어졌고 형태가 어떻게 변했는가?**\n")

    L.append("## AOI\n")
    L.append(f"- 상태: **{cfg['aoi']['status']} (잠정)**")
    L.append(f"- geometry source: {cfg['aoi']['source']}")
    L.append(f"- 면적: **{aoi_km2:.2f} km²** · CRS: {crs} (원본 VWorld EPSG:4326)")
    L.append(f"- 선정 근거: {' '.join(str(cfg['aoi']['note']).split())}")
    L.append(f"- 철도 geometry: {cfg['aoi'].get('railway_source')}")
    L.append("")

    L.append("## Imagery\n")
    L.append(f"- provider: {records[used_years[0]]['provider']} · bands {cfg['imagery']['band_order']} · 10m · "
             f"season window {cfg['imagery']['season']['start']}~{cfg['imagery']['season']['end']} (동일 계절 비교)")
    L.append("- L2A 반사율 DN offset(+1000, processing baseline ≥ 04.00)은 제거해 연도 간 값을 맞췄고, "
             "SCL 구름/그림자/눈 픽셀은 nodata 처리했다.\n")
    rows = []
    for y in used_years:
        r = records[y]
        rows.append({
            "year": y, "acquisition_date": r["date"], "scene_cloud_%": r.get("scene_cloud_cover_pct"),
            "AOI_clear_%": None if r.get("aoi_clear_fraction") is None else round(100 * r["aoi_clear_fraction"], 1),
            "scene_id": r.get("scene_id"), "baseline": r.get("processing_baseline"),
        })
    L.append(_md_table(pd.DataFrame(rows)))
    L.append("**날짜 선정 근거**: " + (records[used_years[0]].get("selection_rule") or "") +
             f" (구름 기준: scene cloud < {cfg['imagery']['max_scene_cloud_pct']}%, "
             f"AOI clear ≥ {cfg['imagery']['min_aoi_clear_fraction']:.0%}). "
             "연도별 모든 후보와 지표는 `reports/scene_selection.json`의 `considered`에 기록되어 있다. "
             "AOI가 두 타일(T52SDG/T52SDF) 경계에 걸쳐 있어 AOI를 완전히 덮는 단일 장면만 후보로 인정했다.\n")

    L.append("## Method\n")
    L.append("1. Imagery Provider(Sentinel-2) → AOI window 다운로드(cache) → 반사율 offset 보정/SCL 마스크 → 4-band 스택 GeoTIFF")
    L.append("2. 공통 전처리: EPSG:5186 재투영, 10m 공통 grid 정렬, AOI clip (`preprocessing.raster_preprocess.align_imagery_pair`)")
    L.append("3. 변화탐지: robust CVA + SSIM + edge/texture 앙상블, fixed threshold "
             f"{cfg['analysis']['mask_threshold']} (`change_detection.baseline`)")
    L.append(f"4. 후처리: morphology + 최소 면적 {cfg['analysis']['min_component_area_m2']} m², polygonize, ECC 정합 QA "
             "(`imagery_change_pipeline`)")
    h = cfg["hotspot"]
    L.append(f"5. Hotspot: 변화 polygon을 {h['cluster_distance_m']} m 이내로 군집, 최소 {h['min_hotspot_area_m2']} m². "
             "priority_score = 면적 × 점수 × 변화 빈도(step 쌍)")
    L.append(f"6. ROI: 추천 hotspot bounds + {h['buffer_m']} m buffer, 한 변 {h['min_roi_size_m']}~{h.get('max_roi_size_m', '-')} m")
    L.append("7. 촬영연도 제안: 인접 연도쌍(step)별 변화 면적이 최대 쌍의 "
             f"{cfg['acquisition_years']['active_fraction_of_max']:.0%} 이상인 구간을 active로 보고 전/중/후 연도를 제안\n")

    L.append("## Results\n")
    L.append(_md_table(summary))
    if "corridor_change_area_m2" in summary:
        L.append(f"`corridor_change_area_m2`: OSM 철도(비공식) 양측 {cfg['hotspot'].get('railway_corridor_buffer_m')} m 이내 변화 면적.\n")
    warn = [n for n in acq["notes"] if n.startswith("경고")]
    if warn:
        L.append("> **해석 주의**: " + warn[0] + "\n")
    total = int(summary["polygon_count"].sum()) if len(summary) else 0
    if total == 0:
        L.append("> Sentinel-2에서는 유의미한 변화가 검출되지 않았다 (이는 '변화 없음'을 의미하지 않는다).\n")
    for key, cap in [("fig1_aoi_overview", "Master AOI overview"), ("fig2_before_after", "Before/After"),
                     ("fig3_change_intensity", "Change intensity"), ("fig4_change_polygons", "Change polygons")]:
        if key in figures:
            L.append(f"![{cap}](../figures/{figures[key].name})\n")

    L.append("## Hotspots\n")
    if hs.empty:
        L.append("_hotspot 없음._\n")
    else:
        cols = ["hotspot_id", "first_year", "last_year", "change_pairs", "change_area_m2", "change_score",
                "change_frequency", "distance_to_railway_m", "centroid_x", "centroid_y", "recommended_for_highres"]
        L.append(_md_table(hs[cols]))
        L.append("`distance_to_railway_m`는 OpenStreetMap 선형(비공식, railway=rail, 야드 제외) 기준이며 공식 노선 geometry가 아니다. "
                 "`first_year`/`last_year`는 인접 연도쌍 중 변화가 관측된 구간의 종료 연도다.\n")
    if "fig5_hotspots" in figures:
        L.append(f"![Hotspots](../figures/{figures['fig5_hotspots'].name})\n")

    L.append("## High-resolution Recommendation\n")
    if roi.empty:
        L.append("Sentinel-2 기준(면적/점수)을 충족해 SkyWatch 50cm 확인을 추천할 hotspot이 없다. "
                 "이는 변화가 없다는 뜻이 아니므로, 사업 우선순위에 따라 별도 판단이 필요하다.\n")
    else:
        L.append("아래 ROI는 변화 후보가 강한 hotspot을 주변 맥락과 함께 감싼 SkyWatch 50cm 확인 후보 영역이다 "
                 "(크기는 config `hotspot.buffer_m`/`min_roi_size_m`로 조정). 구매 비용/최소 주문 면적은 이 분석에서 "
                 "다루지 않는다.\n")
        L.append(_md_table(roi.drop(columns='geometry')))
    if "fig6_skywatch_roi" in figures:
        L.append(f"![SkyWatch ROI](../figures/{figures['fig6_skywatch_roi'].name})\n")

    L.append("## Recommended acquisition years\n")
    L.append("Sentinel-2 변화량에 근거한 **제안**이며 최종 구매연도 확정이 아니다.\n")
    if acq["years"]:
        L.append(_md_table(pd.DataFrame(acq["years"])))
        L.append(f"active 구간: {', '.join(acq['active_pairs']) or '없음'}\n")
    for n in acq["notes"]:
        L.append(f"- {n}")
    L.append("")

    L.append("## Limitations\n")
    for x in REQUIRED_LIMITATIONS:
        L.append(f"- {x}")
    for x in cfg.get("analysis_limitations", []):
        if x not in REQUIRED_LIMITATIONS:
            L.append(f"- {x}")
    L.append("")

    L.append("## SkyWatch 50cm 전환 시 TODO\n")
    L.append("- `imagery.provider: local_geotiff` + `imagery.local_files`에 구매한 GeoTIFF(실제 촬영일/scene_id)를 지정하면 같은 pipeline으로 입력 가능 (`src/imagery/providers.py`).")
    L.append("- 50cm에서는 resolution_m, min_component_area_m2, morphology kernel, SSIM window, ECC 정합 파라미터를 별도 project config에서 재설정해야 한다 (10m 파라미터 그대로 사용 불가).")
    L.append("- SkyWatch Content Store 주문/다운로드 provider는 구현하지 않았다 (구매 전 단계).\n")

    path = rep_dir / "sentinel2_baseline_report.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path
