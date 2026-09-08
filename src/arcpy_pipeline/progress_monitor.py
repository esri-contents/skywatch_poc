"""REQ04 - 사업지구 개발 진행 모니터링 / REQ09 - 과거 시점 기록 보완.

LH 애로사항 1번("넓은 사업지역에서 계속 발생하는 변화를 추적하기 어려움")에
정면으로 대응하는 모듈이다. 두 시점 비교(T1 vs T2)는 "무엇이 바뀌었나"만
알려주지, "이 지구가 지금 어느 단계까지 왔나"는 알려주지 않는다. 여러
시기의 변화탐지 결과를 격자/블록 단위로 누적하면 그 질문에 답할 수 있다.

핵심 개념 두 가지:

**1. 지속성(persistence)** - 같은 위치에서 연속된 여러 구간에 걸쳐 변화가
관측되면 실제 공사가 진행 중이라는 강한 증거다. 반대로 한 구간에서만
튀고 마는 변화는 계절·광량·구름 그림자 같은 잡음일 가능성이 높다.
2시점 비교만으로는 이 둘을 구분할 수 없다 - 다시기 분석의 고유 가치다.

**2. 개발 단계 추정** - 변화 면적 비율, 신축 건수, 변화가 아직 진행 중인지
멈췄는지를 조합해 블록별로 미착수/조성중/건축중/마무리 단계를 추정한다.
정밀한 공정률이 아니라 "어디를 먼저 봐야 하는가"를 좁히는 용도다.

**REQ09(과거 기록 보완)**: Sentinel-2 아카이브는 2015년부터 5일 주기로
존재한다. 드론 촬영이나 현장조사 기록이 없는 과거 시점이라도 위성
아카이브로 소급 복원할 수 있다는 것이 이 방식의 고유한 강점이며,
`historical_baseline_table()`이 그 가용성을 표로 만든다.
"""

from __future__ import annotations

import logging
from datetime import date

import arcpy

from .env import add_field_if_missing, delete_if_exists

logger = logging.getLogger("arcpy_pipeline.progress_monitor")

STAGE_NOT_STARTED = "미착수"
STAGE_SITE_WORK = "부지조성"
STAGE_CONSTRUCTION = "건축진행"
STAGE_NEARING = "마무리단계"
STAGE_STABLE = "변화없음/완료"

PROGRESS_FIELDS = [
    ("block_id", "TEXT", 24),
    ("epochs_observed", "SHORT", None),
    ("epochs_with_change", "SHORT", None),
    ("persistence_ratio", "DOUBLE", None),
    ("total_change_area_m2", "DOUBLE", None),
    ("change_area_ratio", "DOUBLE", None),
    ("new_building_count", "SHORT", None),
    ("demolition_count", "SHORT", None),
    ("latest_epoch_active", "SHORT", None),
    ("development_stage", "TEXT", 24),
    ("stage_evidence", "TEXT", 400),
]


def build_monitoring_grid(
    aoi_fc: str,
    out_fc: str,
    cell_size_m: float = 250.0,
    shape: str = "SQUARE",
) -> str:
    """AOI를 균일 격자로 나눠 모니터링 블록을 만든다.

    실제 사업 블록(공구/획지) 경계가 있으면 그것을 쓰는 게 낫다
    (`summarize_progress`의 block_fc 인자에 직접 넘기면 된다). 없을 때
    쓰는 기본값으로, 250m 격자는 AOI 10.99km² 기준 약 176개 블록이 되어
    사람이 훑어볼 만한 개수다.

    Returns:
        block_id 필드를 가진 격자 Feature Class.
    """
    delete_if_exists(out_fc)
    arcpy.management.GenerateTessellation(
        Output_Feature_Class=out_fc,
        Extent=arcpy.Describe(aoi_fc).extent,
        Shape_Type=shape,
        Size=f"{cell_size_m * cell_size_m} SquareMeters",
        Spatial_Reference=arcpy.Describe(aoi_fc).spatialReference,
    )
    # AOI 밖 격자는 버린다 (경계 격자는 AOI와 교차하면 남긴다)
    lyr = "grid_lyr"
    arcpy.management.MakeFeatureLayer(out_fc, lyr)
    try:
        arcpy.management.SelectLayerByLocation(lyr, "INTERSECT", aoi_fc, invert_spatial_relationship="INVERT")
        if int(arcpy.management.GetCount(lyr)[0]) > 0:
            arcpy.management.DeleteFeatures(lyr)
    finally:
        arcpy.management.Delete(lyr)

    add_field_if_missing(out_fc, "block_id", "TEXT", field_length=24)
    with arcpy.da.UpdateCursor(out_fc, ["OID@", "block_id"]) as cur:
        for row in cur:
            row[1] = f"BLK_{row[0]:04d}"
            cur.updateRow(row)

    n = int(arcpy.management.GetCount(out_fc)[0])
    logger.info("[PROGRESS] 모니터링 격자 %d개 생성 (%.0fm)", n, cell_size_m)
    return out_fc


