"""건물 Overlay · 건축물대장 조인 · 변화유형 분류 · 우선순위 점수 (arcpy).

Baseline의 `src/buildings/*` + `src/scoring/priority.py`를 arcpy로 옮긴 것으로,
**판정 규칙과 가중치는 동일**하다(두 경로의 결과를 대조할 수 있어야 하므로).

Overlay는 arcpy.analysis.Intersect로 건물×변화 교차쌍을 만든 뒤 파이썬에서
집계한다. GDB 조인/요약 도구를 연쇄하는 것보다 이 편이 읽기 쉽고, 필드명
충돌(Intersect가 FID_* 를 만들고 동명 필드에 _1 접미사를 붙이는 문제)을
피할 수 있다.

`site_id`(건물이 속한 대표 change polygon)는 이 파이프라인의 핵심 개념이다.
대형 공사장 하나가 건물 footprint 여러 개에 걸치면 "건물 수"가 실제
"현장 수"보다 훨씬 크게 잡히고, 그대로 현장조사를 지시하면 같은 공사장을
여러 번 방문하게 된다(육안검수로 실측 확인 - poc_summary.md 5번).
현장조사 안내는 반드시 site_id 기준 현장 수로 한다.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import arcpy

from .env import add_field_if_missing, delete_if_exists, fc_path

logger = logging.getLogger("arcpy_pipeline.buildings")

NEW_BUILDING = "NEW_BUILDING"
DEMOLITION = "DEMOLITION"
EXPANSION_OR_RECONSTRUCTION = "EXPANSION_OR_RECONSTRUCTION"
OTHER_CHANGE = "OTHER_CHANGE"

REGISTER_FIELDS = [
    ("mainPurpsCdNm", "TEXT", 100),   # 주용도
    ("archArea", "DOUBLE", None),     # 건축면적
    ("totArea", "DOUBLE", None),      # 연면적
    ("grndFlrCnt", "LONG", None),     # 지상층수
    ("ugrndFlrCnt", "LONG", None),    # 지하층수
    ("pmsDay", "TEXT", 8),            # 허가일
    ("stcnsDay", "TEXT", 8),          # 착공일
    ("useAprDay", "TEXT", 8),         # 사용승인일
]

OVERLAY_FIELDS = [
    ("building_area_m2", "DOUBLE", None),
    ("change_area_m2", "DOUBLE", None),
    ("change_ratio", "DOUBLE", None),
    ("max_change_score", "DOUBLE", None),
    ("near_change", "SHORT", None),
    ("site_id", "TEXT", 40),
    ("brightness_delta", "DOUBLE", None),
]


def _parse_yyyymmdd(s) -> date | None:
    if not s or not isinstance(s, str) or len(s.strip()) != 8:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y%m%d").date()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Overlay
# --------------------------------------------------------------------------
def overlay_buildings_with_changes(
    buildings_fc: str,
    change_fc: str,
    out_fc: str,
    buffer_m: float = 3.0,
) -> str:
    """건물별로 change polygon과의 중첩을 계산해 필드로 붙인다.

    Args:
        buildings_fc: AOI로 clip된 건물 footprint Feature Class.
        change_fc: change_detect.polygonize() 결과.
        out_fc: 결과 Feature Class.
        buffer_m: "건물 본체는 미교차이나 주변에 변화가 있음"을 판단할 버퍼(m).

    Returns:
        out_fc 경로. OVERLAY_FIELDS가 채워져 있다.
    """
    delete_if_exists(out_fc)
    arcpy.management.CopyFeatures(buildings_fc, out_fc)
    for name, ftype, length in OVERLAY_FIELDS:
        add_field_if_missing(out_fc, name, ftype, field_length=length)

    change_count = int(arcpy.management.GetCount(change_fc)[0])
    building_area = {
        oid: area for oid, area in arcpy.da.SearchCursor(out_fc, ["OID@", "SHAPE@AREA"])
    }

    if change_count == 0:
        logger.warning("[BUILDING] change polygon이 0건 - overlay를 건너뜁니다")
        with arcpy.da.UpdateCursor(
            out_fc, ["OID@", "building_area_m2", "change_area_m2", "change_ratio", "near_change"]
        ) as cur:
            for row in cur:
                row[1] = round(building_area.get(row[0], 0.0), 2)
                row[2] = 0.0
                row[3] = 0.0
                row[4] = 0
                cur.updateRow(row)
        return out_fc

    has_brightness = any(f.name == "brightness_delta" for f in arcpy.ListFields(change_fc))
    attr_fields = ["change_id", "max_change_score"] + (["brightness_delta"] if has_brightness else [])
    change_attrs = {
        row[0]: {
            "max_change_score": row[1],
            "brightness_delta": row[2] if has_brightness else None,
        }
        for row in arcpy.da.SearchCursor(change_fc, attr_fields)
    }

    scratch = arcpy.env.scratchGDB
    inter = str(Path(scratch) / "_bld_chg_intersect")
    delete_if_exists(inter)
    # JOIN_FID를 남겨 어느 건물의 교차쌍인지 추적한다. ONLY_FID면 속성이
    # 다 날아가 change_id를 잃으므로 ALL을 쓴다.
    arcpy.analysis.Intersect([out_fc, change_fc], inter, join_attributes="ALL")

    # 건물 OID를 다시 얻기 위해 Intersect가 만든 FID_<원본이름> 필드를 찾는다.
    fid_field = _find_fid_field(inter, out_fc)

    agg_area: dict[int, float] = defaultdict(float)
    agg_max: dict[int, float] = {}
    per_site_area: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for fid, cid, area in arcpy.da.SearchCursor(inter, [fid_field, "change_id", "SHAPE@AREA"]):
        if fid is None or fid < 0:
            continue
        agg_area[fid] += area
        per_site_area[fid][cid] += area
        score = (change_attrs.get(cid) or {}).get("max_change_score")
        if score is not None:
            agg_max[fid] = max(agg_max.get(fid, float("-inf")), float(score))
    delete_if_exists(inter)

    # 버퍼 내 변화 존재 여부 (건물 본체 미교차인 경우에만 의미가 있다)
    near_fids = _fids_with_change_within_buffer(out_fc, change_fc, buffer_m, scratch)

    with arcpy.da.UpdateCursor(
        out_fc,
        ["OID@", "building_area_m2", "change_area_m2", "change_ratio",
         "max_change_score", "near_change", "site_id", "brightness_delta"],
    ) as cur:
        for row in cur:
            oid = row[0]
            b_area = building_area.get(oid, 0.0)
            c_area = agg_area.get(oid, 0.0)
            row[1] = round(b_area, 2)
            row[2] = round(c_area, 2)
            row[3] = round(c_area / b_area, 4) if b_area > 0 else 0.0
            row[4] = agg_max.get(oid)
            row[5] = 1 if (c_area == 0 and oid in near_fids) else 0
            if per_site_area.get(oid):
                # 여러 change polygon에 걸치면 교차면적이 가장 큰 것을 대표로 삼는다
                site = max(per_site_area[oid].items(), key=lambda kv: kv[1])[0]
                row[6] = site
                row[7] = (change_attrs.get(site) or {}).get("brightness_delta")
            else:
                row[6] = None
                row[7] = None
            cur.updateRow(row)

    n_changed = sum(1 for a in agg_area.values() if a > 0)
    n_sites = len({
        max(sites.items(), key=lambda kv: kv[1])[0] for sites in per_site_area.values() if sites
    })
    logger.info(
        "[BUILDING] Overlay 완료: 건물 %d개 중 변화 %d개 (서로 다른 현장 %d곳)",
        len(building_area), n_changed, n_sites,
    )
    return out_fc


def _find_fid_field(intersect_fc: str, source_fc: str) -> str:
    """Intersect 결과에서 원본 Feature Class의 FID 필드명을 찾는다.

    arcpy는 "FID_<입력이름>" 형태로 만들지만, 이름이 길거나 중복되면
    잘리거나 접미사가 붙는다. 이름을 문자열로 조립해 추측하는 대신
    실제 필드 목록에서 찾는다.
    """
    stem = Path(source_fc).name
    candidates = [f.name for f in arcpy.ListFields(intersect_fc) if f.name.upper().startswith("FID_")]
    if not candidates:
        raise RuntimeError(f"[BUILDING] Intersect 결과에 FID_* 필드가 없습니다: {intersect_fc}")
    exact = [c for c in candidates if c[4:].lower() == stem.lower()]
    if exact:
        return exact[0]
    prefixed = [c for c in candidates if stem.lower().startswith(c[4:].lower())]
    return (prefixed or candidates)[0]


def _fids_with_change_within_buffer(
    buildings_fc: str, change_fc: str, buffer_m: float, scratch: str
) -> set[int]:
    """버퍼 안에 change polygon이 걸치는 건물 OID 집합."""
    if buffer_m <= 0:
        return set()
    buf = str(Path(scratch) / "_bld_buffer")
    lyr = "bld_buf_lyr"
    delete_if_exists(buf)
    arcpy.analysis.Buffer(buildings_fc, buf, f"{buffer_m} Meters")
    fid_field = "ORIG_FID" if any(f.name == "ORIG_FID" for f in arcpy.ListFields(buf)) else None

    arcpy.management.MakeFeatureLayer(buf, lyr)
    try:
        arcpy.management.SelectLayerByLocation(lyr, "INTERSECT", change_fc)
        field = fid_field or "OID@"
        result = {r[0] for r in arcpy.da.SearchCursor(lyr, [field])}
    finally:
        arcpy.management.Delete(lyr)
        delete_if_exists(buf)
    return result


# --------------------------------------------------------------------------
# 건축물대장 조인 (STEP 13)
# --------------------------------------------------------------------------
def join_building_register(buildings_fc: str, register_json: str) -> dict:
    """건축물대장 표제부를 PNU + 도로명주소 이중 키로 조인한다.

    두 키를 함께 쓰는 이유(Baseline validation.py에서 실측으로 규명):
    - PNU 키: VWorld 건물 layer의 산여부(10번째 자리)가 표제부와 체계적으로
      어긋난다(VWorld 1/2, 표제부 0). 그 자리를 뺀 9자리로 조인해야 맞는다.
    - 도로명주소 키: 표제부 naRoadCd는 앞 5자리가 시군구코드라 VWorld의
      rn_cd(7자리)와 자릿수가 안 맞는다. 뒤 7자리를 써야 한다.

    Returns:
        {"matched", "total", "pnu", "road_address"} 매칭 통계.
    """
    with open(register_json, encoding="utf-8") as f:
        items = json.load(f)

    for name, ftype, length in REGISTER_FIELDS:
        add_field_if_missing(buildings_fc, name, ftype, field_length=length)
    add_field_if_missing(buildings_fc, "has_register_match", "SHORT")
    add_field_if_missing(buildings_fc, "match_method", "TEXT", field_length=20)

    by_pnu: dict[str, dict] = {}
    by_road: dict[str, dict] = {}
    for it in items:
        # 한 키에 여러 레코드가 걸리면 사용승인일이 가장 최근인 것을 쓴다
        pnu = str(it.get("pnu") or "")
        if len(pnu) >= 11:
            key9 = pnu[:10] + pnu[11:]
            if _newer(it, by_pnu.get(key9)):
                by_pnu[key9] = it
        rk = _register_road_key(it)
        if rk and _newer(it, by_road.get(rk)):
            by_road[rk] = it

    field_names = [n for n, _, _ in REGISTER_FIELDS]
    cursor_fields = ["pnu", "rn_cd", "buld_no", "has_register_match", "match_method"] + field_names
    available = {f.name for f in arcpy.ListFields(buildings_fc)}
    missing = [f for f in ("pnu", "rn_cd", "buld_no") if f not in available]
    if missing:
        raise ValueError(
            f"[VALIDATION] 건물 레이어에 조인 키 필드가 없습니다: {missing}. "
            "VWorld lt_c_spbd 원본 속성이 보존되어 있는지 확인하세요."
        )

    stats = {"matched": 0, "total": 0, "pnu": 0, "road_address": 0}
    with arcpy.da.UpdateCursor(buildings_fc, cursor_fields) as cur:
        for row in cur:
            stats["total"] += 1
            pnu, rn_cd, buld_no = row[0], row[1], row[2]
            rec, method = None, None

            if pnu and len(str(pnu)) >= 11:
                key9 = str(pnu)[:10] + str(pnu)[11:]
                rec = by_pnu.get(key9)
                method = "pnu" if rec else None
            if rec is None:
                rk = _building_road_key(rn_cd, buld_no)
                if rk:
                    rec = by_road.get(rk)
                    method = "road_address" if rec else None

            row[3] = 1 if rec else 0
            row[4] = method
            for i, name in enumerate(field_names):
                row[5 + i] = _coerce(rec.get(name)) if rec else None
            if rec:
                stats["matched"] += 1
                stats[method] += 1
            cur.updateRow(row)

    pct = 100.0 * stats["matched"] / max(stats["total"], 1)
    logger.info(
        "[VALIDATION] 건축물대장 매칭: %d / %d (%.1f%%) - pnu=%d, road_address=%d",
        stats["matched"], stats["total"], pct, stats["pnu"], stats["road_address"],
    )
    stats["match_rate_pct"] = round(pct, 2)
    return stats


def _newer(candidate: dict, current: dict | None) -> bool:
    if current is None:
        return True
    return str(candidate.get("useAprDay") or "") > str(current.get("useAprDay") or "")


def _register_road_key(item: dict) -> str | None:
    road = str(item.get("naRoadCd") or "").strip()
    if not road:
        return None
    return f"{road[-7:]}_{str(item.get('naMainBun') or 0).zfill(4)}_{str(item.get('naSubBun') or 0).zfill(4)}"


def _building_road_key(rn_cd, buld_no) -> str | None:
    if not rn_cd:
        return None
    parts = str(buld_no or "").split("-")
    main = (parts[0].strip() or "0")
    sub = (parts[1].strip() if len(parts) > 1 else "0") or "0"
    return f"{str(rn_cd).strip()}_{main.zfill(4)}_{sub.zfill(4)}"


def _coerce(v):
    """표제부 값은 문자열로 오는 경우가 많다 - 숫자 필드에 넣을 수 있게 변환."""
    if v is None or v == "":
        return None
    return v


def compute_administrative_uncertainty(
    buildings_fc: str, t1_date: date, t2_date: date
) -> None:
    """행정정보로 변화가 설명되는 정도 (0=완전 설명, 1=미설명)를 필드로 채운다.

    - 대장 매칭 없음 → 1.0 (완전 불확실)
    - 매칭 + 사용승인일이 T1~T2 사이 → 0.1 (인허가로 설명됨)
    - 매칭 + 사용승인일이 구간 밖 → 0.6 (등록은 있으나 이번 변화와 대응 안 됨)
    """
    add_field_if_missing(buildings_fc, "administrative_uncertainty", "DOUBLE")
    has_match = any(f.name == "has_register_match" for f in arcpy.ListFields(buildings_fc))
    fields = ["administrative_uncertainty", "useAprDay", "has_register_match"] if has_match else \
             ["administrative_uncertainty"]
    with arcpy.da.UpdateCursor(buildings_fc, fields) as cur:
        for row in cur:
            if not has_match:
                row[0] = 1.0
            elif not row[2]:
                row[0] = 1.0
            else:
                use_apr = _parse_yyyymmdd(row[1])
                row[0] = 0.1 if (use_apr and t1_date <= use_apr <= t2_date) else 0.6
            cur.updateRow(row)


# --------------------------------------------------------------------------
# 분류
# --------------------------------------------------------------------------
def classify_building_changes(
    overlaid_fc: str,
    new_building_ratio_min: float = 0.5,
    t1_date: date | None = None,
    t2_date: date | None = None,
) -> dict:
    """change_ratio / 대장 사용승인일 / 밝기방향으로 변화유형을 분류한다.

    근거 위계 (Baseline classify.py와 동일):
    1. 대장 매칭 + 사용승인일이 T1~T2 사이 → NEW_BUILDING **확정**
    2. 대장 매칭 + 사용승인일이 T1 이전 → EXPANSION_OR_RECONSTRUCTION **확정**
    3. 미매칭 + change_ratio >= 임계 → NEW_BUILDING (휴리스틱)
    4. 미매칭 + change_ratio < 임계 → EXPANSION_OR_RECONSTRUCTION (휴리스틱)

    휴리스틱 판정에는 brightness_delta로 방향 일치 여부를 함께 표시한다
    (`directional_consistency_flag`). 신축/증축이면 새 구조물이 나타나
    밝아지는 방향을 기대하는데 실제로는 어두워진 사례가 육안검수에서
    나왔기 때문이다. 밝기만으로 라벨을 뒤집기엔 근거가 약해(계절/식생 등
    다른 원인 가능) 플래그만 남기고 판단은 현장조사자 몫으로 둔다.

    Returns:
        change_type별 건수 dict.
    """
    add_field_if_missing(overlaid_fc, "change_type", "TEXT", field_length=40)
    add_field_if_missing(overlaid_fc, "classification_note", "TEXT", field_length=400)
    add_field_if_missing(overlaid_fc, "directional_consistency_flag", "SHORT")

    has_register = any(f.name == "has_register_match" for f in arcpy.ListFields(overlaid_fc))
    fields = [
        "change_ratio", "near_change", "brightness_delta",
        "change_type", "classification_note", "directional_consistency_flag",
    ]
    if has_register:
        fields += ["has_register_match", "useAprDay"]

    counts: dict[str, int] = defaultdict(int)
    inconsistent = 0
    with arcpy.da.UpdateCursor(overlaid_fc, fields) as cur:
        for row in cur:
            ratio = row[0] or 0.0
            near = bool(row[1])
            delta = row[2]
            matched = bool(row[6]) if has_register else False
            use_apr = _parse_yyyymmdd(row[7]) if has_register else None

            ctype, note, flag = _classify_one(
                ratio, near, delta, matched, use_apr,
                new_building_ratio_min, t1_date, t2_date,
            )
            row[3], row[4], row[5] = ctype, note, flag
            counts[ctype or "NONE"] += 1
            if flag == 0:
                inconsistent += 1
            cur.updateRow(row)

    logger.info("[BUILDING] 분류 완료: %s (방향 불일치 %d건)", dict(counts), inconsistent)
    return dict(counts)


def _classify_one(ratio, near, delta, matched, use_apr, ratio_min, t1_date, t2_date):
    if ratio == 0 and near:
        return OTHER_CHANGE, "건물 본체 미교차, 버퍼 내 변화만 존재", None
    if ratio == 0:
        return None, "건물과 무관 (change_ratio=0)", None
    if matched and use_apr and t1_date and t2_date and t1_date <= use_apr <= t2_date:
        return (NEW_BUILDING,
                f"사용승인일={use_apr.isoformat()}이 T1~T2 사이 - 신축 확정(건축물대장 근거)",
                None)
    if matched and use_apr:
        return (EXPANSION_OR_RECONSTRUCTION,
                f"사용승인일={use_apr.isoformat()}로 T1 이전부터 존재 - 증축/개축 확정(건축물대장 근거)",
                None)

    consistent = 1 if (delta is None or delta >= 0) else 0
    if ratio >= ratio_min:
        ctype = NEW_BUILDING
        note = f"change_ratio={ratio:.2f} >= {ratio_min} - 신축 추정 (대장 미매칭, 휴리스틱)"
        expect = "신축"
    else:
        ctype = EXPANSION_OR_RECONSTRUCTION
        note = f"change_ratio={ratio:.2f} - 부분 변화, 증축/개축 추정 (대장 미매칭, 휴리스틱)"
        expect = "증축"
    if consistent == 0:
        note += f" - brightness_delta={delta:+.1f}(T2가 더 어두움)로 {expect} 기대 방향과 불일치, 재확인 권장"
    return ctype, note, consistent


def classify_unmatched_changes(
    change_fc: str,
    buildings_fc: str,
    out_fc: str,
    demolition_score_min: float = 0.6,
    min_area_m2: float = 50.0,
) -> str:
    """어떤 건물과도 교차하지 않는 change polygon을 DEMOLITION/OTHER_CHANGE로 분류.

    건물 layer가 "현재 시점" 스냅샷이라 T1에 건물이 있었는지는 알 수 없다.
    따라서 DEMOLITION은 이미지 변화 강도만으로 추정하는 약한 근거이며,
    brightness_delta > 0(T2가 오히려 밝아짐)이면 철거와 정반대 방향이므로
    OTHER_CHANGE로 되돌린다 - 라벨을 바꿀 뿐 후보에서 지우지는 않는다.
    """
    lyr = "chg_unmatched_lyr"
    delete_if_exists(out_fc)
    arcpy.management.MakeFeatureLayer(change_fc, lyr)
    try:
        arcpy.management.SelectLayerByLocation(
            lyr, "INTERSECT", buildings_fc, selection_type="NEW_SELECTION", invert_spatial_relationship="INVERT"
        )
        arcpy.management.CopyFeatures(lyr, out_fc)
    finally:
        arcpy.management.Delete(lyr)

    add_field_if_missing(out_fc, "change_type", "TEXT", field_length=40)
    add_field_if_missing(out_fc, "classification_note", "TEXT", field_length=400)
    add_field_if_missing(out_fc, "site_id", "TEXT", field_length=40)
    add_field_if_missing(out_fc, "directional_consistency_flag", "SHORT")
    add_field_if_missing(out_fc, "change_ratio", "DOUBLE")

    has_brightness = any(f.name == "brightness_delta" for f in arcpy.ListFields(out_fc))
    fields = ["change_id", "mean_change_score", "change_area_m2",
              "change_type", "classification_note", "site_id", "change_ratio"]
    if has_brightness:
        fields.append("brightness_delta")

    with arcpy.da.UpdateCursor(out_fc, fields) as cur:
        for row in cur:
            score = row[1] or 0.0
            area = row[2] or 0.0
            delta = row[7] if has_brightness else None
            if score >= demolition_score_min and area >= min_area_m2:
                if delta is not None and delta > 0:
                    row[3] = OTHER_CHANGE
                    row[4] = (
                        f"건물 미교차 + mean_change_score={score:.2f}이나 "
                        f"brightness_delta={delta:+.1f}(T2가 더 밝음)로 철거 방향과 불일치 - "
                        "미등록 신축 가능성, 재확인 필요"
                    )
                else:
                    row[3] = DEMOLITION
                    row[4] = (
                        f"건물 미교차 + mean_change_score={score:.2f} >= {demolition_score_min} "
                        "- 철거 추정 (T1 건물 유무 미확인, 이미지 변화 강도 기반)"
                    )
            else:
                row[3] = OTHER_CHANGE
                row[4] = "건물 미교차, 변화강도/면적 기준 미달 - 토지조성/기타 추정"
            row[5] = row[0]
            row[6] = 0.0
            cur.updateRow(row)

    n = int(arcpy.management.GetCount(out_fc)[0])
    logger.info("[BUILDING] 건물 미교차 변화 %d건 분류 완료", n)
    return out_fc


# --------------------------------------------------------------------------
# 우선순위 점수
# --------------------------------------------------------------------------
def compute_priority_score(
    fc: str,
    weights: dict[str, float],
    high_threshold: float = 0.7,
    medium_threshold: float = 0.4,
) -> dict:
    """가중합 priority_score와 HIGH/MEDIUM/LOW 등급을 계산한다.

    구성요소(Baseline priority.py와 동일):
    - change_confidence: 변화탐지 점수 (max_change_score, 없으면 mean)
    - change_ratio: 건물 대비 변화 면적 비율
    - administrative_uncertainty: 행정정보로 설명 안 되는 정도
      (buildings.compute_administrative_uncertainty가 채운 값. 없으면 1.0)
    - building_relevance: 건물 관련 변화유형이면 1.0, 아니면 0.3
    """
    for name in ("confidence", "building_relevance", "priority_score"):
        add_field_if_missing(fc, name, "DOUBLE")
    add_field_if_missing(fc, "inspection_priority", "TEXT", field_length=10)
    add_field_if_missing(fc, "administrative_uncertainty", "DOUBLE")

    names = {f.name for f in arcpy.ListFields(fc)}
    score_field = "max_change_score" if "max_change_score" in names else "mean_change_score"

    fields = [score_field, "change_ratio", "administrative_uncertainty", "change_type",
              "confidence", "building_relevance", "priority_score", "inspection_priority"]
    counts: dict[str, int] = defaultdict(int)
    with arcpy.da.UpdateCursor(fc, fields) as cur:
        for row in cur:
            confidence = min(max(row[0] or 0.0, 0.0), 1.0)
            ratio = min(max(row[1] or 0.0, 0.0), 1.0)
            admin = row[2] if row[2] is not None else 1.0
            relevance = 1.0 if row[3] in (NEW_BUILDING, EXPANSION_OR_RECONSTRUCTION, DEMOLITION) else 0.3

            score = round(
                weights["change_confidence"] * confidence
                + weights["change_ratio"] * ratio
                + weights["administrative_uncertainty"] * admin
                + weights["building_relevance"] * relevance,
                4,
            )
            tier = "HIGH" if score >= high_threshold else ("MEDIUM" if score >= medium_threshold else "LOW")
            row[4], row[5], row[6], row[7] = confidence, relevance, score, tier
            counts[tier] += 1
            cur.updateRow(row)

    logger.info("[SCORING] 우선순위 등급: %s", dict(counts))
    return dict(counts)


def merge_results(building_fc: str, unmatched_fc: str, out_fc: str) -> str:
    """건물 연계 변화 + 건물 미교차 변화를 하나의 결과 레이어로 합친다.

    change_type이 None인(변화와 무관한) 건물은 제외한다.
    """
    lyr = "bld_classified_lyr"
    scratch_sel = str(Path(arcpy.env.scratchGDB) / "_bld_classified")
    delete_if_exists(out_fc, scratch_sel)
    arcpy.management.MakeFeatureLayer(building_fc, lyr, "change_type IS NOT NULL")
    try:
        arcpy.management.CopyFeatures(lyr, scratch_sel)
    finally:
        arcpy.management.Delete(lyr)

    arcpy.management.Merge([scratch_sel, unmatched_fc], out_fc)
    delete_if_exists(scratch_sel)
    n = int(arcpy.management.GetCount(out_fc)[0])
    logger.info("[BUILDING] 최종 변화 후보 %d건 -> %s", n, out_fc)
    return out_fc
