"""REQ05 - 현장조사 대상 선별 및 우선순위 지정 + 현장 야장 자동 생성.

LH 설문에서 4.46/5를 받은 항목이고, 애로사항 2번("보고서 작성에 많은
시간이 소요됨")과 3번("현장 상황을 적시에 파악하기 어려움")에 함께 대응한다.

이 모듈의 존재 이유는 **건물 수와 현장 수는 다르다**는 실측 사실이다.
대형 공사장 하나가 건물 footprint 수십 개에 걸치면 "HIGH 32건"처럼 보이지만
실제 방문할 곳은 11곳뿐이었다(육안검수 실측, poc_summary.md 5번). 건물
개수로 현장조사를 지시하면 같은 공사장을 여러 번 방문하게 된다.

그래서 산출물의 기본 단위를 **site(현장)** 으로 바꾼다:

```text
건물 단위 후보 N건
  → site_id로 묶어 현장 단위 M곳 (M << N)
  → 현장별 대표 좌표·우선순위·판단근거 집계
  → 이동 동선 순서 부여 (최근접 이웃 휴리스틱)
  → 현장조사 야장 CSV (지번, 좌표, 확인사항, 판단근거)
```

동선 순서까지 넣는 이유: 우선순위 순서대로만 방문하면 지구 양 끝을
오가게 된다. 등급 안에서 동선을 정렬하면 같은 인력·시간으로 더 많은
현장을 볼 수 있다.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path

import arcpy

from .env import add_field_if_missing, delete_if_exists

logger = logging.getLogger("arcpy_pipeline.field_survey")

PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

SITE_FIELDS = [
    ("site_id", "TEXT", 40),
    ("candidate_count", "SHORT", None),
    ("building_count", "SHORT", None),
    ("site_priority", "TEXT", 10),
    ("max_priority_score", "DOUBLE", None),
    ("total_change_area_m2", "DOUBLE", None),
    ("dominant_change_type", "TEXT", 40),
    ("compensation_status", "TEXT", 32),
    ("illegal_grade", "TEXT", 16),
    ("register_matched_count", "SHORT", None),
    ("directional_flag_count", "SHORT", None),
    ("parcel_pnu_list", "TEXT", 250),
    ("route_order", "SHORT", None),
    ("survey_focus", "TEXT", 400),
]


def collect_sites(results_fc: str) -> list[dict]:
    """변화 후보를 site_id로 묶어 현장 단위 레코드를 만든다.

    site_id가 없는(건물 미교차 등) 후보는 자기 자신을 하나의 현장으로 본다.

    Returns:
        현장 dict 목록 (좌표는 구성 후보들의 면적 가중 중심).
    """
    names = {f.name for f in arcpy.ListFields(results_fc)}
    opt = [f for f in ("site_id", "change_type", "priority_score", "inspection_priority",
                       "change_area_m2", "has_register_match", "directional_consistency_flag",
                       "compensation_status", "illegal_grade", "parcel_pnu",
                       "classification_note", "illegal_evidence", "compensation_evidence")
           if f in names]
    fields = ["OID@", "SHAPE@XY", "SHAPE@AREA"] + opt

    groups: dict[str, dict] = defaultdict(
        lambda: {
            "candidates": 0, "buildings": 0, "area": 0.0,
            "types": defaultdict(float), "max_score": 0.0, "priority": "LOW",
            "register": 0, "dir_flag": 0, "pnus": set(),
            "comp": None, "illegal": None, "wx": 0.0, "wy": 0.0, "w": 0.0,
            "notes": [],
        }
    )

    for row in arcpy.da.SearchCursor(results_fc, fields):
        rec = dict(zip(opt, row[3:]))
        sid = rec.get("site_id") or f"OID_{row[0]}"
        g = groups[sid]
        xy, area = row[1], (row[2] or 0.0)

        g["candidates"] += 1
        g["area"] += rec.get("change_area_m2") or 0.0
        if rec.get("change_type"):
            g["types"][rec["change_type"]] += max(area, 1.0)
        score = rec.get("priority_score") or 0.0
        g["max_score"] = max(g["max_score"], score)
        pr = rec.get("inspection_priority")
        if pr and PRIORITY_RANK.get(pr, 9) < PRIORITY_RANK.get(g["priority"], 9):
            g["priority"] = pr
        if rec.get("has_register_match"):
            g["register"] += 1
        if rec.get("directional_consistency_flag") == 0:
            g["dir_flag"] += 1
        if rec.get("parcel_pnu"):
            g["pnus"].add(str(rec["parcel_pnu"]))
        if rec.get("change_type") in ("NEW_BUILDING", "EXPANSION_OR_RECONSTRUCTION", "DEMOLITION"):
            g["buildings"] += 1
        g["comp"] = _worst(g["comp"], rec.get("compensation_status"), _COMP_ORDER)
        g["illegal"] = _worst(g["illegal"], rec.get("illegal_grade"), _ILLEGAL_ORDER)
        for key in ("classification_note", "compensation_evidence", "illegal_evidence"):
            v = rec.get(key)
            if v and v not in g["notes"]:
                g["notes"].append(v)
        if xy:
            w = max(area, 1.0)
            g["wx"] += xy[0] * w
            g["wy"] += xy[1] * w
            g["w"] += w

    sites = []
    for sid, g in groups.items():
        dominant = max(g["types"].items(), key=lambda kv: kv[1])[0] if g["types"] else None
        sites.append({
            "site_id": sid,
            "candidate_count": g["candidates"],
            "building_count": g["buildings"],
            "site_priority": g["priority"],
            "max_priority_score": round(g["max_score"], 4),
            "total_change_area_m2": round(g["area"], 2),
            "dominant_change_type": dominant,
            "compensation_status": g["comp"],
            "illegal_grade": g["illegal"],
            "register_matched_count": g["register"],
            "directional_flag_count": g["dir_flag"],
            "parcel_pnu_list": ";".join(sorted(g["pnus"]))[:250],
            "x": round(g["wx"] / g["w"], 2) if g["w"] else None,
            "y": round(g["wy"] / g["w"], 2) if g["w"] else None,
            "survey_focus": build_survey_focus(dominant, g),
        })

    total_candidates = sum(s["candidate_count"] for s in sites)
    logger.info(
        "[SURVEY] 후보 %d건 → 현장 %d곳 (%.0f%% 감소)",
        total_candidates, len(sites),
        100 * (1 - len(sites) / total_candidates) if total_candidates else 0,
    )
    return sites


_COMP_ORDER = ["POST_BASELINE_UNVERIFIED", "STRADDLES_BASELINE", "UNKNOWN",
               "POST_BASELINE_PERMITTED", "PRE_BASELINE"]
_ILLEGAL_ORDER = ["A_STRONG", "B_MODERATE", "C_WEAK", "NONE"]


def _worst(current, new, order):
    if new is None or new not in order:
        return current
    if current is None or current not in order:
        return new
    return current if order.index(current) <= order.index(new) else new


def build_survey_focus(dominant_type: str | None, g: dict) -> str:
    """현장에서 무엇을 확인해야 하는지 구체적 지시문을 만든다.

    "가서 보세요"가 아니라 "무엇을 어떤 근거로 확인하라"를 적어야 야장이
    실제로 쓸모가 있다. 자동판정이 약한 지점(대장 미매칭, 방향 불일치)을
    명시적으로 지목한다.
    """
    focus = []
    if dominant_type == "NEW_BUILDING":
        focus.append("신축 여부 및 착공·사용승인 이력 확인")
    elif dominant_type == "EXPANSION_OR_RECONSTRUCTION":
        focus.append("증축·개축 범위와 허가 범위 일치 여부 확인")
    elif dominant_type == "DEMOLITION":
        focus.append("철거 여부 및 멸실신고 이력 확인 (근거가 약한 판정이므로 존치 여부부터 확인)")
    elif dominant_type == "OTHER_CHANGE":
        focus.append("토지 형질변경·야적·가설물 등 지장물 발생 여부 확인")

    if g["register"] == 0 and g["candidates"] > 0:
        focus.append("건축물대장 미매칭 - 무허가 또는 미등록 여부 확인 필요")
    if g["dir_flag"]:
        focus.append(f"밝기 변화 방향 불일치 {g['dir_flag']}건 - 자동판정 신뢰도 낮음, 육안 확인 필수")
    if g["comp"] == "POST_BASELINE_UNVERIFIED":
        focus.append("보상 기준일 이후 변화로 추정되며 행정근거 미확인 - 보상 실무 확인 우선")
    if g["illegal"] == "A_STRONG":
        focus.append("복수 증거가 일치하는 무허가 의심 - 지자체 건축과 협의 검토")
    if g["candidates"] >= 5:
        focus.append(f"후보 {g['candidates']}건이 한 현장에 몰려 있음 - 1회 방문으로 일괄 확인 가능")
    return " / ".join(focus)[:400] or "변화 내용 육안 확인"


def order_route(sites: list[dict], start: tuple[float, float] | None = None) -> list[dict]:
    """등급을 유지한 채 등급 안에서 이동거리를 줄이는 순서를 부여한다.

    최근접 이웃 휴리스틱을 등급별로 적용한다. HIGH를 전부 돈 뒤 MEDIUM으로
    넘어가는 순서는 유지하면서(우선순위는 타협하지 않는다), 같은 등급
    안에서는 가까운 곳부터 돌게 한다.

    Args:
        sites: collect_sites() 결과.
        start: 출발 좌표. None이면 각 등급의 첫 현장에서 시작.

    Returns:
        route_order가 채워진 목록 (원본 리스트도 갱신된다).
    """
    ordered: list[dict] = []
    cursor = start
    for tier in ("HIGH", "MEDIUM", "LOW"):
        pool = [s for s in sites if s["site_priority"] == tier and s["x"] is not None]
        # 등급 안에서 우선 최고점 현장부터 시작해 최근접 이웃으로 잇는다
        if not pool:
            continue
        if cursor is None:
            current = max(pool, key=lambda s: s["max_priority_score"])
        else:
            current = min(pool, key=lambda s: _d2(cursor, s))
        while pool:
            pool.remove(current)
            ordered.append(current)
            cursor = (current["x"], current["y"])
            if pool:
                current = min(pool, key=lambda s: _d2(cursor, s))

    # 좌표가 없는 현장은 맨 뒤에
    ordered += [s for s in sites if s["x"] is None]
    for i, s in enumerate(ordered, start=1):
        s["route_order"] = i

    total = sum(
        _d2((ordered[i]["x"], ordered[i]["y"]), ordered[i + 1]) ** 0.5
        for i in range(len(ordered) - 1)
        if ordered[i]["x"] is not None and ordered[i + 1]["x"] is not None
    )
    logger.info("[SURVEY] 동선 순서 부여 완료: %d곳, 총 이동거리 약 %.1fkm", len(ordered), total / 1000)
    return ordered


def _d2(pt: tuple[float, float], site: dict) -> float:
    return (pt[0] - site["x"]) ** 2 + (pt[1] - site["y"]) ** 2


def write_sites_featureclass(sites: list[dict], out_fc: str, spatial_reference) -> str:
    """현장 목록을 포인트 Feature Class로 저장한다 (Web Map / 모바일 앱용)."""
    delete_if_exists(out_fc)
    path = Path(out_fc)
    arcpy.management.CreateFeatureclass(
        str(path.parent), path.name, "POINT", spatial_reference=spatial_reference
    )
    for name, ftype, length in SITE_FIELDS:
        add_field_if_missing(out_fc, name, ftype, field_length=length)

    names = [n for n, _, _ in SITE_FIELDS]
    with arcpy.da.InsertCursor(out_fc, ["SHAPE@XY"] + names) as cur:
        for s in sites:
            if s["x"] is None:
                continue
            cur.insertRow([(s["x"], s["y"])] + [s.get(n) for n in names])
    logger.info("[SURVEY] 현장 포인트 레이어 저장: %s (%d곳)", out_fc, len(sites))
    return out_fc


WORKORDER_COLUMNS = [
    ("route_order", "방문순서"),
    ("site_priority", "우선순위"),
    ("site_id", "현장ID"),
    ("dominant_change_type", "주요변화유형"),
    ("candidate_count", "후보건수"),
    ("building_count", "건물수"),
    ("total_change_area_m2", "변화면적(m2)"),
    ("parcel_pnu_list", "관련필지PNU"),
    ("x", "X_EPSG5186"),
    ("y", "Y_EPSG5186"),
    ("lon", "경도"),
    ("lat", "위도"),
    ("compensation_status", "보상기준일판정"),
    ("illegal_grade", "무허가의심등급"),
    ("register_matched_count", "대장매칭건수"),
    ("directional_flag_count", "방향불일치건수"),
    ("survey_focus", "현장확인사항"),
    ("manual_result", "현장판정(기입)"),
    ("photo_taken", "사진촬영(기입)"),
    ("surveyor", "조사자(기입)"),
    ("survey_date", "조사일자(기입)"),
    ("comment", "비고(기입)"),
]


def export_work_order(sites: list[dict], out_csv: str | Path, spatial_reference=None) -> Path:
    """현장조사 야장 CSV를 만든다 (사람이 그대로 들고 나갈 수 있는 형태).

    "(기입)" 컬럼은 빈 칸으로 두어 현장에서 채우게 한다. 채워진 야장을
    다시 읽으면 정확도 평가(Human Validation)로 바로 이어진다.

    위경도 컬럼을 함께 넣는 이유: 현장에서 쓰는 지도앱·GPS는 대부분
    EPSG:5186 미터좌표를 못 받는다.
    """
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    if spatial_reference is not None:
        wgs84 = arcpy.SpatialReference(4326)
        for s in sites:
            if s["x"] is None:
                s["lon"] = s["lat"] = None
                continue
            pt = arcpy.PointGeometry(arcpy.Point(s["x"], s["y"]), spatial_reference).projectAs(wgs84)
            s["lon"] = round(pt.centroid.X, 6)
            s["lat"] = round(pt.centroid.Y, 6)

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([label for _, label in WORKORDER_COLUMNS])
        for s in sorted(sites, key=lambda x: x.get("route_order") or 9999):
            writer.writerow([s.get(key, "") if s.get(key) is not None else ""
                             for key, _ in WORKORDER_COLUMNS])

    logger.info("[SURVEY] 현장조사 야장 저장: %s (%d곳)", out_csv, len(sites))
    return out_csv
