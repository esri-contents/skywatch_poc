"""REQ08 - 변화탐지 결과를 지적도·건축물 등 LH 업무데이터와 결합.

LH 애로사항 5번("여러 시스템에 흩어진 데이터의 정합·연계가 어려움")에
대응한다. 변화탐지 결과가 아무리 정확해도 **필지(PNU) 단위로 정리되지
않으면 LH 업무에 바로 못 쓴다** - 보상, 협의, 소유자 통지, 지장물 조사가
전부 필지를 기본 단위로 돌아가기 때문이다.

Baseline에도 `src/data/build_cadastre.py`로 VWorld 연속지적도를 받는
코드는 있었지만 **파이프라인에 연결된 적이 없었다**(handoff.md 10번의
미완료 항목). 이 모듈이 그 연결을 완성한다:

```text
change polygon / 건물 변화 후보
    → 필지(지적도) 공간 조인
    → 필지 단위 집계 (변화면적, 대표 변화유형, 최고 우선순위)
    → 필지 속성(지번, 지목, 면적) + 건축물대장 결합
    → parcel_change_summary  (LH 업무 단위 산출물)
```

필지 단위로 모으면 "건물 76건"이 아니라 "필지 41필"처럼 업무에서 실제로
쓰는 단위로 말할 수 있고, 한 필지에 여러 동이 있어도 한 번만 통지·조사하게
된다. site_id(현장 단위)와 함께 쓰면 중복 방문을 이중으로 막는다.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict

import arcpy

from .env import add_field_if_missing, delete_if_exists

logger = logging.getLogger("arcpy_pipeline.cadastre_link")

PARCEL_FIELDS = [
    ("parcel_pnu", "TEXT", 20),
    ("parcel_jibun", "TEXT", 40),
    ("parcel_area_m2", "DOUBLE", None),
    ("change_area_m2", "DOUBLE", None),
    ("change_area_ratio", "DOUBLE", None),
    ("candidate_count", "SHORT", None),
    ("site_count", "SHORT", None),
    ("dominant_change_type", "TEXT", 40),
    ("max_priority_score", "DOUBLE", None),
    ("inspection_priority", "TEXT", 10),
    ("worst_compensation_status", "TEXT", 32),
    ("worst_illegal_grade", "TEXT", 16),
]

# VWorld 연속지적도(lp_pa_cbnd_*)의 필드명 후보. 레이어/버전마다 조금씩
# 달라서 하드코딩하지 않고 실제 존재하는 것을 고른다.
PNU_CANDIDATES = ("pnu", "PNU", "pnu_cd", "A1")
JIBUN_CANDIDATES = ("jibun", "JIBUN", "addr", "bon_bun", "ADDR")


def _pick_field(fc: str, candidates: tuple[str, ...]) -> str | None:
    names = {f.name.lower(): f.name for f in arcpy.ListFields(fc)}
    for c in candidates:
        if c.lower() in names:
            return names[c.lower()]
    return None


def link_results_to_parcels(
    results_fc: str,
    parcel_fc: str,
    out_fc: str,
    priority_order: tuple[str, ...] = ("HIGH", "MEDIUM", "LOW"),
) -> str:
    """변화 후보를 필지에 공간 조인해 필지 단위로 집계한다.

    Args:
        results_fc: 최종 변화 후보 Feature Class (change_type/priority_score 포함).
        parcel_fc: 연속지적도 필지 Feature Class (build_cadastre 결과).
        out_fc: 결과(변화가 있는 필지만) Feature Class.
        priority_order: 등급 강도 순서 (앞이 가장 강함).

    Returns:
        PARCEL_FIELDS가 채워진 out_fc.
    """
    pnu_field = _pick_field(parcel_fc, PNU_CANDIDATES)
    jibun_field = _pick_field(parcel_fc, JIBUN_CANDIDATES)
    if pnu_field is None:
        raise ValueError(
            f"[CADASTRE] 지적 레이어에서 PNU 필드를 찾지 못했습니다. "
            f"확인한 후보: {PNU_CANDIDATES}, 실제 필드: "
            f"{[f.name for f in arcpy.ListFields(parcel_fc)][:20]}"
        )

    inter = os.path.join(arcpy.env.scratchGDB, "_parcel_results_intersect")
    delete_if_exists(inter, out_fc)
    arcpy.analysis.Intersect([parcel_fc, results_fc], inter, join_attributes="ALL")

    names = {f.name for f in arcpy.ListFields(inter)}
    opt = [f for f in ("change_type", "priority_score", "inspection_priority",
                       "site_id", "compensation_status", "illegal_grade") if f in names]
    fields = [pnu_field, "SHAPE@AREA"] + opt

    agg: dict[str, dict] = defaultdict(
        lambda: {"area": 0.0, "count": 0, "types": defaultdict(float), "sites": set(),
                 "max_score": None, "priority": None, "comp": None, "illegal": None}
    )
    for row in arcpy.da.SearchCursor(inter, fields):
        pnu = row[0]
        if not pnu:
            continue
        rec = dict(zip(opt, row[2:]))
        a = agg[pnu]
        area = row[1] or 0.0
        a["area"] += area
        a["count"] += 1
        if rec.get("change_type"):
            # 대표 변화유형은 "가장 많이 나온 것"이 아니라 "가장 넓은 것"으로
            # 정한다. 작은 조각 여러 개가 큰 변화 하나를 이기면 안 되기 때문.
            a["types"][rec["change_type"]] += area
        if rec.get("site_id"):
            a["sites"].add(rec["site_id"])
        score = rec.get("priority_score")
        if score is not None and (a["max_score"] is None or score > a["max_score"]):
            a["max_score"] = score
        a["priority"] = _stronger(a["priority"], rec.get("inspection_priority"), priority_order)
        a["comp"] = _worse_compensation(a["comp"], rec.get("compensation_status"))
        a["illegal"] = _worse_illegal(a["illegal"], rec.get("illegal_grade"))
    delete_if_exists(inter)

    if not agg:
        logger.warning("[CADASTRE] 변화와 교차하는 필지가 없습니다")

    # 변화가 있는 필지만 뽑아 결과 레이어를 만든다
    where = _in_clause(parcel_fc, pnu_field, list(agg.keys()))
    lyr = "parcel_hit_lyr"
    arcpy.management.MakeFeatureLayer(parcel_fc, lyr, where)
    try:
        arcpy.management.CopyFeatures(lyr, out_fc)
    finally:
        arcpy.management.Delete(lyr)

    for name, ftype, length in PARCEL_FIELDS:
        add_field_if_missing(out_fc, name, ftype, field_length=length)

    dst = [n for n, _, _ in PARCEL_FIELDS]
    src = [pnu_field, "SHAPE@AREA"] + ([jibun_field] if jibun_field else [])
    with arcpy.da.UpdateCursor(out_fc, src + dst) as cur:
        for row in cur:
            pnu = row[0]
            parcel_area = row[1] or 0.0
            jibun = row[2] if jibun_field else None
            a = agg.get(pnu)
            base = len(src)
            row[base + 0] = pnu
            row[base + 1] = jibun
            row[base + 2] = round(parcel_area, 2)
            if a:
                dominant = max(a["types"].items(), key=lambda kv: kv[1])[0] if a["types"] else None
                row[base + 3] = round(a["area"], 2)
                row[base + 4] = round(a["area"] / parcel_area, 4) if parcel_area > 0 else 0.0
                row[base + 5] = a["count"]
                row[base + 6] = len(a["sites"])
                row[base + 7] = dominant
                row[base + 8] = a["max_score"]
                row[base + 9] = a["priority"]
                row[base + 10] = a["comp"]
                row[base + 11] = a["illegal"]
            cur.updateRow(row)

    n = int(arcpy.management.GetCount(out_fc)[0])
    logger.info(
        "[CADASTRE] 필지 단위 집계 완료: 변화 필지 %d필 (후보 %d건 → 필지 %d필)",
        n, sum(v["count"] for v in agg.values()), len(agg),
    )
    return out_fc


def _stronger(current: str | None, new: str | None, order: tuple[str, ...]) -> str | None:
    if new is None:
        return current
    if current is None:
        return new
    return current if order.index(current) <= order.index(new) else new


_COMP_ORDER = ["POST_BASELINE_UNVERIFIED", "STRADDLES_BASELINE", "UNKNOWN",
               "POST_BASELINE_PERMITTED", "PRE_BASELINE"]
_ILLEGAL_ORDER = ["A_STRONG", "B_MODERATE", "C_WEAK", "NONE"]


def _worse_compensation(current: str | None, new: str | None) -> str | None:
    return _worse(current, new, _COMP_ORDER)


def _worse_illegal(current: str | None, new: str | None) -> str | None:
    return _worse(current, new, _ILLEGAL_ORDER)


def _worse(current, new, order):
    if new is None or new not in order:
        return current
    if current is None or current not in order:
        return new
    return current if order.index(current) <= order.index(new) else new


def _in_clause(fc: str, field: str, values: list[str], chunk: int = 900) -> str:
    """긴 IN 절을 안전하게 만든다 (SQL 표현식 길이 한계 회피).

    값이 아주 많으면 IN 절이 DBMS/파일GDB 한계를 넘어 실패한다. 청크로
    쪼개 OR로 잇는다.
    """
    if not values:
        return "1 = 0"
    delim = arcpy.AddFieldDelimiters(fc, field)
    parts = []
    for i in range(0, len(values), chunk):
        quoted = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values[i:i + chunk])
        parts.append(f"{delim} IN ({quoted})")
    return " OR ".join(parts)


def attach_parcel_id_to_results(results_fc: str, parcel_fc: str) -> int:
    """변화 후보 각각에 소속 필지 PNU를 붙인다 (역방향 연계).

    필지 단위 집계와 별개로, 개별 후보에도 "어느 필지인지"가 있어야
    현장조사 야장에 지번을 찍을 수 있다.

    Returns:
        PNU가 채워진 후보 수.
    """
    pnu_field = _pick_field(parcel_fc, PNU_CANDIDATES)
    if pnu_field is None:
        raise ValueError("[CADASTRE] 지적 레이어에서 PNU 필드를 찾지 못했습니다")

    add_field_if_missing(results_fc, "parcel_pnu", "TEXT", field_length=20)
    joined = os.path.join(arcpy.env.scratchGDB, "_results_parcel_sj")
    delete_if_exists(joined)
    # 후보 중심이 어느 필지에 있는지 - 여러 필지에 걸치면 가장 많이 겹치는 필지
    arcpy.analysis.SpatialJoin(
        target_features=results_fc, join_features=parcel_fc, out_feature_class=joined,
        join_operation="JOIN_ONE_TO_ONE", join_type="KEEP_ALL",
        match_option="LARGEST_OVERLAP",
    )
    mapping = {}
    for oid, pnu in arcpy.da.SearchCursor(joined, ["TARGET_FID", pnu_field]):
        mapping[oid] = pnu
    delete_if_exists(joined)

    filled = 0
    with arcpy.da.UpdateCursor(results_fc, ["OID@", "parcel_pnu"]) as cur:
        for row in cur:
            pnu = mapping.get(row[0])
            if pnu:
                row[1] = pnu
                filled += 1
                cur.updateRow(row)
    logger.info("[CADASTRE] 후보 %d건에 필지 PNU 연계", filled)
    return filled
