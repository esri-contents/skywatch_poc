import pandas as pd
import yaml

from src.cost.comparison import (
    build_comparison_table,
    build_cost_scenarios,
    compute_aerial_cost,
    compute_skywatch_cost,
    convert_currency,
    process_imagery_candidates,
    run_cost_comparison,
)


def _project_cfg(**cost_overrides):
    cost_comparison = {
        "area_km2": None,
        "acquisition_count": 2,
        "skywatch": {"unit_price_per_km2": None, "minimum_order_cost": None, "processing_cost": None, "currency": "USD"},
        "aerial": {"mobilization_cost": None, "unit_price_per_km2": None, "processing_cost": None, "currency": "KRW"},
        "exchange_rate": {"usd_to_krw": None, "source": None, "reference_date": None},
        "scenarios_km2": [10, 50],
    }
    cost_comparison.update(cost_overrides)
    return {
        "project": {"id": "test_project"},
        "paths": {"output_root": "unused"},
        "cost_comparison": cost_comparison,
    }


def test_compute_skywatch_cost_none_when_unit_price_missing():
    assert compute_skywatch_cost(10, 2, {"unit_price_per_km2": None}) is None


def test_compute_skywatch_cost_basic():
    cfg = {"unit_price_per_km2": 100, "processing_cost": 50}
    # 10km^2 * 100 * 2회 + 50 = 2050
    assert compute_skywatch_cost(10, 2, cfg) == 2050


def test_compute_skywatch_cost_minimum_order_applied():
    cfg = {"unit_price_per_km2": 10, "minimum_order_cost": 1000}
    # 1km^2 * 10 = 10 < minimum_order(1000) -> 1000 사용, 2회 -> 2000
    assert compute_skywatch_cost(1, 2, cfg) == 2000


def test_compute_aerial_cost_basic():
    cfg = {"unit_price_per_km2": 500000, "mobilization_cost": 2_000_000, "processing_cost": 300_000}
    # 2,000,000 + 10*500,000*2 + 300,000 = 12,300,000
    assert compute_aerial_cost(10, 2, cfg) == 12_300_000


def test_compute_aerial_cost_none_when_unit_price_missing():
    assert compute_aerial_cost(10, 2, {"unit_price_per_km2": None}) is None


def test_convert_currency_none_without_rate():
    assert convert_currency(100, "USD", "KRW", {"usd_to_krw": None}) is None


def test_convert_currency_with_rate():
    assert convert_currency(100, "USD", "KRW", {"usd_to_krw": 1300}) == 130000


def test_convert_currency_same_currency_passthrough():
    assert convert_currency(100, "KRW", "KRW", {"usd_to_krw": None}) == 100


def test_convert_currency_generalizes_to_non_usd_currency():
    """SkyWatch의 자체 'Credits' 단위처럼 usd_to_krw 외의 통화쌍도 동작해야 한다."""
    assert convert_currency(10, "Credits", "KRW", {"credits_to_krw": 1500}) == 15000
    assert convert_currency(10, "Credits", "KRW", {}) is None


def test_convert_currency_uses_inverse_rate_when_only_reverse_key_present():
    assert convert_currency(1500, "KRW", "Credits", {"credits_to_krw": 1500}) == 1


def test_build_cost_scenarios_all_null_produces_none_costs():
    cfg = _project_cfg()
    df = build_cost_scenarios(cfg)
    assert len(df) == 2
    assert df["skywatch_cost"].isna().all()
    assert df["aerial_cost"].isna().all()


def test_build_cost_scenarios_computes_when_prices_present():
    cfg = _project_cfg(
        skywatch={"unit_price_per_km2": 100, "minimum_order_cost": None, "processing_cost": None, "currency": "USD"},
        aerial={"mobilization_cost": None, "unit_price_per_km2": 500000, "processing_cost": None, "currency": "KRW"},
    )
    df = build_cost_scenarios(cfg)
    row = df[df["area_km2"] == 10].iloc[0]
    assert row["skywatch_cost"] == 10 * 100 * 2
    assert row["aerial_cost"] == 10 * 500000 * 2


def test_build_comparison_table_shows_needs_confirmation_when_no_data():
    cfg = _project_cfg()
    df = build_comparison_table(cfg)
    row = df[df["항목"] == "예상 비용"].iloc[0]
    assert row["SkyWatch"] == "확인 필요"
    assert row["항공사진"] == "확인 필요"
    area_row = df[df["항목"] == "대상 면적"].iloc[0]
    assert area_row["SkyWatch"] == "확인 필요"


def test_build_comparison_table_shows_cost_when_area_and_price_present():
    cfg = _project_cfg(
        area_km2=10,
        skywatch={"unit_price_per_km2": 100, "minimum_order_cost": None, "processing_cost": None, "currency": "USD"},
        aerial={"mobilization_cost": None, "unit_price_per_km2": None, "processing_cost": None, "currency": "KRW"},
    )
    df = build_comparison_table(cfg)
    row = df[df["항목"] == "예상 비용"].iloc[0]
    assert "USD" in row["SkyWatch"]
    assert row["항공사진"] == "확인 필요"  # 항공 unit_price가 없으므로


def test_process_imagery_candidates_missing_file_returns_none(tmp_path):
    assert process_imagery_candidates(tmp_path / "does_not_exist.csv") is None


def test_process_imagery_candidates_sorts_by_date_cloud_resolution(tmp_path):
    csv_path = tmp_path / "candidates.csv"
    pd.DataFrame([
        {"scene_id": "A", "acquisition_date": "2024-01-01", "cloud_cover_pct": 20, "resolution_m": 1.0},
        {"scene_id": "B", "acquisition_date": "2024-06-01", "cloud_cover_pct": 5, "resolution_m": 0.5},
        {"scene_id": "C", "acquisition_date": "2024-06-01", "cloud_cover_pct": 5, "resolution_m": 2.0},
    ]).to_csv(csv_path, index=False)

    df = process_imagery_candidates(csv_path)
    # 가장 최근 날짜(2024-06-01) 중 구름 낮고 해상도 높은(B) 순.
    assert list(df["scene_id"]) == ["B", "C", "A"]


def test_run_cost_comparison_writes_csvs_without_prices(tmp_path):
    project_path = tmp_path / "project.yaml"
    out_dir = tmp_path / "outputs"
    cfg = _project_cfg()
    cfg["paths"]["output_root"] = str(out_dir)
    with open(project_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)

    result = run_cost_comparison(project_path)
    assert (out_dir / "reports" / "cost_comparison.csv").exists()
    assert (out_dir / "reports" / "cost_scenarios.csv").exists()
    df = pd.read_csv(out_dir / "reports" / "cost_comparison.csv")
    assert (df["SkyWatch"] == "확인 필요").any()