def classify_development_stage(
    change_area_ratio: float,
    new_building_count: int,
    demolition_count: int,
    persistence_ratio: float,
    latest_epoch_active: bool,
    site_work_ratio: float = 0.10,
    active_ratio: float = 0.03,
) -> tuple[str, str]:
    """블록 하나의 개발 단계를 추정한다 (순수 함수).

    규칙은 단순하고 설명 가능해야 한다 - 현장 담당자가 왜 이 단계로
    분류됐는지 즉시 납득할 수 있어야 하기 때문이다.

    Args:
        change_area_ratio: 블록 면적 대비 누적 변화 면적 비율.
        new_building_count: 블록 내 NEW_BUILDING 후보 수.
        demolition_count: DEMOLITION/철거 추정 수.
        persistence_ratio: 변화가 관측된 구간 수 / 전체 구간 수.
        latest_epoch_active: 가장 최근 구간에서도 변화가 있었는지.
        site_work_ratio: 이 이상이면 "면적 위주 대규모 조성" 규모로 본다.
        active_ratio: 이 미만이면 사실상 변화 없음으로 본다.

    Returns:
        (단계, 판정 근거 문자열)
    """
    parts = [
        f"변화면적비율={change_area_ratio:.1%}",
        f"신축후보={new_building_count}",
        f"철거후보={demolition_count}",
        f"지속성={persistence_ratio:.2f}",
        f"최근구간활성={'예' if latest_epoch_active else '아니오'}",
    ]
    evidence = ", ".join(parts)

    if change_area_ratio < active_ratio:
        return STAGE_NOT_STARTED if new_building_count == 0 else STAGE_STABLE, evidence

    if not latest_epoch_active:
        # 과거에는 변화가 있었으나 최근 구간에서 멈춤 -> 공사 완료 또는 중단
        return STAGE_STABLE, evidence + " | 최근 구간에 변화가 멈춤 - 완료 또는 중단"

    if change_area_ratio >= site_work_ratio and new_building_count == 0:
        return STAGE_SITE_WORK, evidence + " | 넓은 면적 변화에 비해 신축 후보 없음 - 정지/조성 단계 추정"

    if new_building_count > 0 and persistence_ratio >= 0.5:
        return STAGE_CONSTRUCTION, evidence + " | 신축 후보가 여러 구간에 지속 관측 - 건축 진행 추정"

    if new_building_count > 0:
        return STAGE_NEARING, evidence + " | 신축 후보는 있으나 지속성이 낮음 - 마무리 또는 단발 변화"

    return STAGE_SITE_WORK, evidence


