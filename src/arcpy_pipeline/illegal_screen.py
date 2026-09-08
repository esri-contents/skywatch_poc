"""REQ02 - 불법·무허가 개발 의심지역 식별.

LH 요구 기능 2순위. **이 모듈은 위법을 판정하지 않는다.** 위반건축물 판정은
건축법상 행정처분이며, 지자체(고양시 도시균형개발과 등)의 조사·인정 절차를
거친다. 실제로 확인한 결과 경기도 "위반건축물 현황" 공개데이터는 분기별
집계(288행)뿐이고 건물 단위 목록은 공개 API로 제공되지 않는다 - 즉
**"이 건물이 위반이다"의 정답 데이터 자체가 존재하지 않는다**
(`outputs/reports/handoff.md` 7번).

따라서 여기서 산출하는 것은 판정이 아니라 **"보유한 행정정보로 설명되지
않는 정도"의 등급**이다. 세 갈래 증거를 쓴다:

1. **미등록 신축 신호**: 영상에서 뚜렷한 변화가 잡혔는데 건축물대장에
   해당 건물이 없다. 신도시 조성지구에서는 정상적인 공사 중 건물도
   대장이 아직 없을 수 있어(실측 매칭률 67.7%), 이것만으로는 약한 증거다.
2. **면적 초과 신호**: 건축물대장의 건축면적(archArea)보다 영상/지적
   기준 실제 footprint가 뚜렷하게 크다. 무단증축의 전형적 패턴이며,
   **이 프로젝트가 실제로 계산할 수 있는 가장 강한 정량 증거**다.
3. **변화 강도·유형 신호**: 변화 확신도, change_ratio, 방향 일치 여부.

면적 초과 신호의 한계(반드시 함께 읽을 것):
- VWorld 건물 footprint는 처마·발코니 돌출부를 포함할 수 있고, 건축법상
  건축면적(수평투영면적) 정의와 완전히 같지 않다.
- 한 대장 레코드에 여러 동이 묶이거나 반대로 한 동이 여러 레코드로 갈릴
  수 있어, 1:1 대응이 깨진 건이 섞인다.
따라서 여유율(`tolerance_ratio`)을 크게 잡아 명백한 초과만 잡고, 등급은
"의심"이지 "위반"이 아니다.
"""

from __future__ import annotations

import logging

import arcpy

from .env import add_field_if_missing

logger = logging.getLogger("arcpy_pipeline.illegal_screen")

GRADE_A = "A_STRONG"      # 복수의 독립 증거가 일치 - 우선 확인
GRADE_B = "B_MODERATE"    # 단일 증거 - 확인 권장
GRADE_C = "C_WEAK"        # 참고 수준
GRADE_NONE = "NONE"       # 행정정보로 설명됨

SCREEN_FIELDS = [
    ("illegal_grade", "TEXT", 16),
    ("illegal_score", "DOUBLE", None),
    ("area_excess_ratio", "DOUBLE", None),
    ("illegal_evidence", "TEXT", 500),
]


