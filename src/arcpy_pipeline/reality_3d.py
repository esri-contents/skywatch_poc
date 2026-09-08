"""REQ11 - 후보지역을 대상으로 ArcGIS Reality 기반 3D 정밀검토.

LH 설문에서 "3D Reality의 의사결정 지원 가치"는 4.08/5로, 11개 요구
기능 중 가장 낮은 우선순위이지만 그래도 사실상 전 항목이 4점을 넘는
높은 관심사다.

**정직하게 짚어야 할 한계**: ArcGIS Reality는 다중시점 스테레오/드론
영상에서 포인트클라우드·메시·정사영상을 재구성하는 사진측량 엔진이다.
Sentinel-2(10m, 단일 시점 촬영)로는 애초에 입력 자체가 성립하지 않는다
- 스테레오 페어가 없고 해상도도 3D 재구성 단위(수 cm~수십 cm)에
턱없이 못 미친다. 그래서 이 모듈은 "3D 재구성을 실행"하지 않는다.

대신 이 모듈이 실제로 하는 일 - **3D 정밀검토가 필요한 후보 선정과
그 검토를 준비하는 것**:

1. 변화 후보 중 3D 검토가 특히 유용한 곳을 골라낸다(신축 대형 구조물,
   방향 불일치로 자동판정이 불확실한 곳, 층수 변화가 의심되는 곳 등).
2. 이미 보유한 정보(건축물대장의 층수)로 **임시 3D 블록모델**을 만든다
   - 실제 Reality 메시가 오기 전까지 협의·보고에 쓸 수 있는 대체물이다.
3. 실제 Reality 처리에 필요한 촬영 스펙과 절차를 담은 계획 문서를
   만든다 - 드론/고해상 스테레오 영상이 확보된 후 그대로 실행할 수 있게.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import arcpy

from .env import add_field_if_missing, delete_if_exists

logger = logging.getLogger("arcpy_pipeline.reality_3d")

REALITY_FIELDS = [
    ("reality_review_flag", "SHORT", None),
    ("reality_review_reason", "TEXT", 300),
    ("reality_priority", "SHORT", None),
]


def select_reality_review_candidates(
    results_fc: str,
    out_fc: str,
    buffer_m: float = 20.0,
) -> str:
    """3D 정밀검토가 특히 유용한 후보를 골라 AOI를 만든다.

    선정 기준 (근거 있는 것만 - 막연히 "크니까"로 고르지 않는다):
    - 대장 미매칭 + HIGH 등급: 행정정보로 확정이 안 되니 형상 확인이
      의사결정에 실질적으로 기여한다.
    - directional_consistency_flag=False: 자동판정 신뢰도가 낮다고
      이미 표시된 곳 - 3D로 실제 형상을 보면 밝기 신호보다 명확할 수 있다.
    - EXPANSION_OR_RECONSTRUCTION + 대장 층수 정보 있음: 실제 증축이
      층수 변화를 동반했는지 3D로 검증 가능하다.
    - 보상기준일 POST_BASELINE_UNVERIFIED: 보상 실무에서 형상 증거가
      필요한 사안.

    Args:
        results_fc: 최종 변화 후보 (분류/점수화 완료).
        out_fc: 선정된 후보만 담은 결과 (버퍼 적용된 검토 영역).
        buffer_m: 각 후보 주변 촬영/검토 범위 버퍼.

    Returns:
        out_fc 경로.
    """
    names = {f.name for f in arcpy.ListFields(results_fc)}

    clauses = []
    if {"has_register_match", "inspection_priority"} <= names:
        clauses.append("(has_register_match = 0 AND inspection_priority = 'HIGH')")
    if "directional_consistency_flag" in names:
        clauses.append("(directional_consistency_flag = 0)")
    if {"change_type", "grndFlrCnt"} <= names:
        clauses.append("(change_type = 'EXPANSION_OR_RECONSTRUCTION' AND grndFlrCnt IS NOT NULL)")
    if "compensation_status" in names:
        clauses.append("(compensation_status = 'POST_BASELINE_UNVERIFIED')")

    if not clauses:
        logger.warning("[REALITY] 선정 기준 필드가 하나도 없습니다 - 분류/점수화를 먼저 실행하세요")
        where = "1 = 0"
    else:
        where = " OR ".join(clauses)

    lyr = "reality_candidates_lyr"
    arcpy.management.MakeFeatureLayer(results_fc, lyr, where)
    try:
        count = int(arcpy.management.GetCount(lyr)[0])
        delete_if_exists(out_fc)
        if count == 0:
            logger.warning("[REALITY] 선정 기준에 맞는 후보가 없습니다")
            arcpy.management.CopyFeatures(lyr, out_fc)
        else:
            buffered = arcpy.analysis.Buffer(lyr, "in_memory\\_reality_buf", f"{buffer_m} Meters")
            arcpy.analysis.PairwiseDissolve(buffered, out_fc, multi_part="SINGLE_PART")
            arcpy.management.Delete("in_memory\\_reality_buf")
    finally:
        arcpy.management.Delete(lyr)

    for name, ftype, length in REALITY_FIELDS[:2]:
        add_field_if_missing(out_fc, name, ftype, field_length=length)

    n = int(arcpy.management.GetCount(out_fc)[0])
    logger.info("[REALITY] 3D 정밀검토 대상 %d개 구역 선정 (기준: %d개 조건)", n, len(clauses))
    return out_fc


def build_interim_3d_blocks(
    buildings_fc: str,
    out_fc: str,
    floor_height_m: float = 3.0,
    default_floors: int = 1,
) -> str:
    """건축물대장 층수(grndFlrCnt)로 임시 3D 블록모델(Extrude)을 만든다.

    실제 Reality 메시가 오기 전까지 협의·PPT·Web Scene에 쓸 수 있는
    대체물이다. **주의**: 이것은 실측 3D가 아니라 "층수 × 층고"로 계산한
    단순 블록이며, 지붕 형태·옥탑·발코니 등은 반영되지 않는다. 층수가
    없는 건물(대장 미매칭)은 default_floors로 채우되 그 사실을 필드에
    남긴다.

    Args:
        buildings_fc: 건물 footprint (건축물대장 조인 완료본).
        out_fc: 저장할 3D Feature Class.
        floor_height_m: 층당 높이 가정치.
        default_floors: 층수 정보가 없을 때 쓸 기본값.

    Returns:
        out_fc 경로 (Z값을 가진 MultiPatch/Polygon).
    """
    names = {f.name for f in arcpy.ListFields(buildings_fc)}
    has_floors = "grndFlrCnt" in names

    scratch = arcpy.CreateScratchName("h", "", "FeatureClass", arcpy.env.scratchGDB)
    arcpy.management.CopyFeatures(buildings_fc, scratch)
    add_field_if_missing(scratch, "est_height_m", "DOUBLE")
    add_field_if_missing(scratch, "height_is_estimated", "SHORT")

    fields = ["est_height_m", "height_is_estimated"] + (["grndFlrCnt"] if has_floors else [])
    with arcpy.da.UpdateCursor(scratch, fields) as cur:
        for row in cur:
            floors = row[2] if has_floors and row[2] else None
            row[0] = (floors or default_floors) * floor_height_m
            row[1] = 0 if floors else 1
            cur.updateRow(row)

    delete_if_exists(out_fc)
    arcpy.ddd.FeatureTo3DByAttribute(scratch, out_fc, "est_height_m")
    delete_if_exists(scratch)

    n = int(arcpy.management.GetCount(out_fc)[0])
    n_estimated = sum(
        1 for (v,) in arcpy.da.SearchCursor(out_fc, ["height_is_estimated"]) if v == 1
    )
    logger.info(
        "[REALITY] 임시 3D 블록 %d개 생성 (그중 %d개는 층수 정보 없어 기본값 추정)",
        n, n_estimated,
    )
    return out_fc


def build_capture_plan(
    out_path: str | Path,
    review_aoi_fc: str,
    target_gsd_cm: float = 3.0,
    overlap_pct: int = 75,
) -> Path:
    """ArcGIS Reality 처리를 위한 촬영계획 문서를 만든다.

    실제 드론/고해상 영상이 확보되면 이 문서의 스펙대로 촬영해 바로
    ArcGIS Reality(또는 Drone2Map)에 투입할 수 있게 하는 것이 목적이다.

    Args:
        out_path: 저장할 markdown 경로.
        review_aoi_fc: select_reality_review_candidates() 결과.
        target_gsd_cm: 목표 지상표본거리(cm). 3D 메시 품질에 직접 영향.
        overlap_pct: 종중복도/횡중복도(%) - 스테레오 매칭에 필요한 최소 중복.

    Returns:
        저장된 파일 경로.
    """
    n_areas = int(arcpy.management.GetCount(review_aoi_fc)[0]) if arcpy.Exists(review_aoi_fc) else 0
    total_area_m2 = sum(
        r[0] for r in arcpy.da.SearchCursor(review_aoi_fc, ["SHAPE@AREA"])
    ) if n_areas else 0.0

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ArcGIS Reality 3D 정밀검토 촬영계획",
        "",
        f"- **작성일**: {date.today().isoformat()}",
        f"- **선정 구역 수**: {n_areas}개",
        f"- **총 면적**: {total_area_m2 / 1e4:.2f} ha",
        "",
        "## 1. 왜 지금은 3D 처리를 실행하지 않는가",
        "",
        "ArcGIS Reality는 다중시점 스테레오 영상에서 포인트클라우드/메시를",
        "재구성하는 사진측량 엔진이다. 이 PoC의 1차 소스인 Sentinel-2는",
        "단일시점·10m 해상도라 애초에 3D 재구성의 입력 요건(스테레오 페어,",
        "cm~dm급 해상도)을 충족하지 못한다. 아래 스펙으로 별도 촬영이",
        "확보된 뒤에 이 절차를 실행한다.",
        "",
        "## 2. 촬영 스펙 요구사항",
        "",
        "| 항목 | 요구값 | 근거 |",
        "|---|---|---|",
        f"| 목표 GSD | {target_gsd_cm:.1f} cm 이하 | 건물 외벽/옥상 구조 식별 가능 수준 |",
        f"| 종중복도(Forward overlap) | {overlap_pct}% 이상 | 스테레오 매칭에 필요한 최소 중복 |",
        f"| 횡중복도(Side overlap) | {max(overlap_pct - 15, 50)}% 이상 | 〃 |",
        "| 촬영 방식 | 드론 사진측량 또는 위성 스테레오 트리플렛 | 다중시점 필수 |",
        "| 카메라 각도 | 수직 + 경사(oblique) 조합 권장 | 건물 측면 재구성에 경사 영상 필요 |",
        "| 기준점(GCP) | 지상기준점 확보 권장 | 절대 위치정확도 확보 |",
        "",
        "## 3. 대상 구역 선정 근거",
        "",
        "다음 조건 중 하나 이상에 해당하는 변화 후보 주변을 검토 대상으로 잡았다",
        "(`reality_3d.select_reality_review_candidates` 참고):",
        "",
        "- 건축물대장 미매칭 + HIGH 등급 (행정정보로 확정 불가, 형상 확인이 유효)",
        "- 밝기 변화 방향 불일치 플래그 (자동판정 신뢰도 낮음)",
        "- 증축/개축 판정 + 층수 정보 보유 (실제 층수 변화 3D 검증 가능)",
        "- 보상기준일 이후 미확인 변화 (형상 증거 필요)",
        "",
        "## 4. 확보 후 처리 절차",
        "",
        "1. 드론/고해상 영상을 ArcGIS Reality(또는 Drone2Map for ArcGIS)에 투입",
        "2. 공중삼각측량(Aerial Triangulation) → 포인트클라우드 → 메시 생성",
        "3. `management.CreateSceneLayerPackage`로 Scene Layer Package(.slpk) 생성",
        "4. ArcGIS Online에 Scene Layer로 발행, Web Scene에서 임시 3D 블록모델",
        "   (`reality_3d.build_interim_3d_blocks` 산출물)과 나란히 비교",
        "5. 실측 3D와 대장 기반 추정치의 차이를 근거로 최종 판단",
        "",
        "## 5. 임시 대체물 (실측 3D 확보 전까지)",
        "",
        "건축물대장 층수(grndFlrCnt) × 층고 3m 가정으로 만든 블록모델을",
        "`reality_3d.build_interim_3d_blocks()`로 생성해 두었다. 실제 3D가",
        "아니므로 형태·옥탑·발코니는 반영되지 않으며, 층수 정보가 없는",
        "건물은 기본 1층으로 추정한 뒤 그 사실을 `height_is_estimated`",
        "필드에 남긴다 - 보고서에 쓸 때 반드시 이 구분을 함께 표기한다.",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[REALITY] 촬영계획 문서 생성: %s", out_path)
    return out_path