def summarize_progress(
    block_fc: str,
    epochs: list[dict],
    out_fc: str,
) -> str:
    """여러 시기 결과를 블록 단위로 누적해 개발 진행 상황을 산출한다.

    Args:
        block_fc: 모니터링 블록 (block_id 필드 필요).
        epochs: [{"label": "2022-2024", "results_fc": <경로>,
                  "t1": date, "t2": date}, ...] - 시간순 정렬 권장.
        out_fc: 결과 Feature Class.

    Returns:
        PROGRESS_FIELDS가 채워진 out_fc.
    """
    if not epochs:
        raise ValueError("[PROGRESS] epochs가 비어 있습니다")

    delete_if_exists(out_fc)
    arcpy.management.CopyFeatures(block_fc, out_fc)
    for name, ftype, length in PROGRESS_FIELDS:
        add_field_if_missing(out_fc, name, ftype, field_length=length)

    block_area = {
        bid: area for bid, area in arcpy.da.SearchCursor(out_fc, ["block_id", "SHAPE@AREA"])
    }
    ordered = sorted(epochs, key=lambda e: (e.get("t2") or date.min))
    latest_label = ordered[-1]["label"]

    agg = {
        bid: {
            "area": 0.0, "new": 0, "demo": 0,
            "epochs": set(), "latest_active": False,
        }
        for bid in block_area
    }

    for ep in ordered:
        hits = _blocks_touched(out_fc, ep["results_fc"])
        for bid, info in hits.items():
            if bid not in agg:
                continue
            a = agg[bid]
            a["area"] += info["change_area"]
            a["new"] += info["new_building"]
            a["demo"] += info["demolition"]
            if info["change_area"] > 0 or info["feature_count"] > 0:
                a["epochs"].add(ep["label"])
                if ep["label"] == latest_label:
                    a["latest_active"] = True

    n_epochs = len(ordered)
    stage_counts: dict[str, int] = {}
    with arcpy.da.UpdateCursor(
        out_fc, ["block_id"] + [n for n, _, _ in PROGRESS_FIELDS][1:]
    ) as cur:
        for row in cur:
            bid = row[0]
            a = agg.get(bid, {"area": 0.0, "new": 0, "demo": 0, "epochs": set(), "latest_active": False})
            area = block_area.get(bid, 0.0)
            ratio = (a["area"] / area) if area > 0 else 0.0
            persistence = len(a["epochs"]) / n_epochs if n_epochs else 0.0
            stage, evidence = classify_development_stage(
                ratio, a["new"], a["demo"], persistence, a["latest_active"]
            )
            row[1] = n_epochs
            row[2] = len(a["epochs"])
            row[3] = round(persistence, 3)
            row[4] = round(a["area"], 2)
            row[5] = round(ratio, 5)
            row[6] = a["new"]
            row[7] = a["demo"]
            row[8] = 1 if a["latest_active"] else 0
            row[9] = stage
            row[10] = evidence[:400]
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            cur.updateRow(row)

    logger.info("[PROGRESS] 블록 %d개 개발단계 분포: %s", len(block_area), stage_counts)
    return out_fc


def _blocks_touched(block_fc: str, results_fc: str) -> dict[str, dict]:
    """결과 레이어를 블록과 교차시켜 블록별 변화 면적/유형 집계를 만든다."""
    import os

    inter = os.path.join(arcpy.env.scratchGDB, "_blk_res_intersect")
    delete_if_exists(inter)
    arcpy.analysis.Intersect([block_fc, results_fc], inter, join_attributes="ALL")

    out: dict[str, dict] = {}
    names = {f.name for f in arcpy.ListFields(inter)}
    ctype_field = "change_type" if "change_type" in names else None
    fields = ["block_id", "SHAPE@AREA"] + ([ctype_field] if ctype_field else [])
    for row in arcpy.da.SearchCursor(inter, fields):
        bid = row[0]
        rec = out.setdefault(bid, {"change_area": 0.0, "new_building": 0,
                                   "demolition": 0, "feature_count": 0})
        rec["change_area"] += row[1] or 0.0
        rec["feature_count"] += 1
        if ctype_field:
            if row[2] == "NEW_BUILDING":
                rec["new_building"] += 1
            elif row[2] == "DEMOLITION":
                rec["demolition"] += 1
    delete_if_exists(inter)
    return out