def evaluate_illegal_signals(
    row: dict,
    tolerance_ratio: float = 1.3,
    strong_change_ratio: float = 0.5,
    strong_confidence: float = 0.6,
) -> dict:
    """변화 후보 한 건의 "행정정보로 설명되지 않는 정도"를 등급화한다 (순수 함수).

    Args:
        row: has_register_match / archArea / building_area_m2 / change_ratio /
            max_change_score / change_type / useAprDay /
            directional_consistency_flag 를 담은 dict.
        tolerance_ratio: 실제 footprint / 대장 건축면적 이 값을 넘으면
            면적 초과 신호로 본다. 기본 1.3(30% 초과) - footprint 정의
            차이와 1:1 대응 오차를 흡수하기 위해 넉넉히 잡았다.
        strong_change_ratio: 이 이상이면 "건물 전체가 바뀜" 수준.
        strong_confidence: 변화탐지 확신도 임계.

    Returns:
        {"illegal_grade", "illegal_score", "area_excess_ratio", "illegal_evidence"}
    """
    evidence: list[str] = []
    signals = 0
    score = 0.0

    matched = bool(row.get("has_register_match"))
    change_ratio = _num(row.get("change_ratio")) or 0.0
    confidence = _num(row.get("max_change_score")) or 0.0
    change_type = row.get("change_type")
    footprint = _num(row.get("building_area_m2"))
    arch_area = _num(row.get("archArea"))

    # --- 신호 1: 미등록 상태에서의 뚜렷한 변화 ---
    if not matched and change_type in ("NEW_BUILDING", "EXPANSION_OR_RECONSTRUCTION"):
        if change_ratio >= strong_change_ratio and confidence >= strong_confidence:
            signals += 1
            score += 0.45
            evidence.append(
                f"건축물대장 미매칭 + change_ratio={change_ratio:.2f}·확신도={confidence:.2f}로 "
                "뚜렷한 변화 (신도시 조성 중 미등록 정상 건물일 가능성도 있음)"
            )
        else:
            score += 0.15
            evidence.append(
                f"건축물대장 미매칭이나 변화 강도가 낮음(ratio={change_ratio:.2f}, "
                f"확신도={confidence:.2f})"
            )

    # --- 신호 2: 대장 건축면적 대비 실제 footprint 초과 (무단증축 패턴) ---
    excess_ratio = None
    if footprint and arch_area and arch_area > 0:
        excess_ratio = round(footprint / arch_area, 3)
        if excess_ratio >= tolerance_ratio:
            signals += 1
            score += 0.40
            evidence.append(
                f"실제 footprint {footprint:.0f}m²가 대장 건축면적 {arch_area:.0f}m²의 "
                f"{excess_ratio:.2f}배 - 무단증축 의심 "
                "(단, footprint 정의 차이·대장 1:다 대응 오차 가능)"
            )

    # --- 신호 3: 변화 방향 불일치 (자동 판정 신뢰도 저하 신호) ---
    if row.get("directional_consistency_flag") == 0:
        score += 0.10
        evidence.append("밝기 변화 방향이 분류 결과와 불일치 - 자동판정 신뢰도 낮음, 육안·현장 확인 필요")

    # --- 설명됨: 대장 근거로 시점까지 확인된 건 ---
    if matched and row.get("useAprDay") and excess_ratio is not None and excess_ratio < tolerance_ratio:
        return {
            "illegal_grade": GRADE_NONE,
            "illegal_score": 0.0,
            "area_excess_ratio": excess_ratio,
            "illegal_evidence": (
                f"건축물대장 매칭(사용승인일={row.get('useAprDay')})이고 면적 초과 없음"
                f"(footprint/건축면적={excess_ratio:.2f}) - 행정정보로 설명됨"
            ),
        }

    score = round(min(score, 1.0), 3)
    if signals >= 2:
        grade = GRADE_A
    elif signals == 1:
        grade = GRADE_B
    elif score > 0:
        grade = GRADE_C
    else:
        grade = GRADE_NONE
        evidence.append("추가 의심 신호 없음")

    return {
        "illegal_grade": grade,
        "illegal_score": score,
        "area_excess_ratio": excess_ratio,
        "illegal_evidence": " | ".join(evidence)[:500],
    }


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def apply_to_featureclass(
    fc: str,
    tolerance_ratio: float = 1.3,
    strong_change_ratio: float = 0.5,
    strong_confidence: float = 0.6,
) -> dict:
    """결과 Feature Class에 불법·무허가 의심 등급 필드를 채운다.

    Returns:
        illegal_grade별 건수.
    """
    for name, ftype, length in SCREEN_FIELDS:
        add_field_if_missing(fc, name, ftype, field_length=length)

    names = {f.name for f in arcpy.ListFields(fc)}
    src = [f for f in ("has_register_match", "archArea", "building_area_m2", "change_ratio",
                       "max_change_score", "change_type", "useAprDay",
                       "directional_consistency_flag") if f in names]
    dst = [n for n, _, _ in SCREEN_FIELDS]

    counts: dict[str, int] = {}
    with arcpy.da.UpdateCursor(fc, src + dst) as cur:
        for row in cur:
            record = dict(zip(src, row[: len(src)]))
            verdict = evaluate_illegal_signals(
                record, tolerance_ratio, strong_change_ratio, strong_confidence
            )
            for i, key in enumerate(dst):
                row[len(src) + i] = verdict[key]
            counts[verdict["illegal_grade"]] = counts.get(verdict["illegal_grade"], 0) + 1
            cur.updateRow(row)

    logger.info("[ILLEGAL] 의심 등급 분포: %s", counts)
    return counts
