from datetime import date

import pandas as pd

from src.buildings.validation import (
    _join_key9,
    _parse_buld_no,
    _road_key_from_buildings,
    _road_key_from_register,
    compute_administrative_uncertainty,
)


def test_join_key9_strips_san_digit():
    pnu = pd.Series(["4128111300108880012"])
    assert _join_key9(pnu).iloc[0] == "4128111300" + "08880012"


def test_parse_buld_no_with_sub():
    assert _parse_buld_no("307-16") == ("307", "16")


def test_parse_buld_no_without_sub():
    assert _parse_buld_no("315") == ("315", "0")


def test_parse_buld_no_empty():
    assert _parse_buld_no("") == ("0", "0")


def test_road_key_register_vs_buildings_agree_for_matching_case():
    reg = pd.DataFrame({
        "naRoadCd": ["412813000007"], "naMainBun": ["307"], "naSubBun": ["16"],
    })
    b = pd.DataFrame({"rn_cd": ["3000007"], "buld_no": ["307-16"]})
    assert _road_key_from_register(reg).iloc[0] == _road_key_from_buildings(b).iloc[0]


def test_admin_uncertainty_no_match():
    row = pd.Series({"has_register_match": False})
    assert compute_administrative_uncertainty(row, date(2022, 1, 1), date(2024, 1, 1)) == 1.0


def test_admin_uncertainty_explained():
    row = pd.Series({"has_register_match": True, "useAprDay": "20230601"})
    assert compute_administrative_uncertainty(row, date(2022, 1, 1), date(2024, 1, 1)) == 0.1


def test_admin_uncertainty_matched_but_outside_window():
    row = pd.Series({"has_register_match": True, "useAprDay": "19950101"})
    assert compute_administrative_uncertainty(row, date(2022, 1, 1), date(2024, 1, 1)) == 0.6
