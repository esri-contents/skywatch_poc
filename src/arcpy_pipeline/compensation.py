"""REQ03 - 보상 기준일 전후의 토지·건물 변화 확인.

LH 요구 기능 3순위. 공익사업 보상 실무에서 가장 민감한 문제는 **보상
기준일(통상 주민공람공고일) 이후에 보상금을 노리고 급조된 건축물**이다.
토지보상법 체계상 기준일 이후 신축·증축분은 보상 대상에서 제외되거나
제한되지만, 넓은 지구에서 그것을 사람이 전수 확인하기는 어렵다.

이 모듈이 하는 일:

1. 각 변화 후보가 기준일 **이전/이후** 중 언제 발생했는지 판정한다.
   근거는 두 갈래이며 서로 독립적이라 교차검증이 된다.
   - **행정 근거**: 건축물대장의 사용승인일/착공일/허가일
   - **영상 근거**: 여러 시기(T1/T2/T3)의 변화탐지 결과로 변화 발생
     구간을 좁힌다(bracketing). 대장에 없는 무허가 건축물은 행정 근거가
     아예 없으므로, 영상만이 유일한 시점 증거가 된다.
2. 둘을 합쳐 `compensation_status`와 위험도를 매긴다.

**중요 - 이 모듈은 보상 여부를 결정하지 않는다.** 보상 대상 판정은
법령·감정평가·이의절차를 거치는 행정처분이다. 여기서 하는 것은
"기준일 이후 변화로 보이는데 행정 근거가 확인되지 않는 건"을 우선
확인 대상으로 올리는 것뿐이다.

**기준일은 반드시 사업부서가 확정해 config에 넣어야 한다.** 코드에
특정 날짜를 사실로 박아두지 않는다 - 잘못된 기준일은 결과 전체를
무효로 만들기 때문이다(`config.yaml`의 `compensation.baseline_date`).
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import arcpy

from .env import add_field_if_missing

logger = logging.getLogger("arcpy_pipeline.compensation")

# compensation_status 값
PRE_BASELINE = "PRE_BASELINE"                    # 기준일 이전 - 통상 보상 대상
POST_BASELINE_PERMITTED = "POST_BASELINE_PERMITTED"      # 기준일 이후지만 허가 근거 있음
POST_BASELINE_UNVERIFIED = "POST_BASELINE_UNVERIFIED"    # 기준일 이후 + 행정근거 없음 (최우선)
STRADDLES_BASELINE = "STRADDLES_BASELINE"        # 영상 구간이 기준일을 걸쳐 시점 특정 불가
UNKNOWN = "UNKNOWN"                              # 판단 근거 자체가 없음

STATUS_RISK = {
    POST_BASELINE_UNVERIFIED: 1.0,
    STRADDLES_BASELINE: 0.6,
    POST_BASELINE_PERMITTED: 0.3,
    UNKNOWN: 0.5,
    PRE_BASELINE: 0.0,
}

COMPENSATION_FIELDS = [
    ("compensation_status", "TEXT", 32),
    ("compensation_risk", "DOUBLE", None),
    ("compensation_evidence", "TEXT", 400),
    ("change_epoch", "TEXT", 40),
]


def parse_date(value) -> date | None:
    """'YYYYMMDD' / 'YYYY-MM-DD' / date 를 date로."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def evaluate_compensation(
    row: dict,
    baseline_date: date,
    t1_date: date,
    t2_date: date,
) -> dict:
    """변화 후보 한 건의 보상 기준일 대비 상태를 판정한다 (순수 함수).

    행정 근거를 영상 근거보다 우선한다. 대장에 사용승인일이 있으면 그것이
    그 건물의 실제 완공 시점에 대한 가장 강한 증거이고, 영상 구간(T1~T2)은
    "그 사이 어딘가"라는 넓은 범위만 알려주기 때문이다.

    Args:
        row: has_register_match / useAprDay / stcnsDay / pmsDay /
            change_type / change_ratio 를 담은 dict.
        baseline_date: 보상 기준일 (공람공고일 등).
        t1_date: 비교 시작 영상 촬영일.
        t2_date: 비교 종료 영상 촬영일.

    Returns:
        {"compensation_status", "compensation_risk", "compensation_evidence",
         "change_epoch"}
    """
    epoch = f"{t1_date.isoformat()}~{t2_date.isoformat()}"
    matched = bool(row.get("has_register_match"))
    use_apr = parse_date(row.get("useAprDay"))
    stcns = parse_date(row.get("stcnsDay"))
    pms = parse_date(row.get("pmsDay"))

    # --- 1) 행정 근거가 있는 경우 ---
    # 기존 건물의 오래된 사용승인일은 최근 증축·개축의 발생시점을 설명하지
    # 못한다. 신축 후보에만 건물 전체의 사용승인일을 강한 시점 근거로 쓰고,
    # 증축·개축은 별도 대수선/증축 허가자료가 없으면 영상 구간으로 판정한다.
    if matched and use_apr and row.get("change_type") == "NEW_BUILDING":
        if use_apr < baseline_date:
            return _result(
                PRE_BASELINE,
                f"사용승인일={use_apr.isoformat()} < 기준일={baseline_date.isoformat()} "
                "- 기준일 이전 완공(건축물대장 근거)",
                epoch,
            )
        # 기준일 이후 완공 - 허가/착공이 기준일 이전이면 정상 진행분일 수 있다
        earlier = min([d for d in (pms, stcns) if d], default=None)
        if earlier and earlier < baseline_date:
            return _result(
                POST_BASELINE_PERMITTED,
                f"사용승인일={use_apr.isoformat()}은 기준일 이후이나 "
                f"{'허가일' if earlier == pms else '착공일'}={earlier.isoformat()}이 기준일 이전 "
                "- 기준일 전 착수분으로 보이며 보상 실무 확인 필요",
                epoch,
            )
        return _result(
            POST_BASELINE_UNVERIFIED,
            f"사용승인일={use_apr.isoformat()}이 기준일={baseline_date.isoformat()} 이후이고 "
            "기준일 이전 허가·착공 근거가 확인되지 않음",
            epoch,
        )

    # --- 2) 행정 근거가 없는 경우: 영상 구간으로만 판단 ---
    if not matched:
        if t1_date >= baseline_date:
            # 비교 구간 전체가 기준일 이후 → 이 변화는 확실히 기준일 이후
            return _result(
                POST_BASELINE_UNVERIFIED,
                f"건축물대장 미매칭이며 영상 비교구간({epoch}) 전체가 기준일 이후 "
                "- 기준일 이후 발생한 무허가 변화 가능성, 최우선 확인 대상",
                epoch,
            )
        if t2_date <= baseline_date:
            return _result(
                PRE_BASELINE,
                f"건축물대장 미매칭이나 영상 비교구간({epoch})이 전부 기준일 이전 "
                "- 기준일 이후 변화 아님",
                epoch,
            )
        return _result(
            STRADDLES_BASELINE,
            f"건축물대장 미매칭 + 영상 비교구간({epoch})이 기준일"
            f"({baseline_date.isoformat()})을 걸쳐 시점 특정 불가 "
            "- 기준일 전후로 나뉜 영상으로 재분석 권장",
            epoch,
        )

    # 대장은 매칭됐지만 사용승인일 자체가 없는 경우 - 최근 변화 시점을
    # 추정할 행정 근거가 전혀 없으므로, 영상 구간과 무관하게 판단 보류.
    if not use_apr:
        return _result(
            UNKNOWN,
            "건축물대장은 매칭됐으나 사용승인일이 없어 시점 판단 불가",
            epoch,
        )

    if t1_date >= baseline_date:
        return _result(
            UNKNOWN,
            "기존 건물 대장은 매칭됐으나 최근 증축·개축의 행정 시점 근거가 없음; "
            f"영상 구간({epoch})은 기준일 이후이므로 별도 인허가 이력 확인 필요",
            epoch,
        )
    if t2_date <= baseline_date:
        return _result(PRE_BASELINE, f"영상 비교구간({epoch})이 기준일 이전", epoch)
    return _result(
        STRADDLES_BASELINE,
        f"기존 건물의 오래된 사용승인일로 최근 변화를 소급 판정하지 않음; 영상 구간({epoch})이 기준일을 걸침",
        epoch,
    )


