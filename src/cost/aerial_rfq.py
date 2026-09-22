"""항공촬영(aerial photography) 신규촬영 요구사항(RFQ) 문서 자동 생성.

src/arcpy_pipeline/imagery_tasking.py::build_tasking_request()가 상용
위성(SkyWatch 등) tasking 요구사항 문서를 만드는 것과 동일한 목적을,
국내 항공측량업체 대상으로 만든다. 별도 모듈로 둔 이유는 두 문서의 확인
필요 상업조건 항목(예: 항공촬영은 출동비/촬영고도, 위성은 off-nadir/
아카이브 vs tasking)이 서로 달라 하나로 합치면 오히려 항목이 뒤섞이기
때문이다.

이 PoC가 실측으로 확인한 수치(Sentinel-2 10m 해상도의 픽셀 수 부족,
SkyWatch 실제 관측 단가)를 근거로 요구 사양을 도출한다 - "고해상도가
필요하다"는 주장보다 "254m^2 필지가 10m 해상도에서 3x3픽셀이라 SSIM
최소 크기(7x7)조차 못 채운다"는 실측치가 협상에서 훨씬 강하다(#32 원칙:
가상의 견적/할인율/최소주문을 만들지 않는다 - 이 문서는 사양/근거만
자동 생성하고 가격은 채우지 않는다).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger("cost.aerial_rfq")


def build_aerial_rfq(
    out_path: str | Path,
    aoi_name: str,
    aoi_area_m2: float,
    measured: dict,
    target_dates: list[str],
    skywatch_reference: list[dict] | None = None,
) -> Path:
    """항공촬영 신규촬영 요구사항 문서를 자동 생성한다.

    Args:
        out_path: 저장할 markdown 경로.
        aoi_name: 대상 필지/지구명.
        aoi_area_m2: 공식 AOI 면적(m^2). 실제 항공측량 발주 단위(보통 km^2
            블록)와 다를 수 있음을 문서에 명시한다.
        measured: {"resolution_m", "grid_shape", "pixel_limit_note",
                   "detected_nearby_change_note"} 등 이 PoC가 실측한 결과.
        target_dates: 요청 촬영 시점 목록 (확정 아니면 그 사실을 그대로 담는다).
        skywatch_reference: [{"product","resolution_m","unit_price","currency","band"}...]
            비교 근거로 삼을 SkyWatch 실측 단가 (있으면 표에 포함, 없으면 생략).

    Returns:
        저장된 파일 경로.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resolution_m = measured.get("resolution_m")
    grid_shape = measured.get("grid_shape")
    pixel_limit_note = measured.get("pixel_limit_note")
    nearby_note = measured.get("detected_nearby_change_note")

    lines = [
        "# 항공촬영(신규촬영) 요구사항",
        "",
        f"- **작성일**: {date.today().isoformat()}",
        f"- **대상**: {aoi_name} ({aoi_area_m2:,.2f} m² = {aoi_area_m2 / 1e6:.6f} km²)",
        "- **작성 근거**: 본 PoC의 Sentinel-2 Baseline 실측 결과 + SkyWatch Content Store 실제 조회 결과",
        "",
        "> 이 문서는 요구 사양/실측 근거만 자동 생성한다. 단가·할인율·최소주문 등",
        "> 상업 조건은 실제 업체 확인 없이는 어떤 값도 채우지 않는다.",
        "",
        "## 1. 왜 항공촬영(고해상도)이 필요한가 (실측 근거)",
        "",
    ]

    if resolution_m and grid_shape:
        lines.append(
            f"- 무료 아카이브(Sentinel-2, GSD {resolution_m:g}m) 기준, 대상 AOI가 격자로 "
            f"**{grid_shape}**밖에 나오지 않음을 실측 확인했다."
        )
    if pixel_limit_note:
        lines.append(f"- {pixel_limit_note}")
    lines += [
        "- 이는 개별 필지 단위 변화탐지가 10m 해상도에서 원천적으로 불가능한 수준이며,"
        " 후처리 알고리즘(SSIM 등)이 요구하는 최소 픽셀 수조차 채우지 못한다.",
    ]
    if nearby_note:
        lines.append(f"- {nearby_note}")
    lines += [
        "- 결론: 개별 필지 단위 지장물 확인에는 SkyWatch 상용 위성(15~50cm) 또는"
        " 국내 항공측량(통상 5~25cm)이 필요하다. 이 문서는 그중 항공측량 옵션의"
        " 견적 요청용이다.",
        "",
        "## 2. 요구 사양",
        "",
        "| 항목 | 요구 수준 | 근거 |",
        "|---|---|---|",
        "| 공간해상도(GSD) | **10cm 이하 권장, 25cm 이하 필수** | SkyWatch 최고 해상도(15cm) 대비 경쟁력 확보 + 소규모 필지(수백~수천 m²) 분리 필요 |",
        "| 정사보정 | 정사보정 완료본(Orthophoto) | 필지 경계와의 정합 오차 최소화 |",
        "| 밴드 | RGB + NIR(4밴드) 필수 | 이 프로젝트 파이프라인(band_schema=RGBNIR)이 그대로 지원 |",
        "| 좌표계 | EPSG:5186 (Korea 2000 / Central Belt 2010) | 이 PoC의 분석 좌표계와 동일 - 재투영 없이 바로 사용 가능 |",
        "| 구름/그림자 | 5% 미만 권장 | Sentinel-2 실측 경험상 구름이 있으면 정확히 대상 필지 픽셀이 가려지는 사례를 실제로 확인(SCL=9) |",
        "| 계절 | T1/T2 동일 계절 권장 | 식생·광량 차이로 인한 오탐 억제 (analysis_limitations 참고) |",
        "| 포맷 | GeoTIFF (밴드 description 포함 권장) | band_schema.determine_band_order()가 자동 인식 |",
        "",
        "## 3. 요청 촬영 시점",
        "",
    ]
    for d in target_dates:
        lines.append(f"- {d}")
    lines += [
        "",
        "> 서해선 사업 착공/준공의 정확한 시점이 아직 확정되지 않았다"
        "(config/projects/kr_rail_nohari.yaml의 reference_period_note 참고)."
        " 실제 촬영 시점은 사업부서가 착공/준공 기준일을 확정한 뒤 재조정해야 한다.",
        "",
        "## 4. 확인 필요 상업 조건",
        "",
        f"- km²당 단가 (본 AOI {aoi_area_m2 / 1e6:.6f}km²는 너무 작아 실제로는"
        " 더 넓은 촬영 블록 단위로 발주될 가능성이 높음 - 최소 촬영 블록 크기 확인 필요)",
        "- 출동비(mobilization cost) - 촬영 1회당인지, 계약당인지",
        "- 최소 주문 면적/금액",
        "- 후처리(정사보정/모자이크) 비용 별도 여부",
        "- 촬영 준비 기간(항공기 배정, 관제 승인 등)",
        "- 촬영 후 성과품 인도까지 소요 기간(lead time)",
        "- 기상(구름) 사유 재촬영 정책 및 비용",
        "- 연간 계약(복수 시기 촬영) 시 할인 여부",
        "",
        "## 5. SkyWatch 대안과의 비교 참고 자료",
        "",
    ]
    if skywatch_reference:
        lines.append("2026-09-22 SkyWatch Content Store 실제 조회로 확인한 참고 단가:")
        lines.append("")
        lines.append("| 상품 | 해상도 | 밴드 | 단가 |")
        lines.append("|---|---|---|---|")
        for r in skywatch_reference:
            lines.append(
                f"| {r['product']} | {r['resolution_m']}m | {r['band']} | "
                f"{r['unit_price']} {r['currency']}/km² |"
            )
        lines.append("")
        lines.append(
            "> SkyWatch는 자체 'Credits' 단위로 과금하며, KRW 환산 환율은 계약"
            " 조건에 따라 별도 확인이 필요하다(config의 cost_comparison.exchange_rate.credits_to_krw)."
        )
    else:
        lines.append("(SkyWatch 참고 단가 없음 - measured 인자에 skywatch_reference를 전달하지 않음)")

    lines += [
        "",
        "## 6. 도입 후 비교 검증 계획",
        "",
        "항공측량 견적/성과품 확보 후 동일 AOI·동일 파이프라인(src/imagery_change_pipeline.py)에"
        " 투입해 다음을 비교한다.",
        "",
        "| 지표 | Sentinel-2(현재) | SkyWatch | 항공측량 |",
        "|---|---|---|---|",
        f"| 공간해상도 | {resolution_m:g}m | 15~50cm | 요청 사양 |"
        if resolution_m else "| 공간해상도 | 10m | 15~50cm | 요청 사양 |",
        "| 대상 필지 분석 가능 여부 | 픽셀 부족으로 사실상 불가 | 가능(추정) | 재측정 |",
        "| km²당 비용 | 0 (무료) | 실측(Credits, 환율 확인 필요) | 재측정 |",
        "| 촬영 준비~인도 기간 | 아카이브 즉시 | 재측정 | 재측정 |",
        "",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[COST] 항공촬영 RFQ 문서 생성: %s", out_path)
    return out_path