def compute_site_persistence(epochs: list[dict], distance_m: float = 30.0) -> list[dict]:
    """시기 간 동일 위치 변화(지속 관측)를 찾아 신뢰도를 매긴다.

    한 구간에서만 나타난 변화는 계절/광량 잡음일 수 있고, 연속 구간에
    반복 관측되면 실제 공사일 가능성이 높다. 위치 일치는 중심점 간
    거리로 판단한다(폴리곤 경계는 시기마다 조금씩 달라지므로).

    Args:
        epochs: [{"label", "results_fc", "t1", "t2"}, ...] 시간순.
        distance_m: 이 거리 안이면 같은 현장으로 본다.

    Returns:
        [{"epoch", "site_id", "x", "y", "persisted_in", "persistence_count"}, ...]
    """
    if len(epochs) < 2:
        logger.warning("[PROGRESS] 지속성 분석에는 2개 이상 시기가 필요합니다")
        return []

    ordered = sorted(epochs, key=lambda e: (e.get("t2") or date.min))
    per_epoch: dict[str, list[tuple[str, float, float]]] = {}
    for ep in ordered:
        pts = []
        names = {f.name for f in arcpy.ListFields(ep["results_fc"])}
        sid = "site_id" if "site_id" in names else "OID@"
        for row in arcpy.da.SearchCursor(ep["results_fc"], [sid, "SHAPE@XY"]):
            if row[1]:
                pts.append((str(row[0]), row[1][0], row[1][1]))
        per_epoch[ep["label"]] = pts

    results = []
    labels = [e["label"] for e in ordered]
    for i, label in enumerate(labels):
        others = [l for j, l in enumerate(labels) if j != i]
        for sid, x, y in per_epoch[label]:
            persisted = [
                other for other in others
                if any((x - ox) ** 2 + (y - oy) ** 2 <= distance_m ** 2
                       for _, ox, oy in per_epoch[other])
            ]
            results.append({
                "epoch": label,
                "site_id": sid,
                "x": round(x, 2),
                "y": round(y, 2),
                "persisted_in": ";".join(persisted),
                "persistence_count": len(persisted) + 1,
            })

    multi = sum(1 for r in results if r["persistence_count"] > 1)
    logger.info(
        "[PROGRESS] 지속성 분석: 전체 %d건 중 %d건이 2개 이상 시기에 반복 관측",
        len(results), multi,
    )
    return results


def historical_baseline_table(
    aoi_bbox_wgs84: list[float],
    years: list[int],
    max_cloud_pct: float = 20.0,
) -> list[dict]:
    """REQ09 - 과거 시점별 위성영상 가용성 표를 만든다.

    드론·현장조사 기록이 없는 과거 시점도 위성 아카이브로 소급 복원할 수
    있음을 보이는 것이 목적이다. 실제 조회는 imagery_tasking 모듈이
    수행하고, 여기서는 연도별로 정리한다.

    Returns:
        [{"year", "scene_count", "best_date", "best_cloud_pct", "recoverable"}, ...]
    """
    from .imagery_tasking import search_archive

    rows = []
    for year in years:
        try:
            scenes = search_archive(
                bbox=aoi_bbox_wgs84,
                date_range=f"{year}-01-01/{year}-12-31",
                max_cloud_pct=max_cloud_pct,
            )
        except Exception as e:  # 네트워크 없는 환경에서도 표는 만들어져야 한다
            logger.warning("[PROGRESS] %d년 아카이브 조회 실패: %s", year, e)
            rows.append({"year": year, "scene_count": None, "best_date": None,
                         "best_cloud_pct": None, "recoverable": "조회실패"})
            continue
        best = scenes[0] if scenes else None
        rows.append({
            "year": year,
            "scene_count": len(scenes),
            "best_date": best["datetime"][:10] if best else None,
            "best_cloud_pct": round(best["cloud_cover"], 2) if best else None,
            "recoverable": "가능" if best else "불가",
        })
    return rows