def _result(status: str, evidence: str, epoch: str) -> dict:
    return {
        "compensation_status": status,
        "compensation_risk": STATUS_RISK[status],
        "compensation_evidence": evidence,
        "change_epoch": epoch,
    }


def refine_with_epochs(
    base: dict,
    epoch_hits: list[tuple[date, date, bool]],
    baseline_date: date,
) -> dict:
    """여러 시기 변화탐지 결과로 변화 발생 구간을 좁힌다 (bracketing).

    STRADDLES_BASELINE(시점 특정 불가)은 "영상이 두 장뿐"이라서 생기는
    한계다. 같은 지점에 대해 여러 구간의 변화탐지 결과가 있으면, 변화가
    잡힌 가장 이른 구간이 실제 발생 시점을 훨씬 좁게 알려준다.

    Args:
        base: evaluate_compensation() 결과.
        epoch_hits: [(구간시작, 구간종료, 이 구간에서 변화가 잡혔는지), ...].
        baseline_date: 보상 기준일.

    Returns:
        갱신된 판정 dict. 좁히지 못하면 base를 그대로 돌려준다.
    """
    hits = sorted([(s, e) for s, e, hit in epoch_hits if hit])
    if not hits:
        return base

    first_start, first_end = hits[0]
    epoch_label = f"{first_start.isoformat()}~{first_end.isoformat()}"

    if first_start >= baseline_date:
        return {
            "compensation_status": POST_BASELINE_UNVERIFIED
            if base["compensation_status"] in (STRADDLES_BASELINE, UNKNOWN)
            else base["compensation_status"],
            "compensation_risk": max(
                base["compensation_risk"], STATUS_RISK[POST_BASELINE_UNVERIFIED]
            ),
            "compensation_evidence": (
                base["compensation_evidence"]
                + f" | 다시기 분석: 변화가 처음 관측된 구간({epoch_label})이 기준일 이후"
            ),
            "change_epoch": epoch_label,
        }
    if first_end <= baseline_date:
        return {
            "compensation_status": PRE_BASELINE,
            "compensation_risk": STATUS_RISK[PRE_BASELINE],
            "compensation_evidence": (
                base["compensation_evidence"]
                + f" | 다시기 분석: 변화가 이미 기준일 이전 구간({epoch_label})에 관측됨"
            ),
            "change_epoch": epoch_label,
        }
    return {**base, "change_epoch": epoch_label}


