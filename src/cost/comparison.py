"""SkyWatch 영상 vs 항공사진 촬영 비용/특성 비교.

이 모듈은 실제 견적/단가가 없어도 정상 동작해야 한다(#22 "empty price 상태를
정상 처리"). 확인되지 않은 숫자는 절대 추측해서 채우지 않는다(#32 원칙) -
config 값이 null이면 계산을 건너뛰고 "확인 필요"로 표시한다.

비용 산식(프로젝트 요구사항 #29에서 그대로 가져옴, 새로 설계하지 않음):
  SkyWatch = AOI(km^2) * unit_price_per_km2 * acquisition_count + processing_cost
             (minimum_order_cost가 있으면 촬영 1회당 최소 주문 금액으로 반영)
  항공촬영 = mobilization_cost + AOI(km^2) * unit_price_per_km2 * acquisition_count
             + processing_cost
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger("cost.comparison")

NOT_AVAILABLE = "N/A"
NEEDS_CONFIRMATION = "확인 필요"

DEFAULT_SCENARIOS_KM2 = [10, 50, 100, 500]


def load_cost_config(project_config_path: str | Path) -> dict:
    """프로젝트 YAML에서 cost_comparison 섹션을 읽는다."""
    with open(project_config_path, encoding="utf-8") as f:
        proj_cfg = yaml.safe_load(f)
    cost_cfg = proj_cfg.get("cost_comparison")
    if cost_cfg is None:
        raise ValueError(
            f"[COST] {project_config_path}에 cost_comparison 섹션이 없습니다."
        )
    return proj_cfg


def compute_skywatch_cost(
    area_km2: float | None,
    acquisition_count: int,
    cfg: dict,
) -> float | None:
    """SkyWatch 영상 비용. unit_price_per_km2가 없으면 None(계산 불가)."""
    unit_price = cfg.get("unit_price_per_km2")
    if area_km2 is None or unit_price is None:
        return None
    per_acquisition = area_km2 * unit_price
    minimum_order = cfg.get("minimum_order_cost")
    if minimum_order is not None:
        per_acquisition = max(per_acquisition, minimum_order)
    total = per_acquisition * acquisition_count
    processing_cost = cfg.get("processing_cost")
    if processing_cost is not None:
        total += processing_cost
    return total


def compute_aerial_cost(
    area_km2: float | None,
    acquisition_count: int,
    cfg: dict,
) -> float | None:
    """항공촬영 비용. unit_price_per_km2가 없으면 None(계산 불가)."""
    unit_price = cfg.get("unit_price_per_km2")
    if area_km2 is None or unit_price is None:
        return None
    total = area_km2 * unit_price * acquisition_count
    mobilization_cost = cfg.get("mobilization_cost")
    if mobilization_cost is not None:
        total += mobilization_cost
    processing_cost = cfg.get("processing_cost")
    if processing_cost is not None:
        total += processing_cost
    return total


def convert_currency(amount: float | None, from_currency: str, to_currency: str, exchange_rate_cfg: dict) -> float | None:
    """환율이 config에 명시된 경우에만 환산한다. 실시간 조회하지 않는다(#28 원칙).

    환율 키는 "{from}_to_{to}" 형태(소문자)로 config에서 찾는다(예: usd_to_krw).
    SkyWatch가 자체 "Credits" 단위를 쓰는 경우(credits_to_krw 등)에도 동일한
    패턴으로 확장된다 - 새 통화쌍이 생겨도 이 함수를 다시 고칠 필요가 없다.
    역방향 키(to_from)만 있으면 그 값의 역수를 쓴다.
    """
    if amount is None:
        return None
    if from_currency == to_currency:
        return amount
    key = f"{from_currency.lower()}_to_{to_currency.lower()}"
    rate = exchange_rate_cfg.get(key)
    if rate is not None:
        return amount * rate
    inverse_key = f"{to_currency.lower()}_to_{from_currency.lower()}"
    inverse_rate = exchange_rate_cfg.get(inverse_key)
    if inverse_rate is not None:
        return amount / inverse_rate
    return None


def build_cost_scenarios(proj_cfg: dict) -> pd.DataFrame:
    """면적 시나리오별 SkyWatch/항공촬영 비용 표를 만든다. 값이 없으면 빈 칸(NaN)."""
    cost_cfg = proj_cfg["cost_comparison"]
    acquisition_count = cost_cfg.get("acquisition_count", 2)
    scenarios = cost_cfg.get("scenarios_km2", DEFAULT_SCENARIOS_KM2)
    skywatch_cfg = cost_cfg.get("skywatch", {})
    aerial_cfg = cost_cfg.get("aerial", {})
    exchange_rate_cfg = cost_cfg.get("exchange_rate", {})

    rows = []
    for area_km2 in scenarios:
        sw_cost = compute_skywatch_cost(area_km2, acquisition_count, skywatch_cfg)
        aerial_cost = compute_aerial_cost(area_km2, acquisition_count, aerial_cfg)
        sw_currency = skywatch_cfg.get("currency", "USD")
        aerial_currency = aerial_cfg.get("currency", "KRW")
        sw_in_aerial_currency = convert_currency(sw_cost, sw_currency, aerial_currency, exchange_rate_cfg)
        rows.append({
            "area_km2": area_km2,
            "acquisition_count": acquisition_count,
            "skywatch_cost": sw_cost,
            "skywatch_currency": sw_currency,
            "aerial_cost": aerial_cost,
            "aerial_currency": aerial_currency,
            f"skywatch_cost_in_{aerial_currency}": sw_in_aerial_currency,
            "cost_comparable": sw_in_aerial_currency is not None and aerial_cost is not None,
        })
    return pd.DataFrame(rows)


def _qual_row(item: str, skywatch: str, aerial: str) -> dict:
    return {"항목": item, "SkyWatch": skywatch, "항공사진": aerial}


def build_comparison_table(proj_cfg: dict, area_km2_for_summary: float | None = None) -> pd.DataFrame:
    """정성 비교표(SkyWatch vs 항공사진). 실제 확인된 값이 없는 항목은 "확인 필요"/N/A로 둔다.

    이 PoC가 이번 국가철도공단 건에 대해 실제로 확인한 값(예: config에 입력된
    면적/촬영횟수/최소주문금액)만 채우고, 나머지 정성 항목(과거 영상 확보
    가능성, Lead Time 등)은 벤더/현업 확인 없이 임의로 단정하지 않는다(#32 원칙).
    """
    cost_cfg = proj_cfg["cost_comparison"]
    acquisition_count = cost_cfg.get("acquisition_count", 2)
    skywatch_cfg = cost_cfg.get("skywatch", {})
    aerial_cfg = cost_cfg.get("aerial", {})
    area_km2 = area_km2_for_summary if area_km2_for_summary is not None else cost_cfg.get("area_km2")

    sw_cost = compute_skywatch_cost(area_km2, acquisition_count, skywatch_cfg) if area_km2 else None
    aerial_cost = compute_aerial_cost(area_km2, acquisition_count, aerial_cfg) if area_km2 else None

    def cost_str(cost, currency):
        if cost is None:
            return NEEDS_CONFIRMATION
        return f"{cost:,.0f} {currency}"

    rows = [
        _qual_row("과거 영상 확보 가능성", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row("신규 촬영 가능성", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row("공간해상도", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row(
            "대상 면적",
            f"{area_km2:g} km²" if area_km2 else NEEDS_CONFIRMATION,
            f"{area_km2:g} km²" if area_km2 else NEEDS_CONFIRMATION,
        ),
        _qual_row("촬영 횟수", f"{acquisition_count}회", f"{acquisition_count}회"),
        _qual_row(
            "최소 주문 조건",
            f"{skywatch_cfg['minimum_order_cost']:,.0f} {skywatch_cfg.get('currency', 'USD')}"
            if skywatch_cfg.get("minimum_order_cost") is not None else NEEDS_CONFIRMATION,
            NOT_AVAILABLE,
        ),
        _qual_row("초기 준비", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row("촬영 준비 기간", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row("영상 확보 Lead Time", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row("반복 촬영 편의성", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row("후처리 필요 여부", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row("ArcGIS 활용 편의성", NEEDS_CONFIRMATION, NEEDS_CONFIRMATION),
        _qual_row("예상 비용", cost_str(sw_cost, skywatch_cfg.get("currency", "USD")), cost_str(aerial_cost, aerial_cfg.get("currency", "KRW"))),
    ]
    return pd.DataFrame(rows)


def process_imagery_candidates(csv_path: str | Path) -> pd.DataFrame | None:
    """SkyWatch 영상 후보 CSV가 있으면 정렬해 반환한다 (없으면 None).

    정렬 기준(#25): 1) 분석 시점과 가까운 영상(acquisition_date), 2) 낮은
    cloud_cover_pct, 3) 높은 spatial resolution(= 낮은 resolution_m). 값이
    비어 있는 컬럼은 정렬 우선순위에서 가장 뒤로 보낸다. 가격을 포함한
    종합점수는 만들지 않는다(#25 원칙).
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        logger.info("[COST] 영상 후보 CSV 없음 - 건너뜀: %s", csv_path)
        return None

    df = pd.read_csv(csv_path)
    if df.empty:
        return df

    df["_cloud_cover_sort"] = pd.to_numeric(df.get("cloud_cover_pct"), errors="coerce")
    df["_resolution_sort"] = pd.to_numeric(df.get("resolution_m"), errors="coerce")
    df["_date_sort"] = pd.to_datetime(df.get("acquisition_date"), errors="coerce")

    df = df.sort_values(
        by=["_date_sort", "_cloud_cover_sort", "_resolution_sort"],
        ascending=[False, True, True],
        na_position="last",
    ).drop(columns=["_cloud_cover_sort", "_resolution_sort", "_date_sort"])
    return df.reset_index(drop=True)


IMAGERY_CANDIDATE_COLUMNS = [
    "scene_id", "provider", "acquisition_date", "resolution_m", "cloud_cover_pct",
    "archive_or_tasking", "area_km2", "unit_price", "currency", "status", "source", "notes",
]


def run_cost_comparison(project_config_path: str | Path, out_dir: str | Path | None = None) -> dict:
    """비용 시나리오 + 정성 비교표(+영상 후보 CSV가 있으면 정렬본)를 생성한다."""
    proj_cfg = load_cost_config(project_config_path)
    out_dir = Path(out_dir) if out_dir else Path(proj_cfg["paths"]["output_root"])
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    scenarios_df = build_cost_scenarios(proj_cfg)
    comparison_df = build_comparison_table(proj_cfg)

    comparison_path = reports_dir / "cost_comparison.csv"
    comparison_df.to_csv(comparison_path, index=False, encoding="utf-8-sig")
    scenarios_path = reports_dir / "cost_scenarios.csv"
    scenarios_df.to_csv(scenarios_path, index=False, encoding="utf-8-sig")
    logger.info("[COST] 저장 완료: %s, %s", comparison_path, scenarios_path)

    candidates_csv = proj_cfg.get("imagery_candidates", {}).get("csv_path")
    candidates_report_path = None
    if candidates_csv:
        candidates_df = process_imagery_candidates(candidates_csv)
        if candidates_df is not None:
            candidates_report_path = reports_dir / "imagery_candidates.csv"
            candidates_df.to_csv(candidates_report_path, index=False, encoding="utf-8-sig")
            logger.info("[COST] 영상 후보 정렬본 저장: %s (%d건)", candidates_report_path, len(candidates_df))

    result = {
        "project_id": proj_cfg["project"]["id"],
        "cost_comparison_csv": str(comparison_path),
        "cost_scenarios_csv": str(scenarios_path),
        "imagery_candidates_csv": str(candidates_report_path) if candidates_report_path else None,
        "scenarios": scenarios_df.to_dict(orient="records"),
    }
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SkyWatch 영상 vs 항공사진 촬영 비용 비교")
    parser.add_argument("--project", required=True, help="프로젝트 YAML 경로 (예: config/projects/kr_rail_nohari.yaml)")
    parser.add_argument("--out-dir", default=None, help="결과 저장 루트 (생략 시 project config의 paths.output_root 사용)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = run_cost_comparison(args.project, out_dir=args.out_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