def apply_to_featureclass(
    fc: str,
    baseline_date: date,
    t1_date: date,
    t2_date: date,
) -> dict:
    """결과 Feature Class에 보상 기준일 판정 필드를 채운다.

    Returns:
        compensation_status별 건수.
    """
    for name, ftype, length in COMPENSATION_FIELDS:
        add_field_if_missing(fc, name, ftype, field_length=length)

    names = {f.name for f in arcpy.ListFields(fc)}
    src = [f for f in ("has_register_match", "useAprDay", "stcnsDay", "pmsDay",
                       "change_type", "change_ratio") if f in names]
    dst = [n for n, _, _ in COMPENSATION_FIELDS]

    counts: dict[str, int] = {}
    with arcpy.da.UpdateCursor(fc, src + dst) as cur:
        for row in cur:
            record = dict(zip(src, row[: len(src)]))
            verdict = evaluate_compensation(record, baseline_date, t1_date, t2_date)
            for i, key in enumerate(dst):
                row[len(src) + i] = verdict[key]
            counts[verdict["compensation_status"]] = counts.get(verdict["compensation_status"], 0) + 1
            cur.updateRow(row)

    logger.info(
        "[COMPENSATION] 기준일=%s 판정 완료: %s",
        baseline_date.isoformat(), counts,
    )
    return counts
