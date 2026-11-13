"""Parser của app/db/catalog_import.py trên các định dạng lạ có thật trong CKD_MASTER_PRODUCT_DATABASE.csv."""
from decimal import Decimal

import pytest

from app.db.catalog_import import (parse_mass, parse_power, parse_price, parse_row, parse_torque, parse_triple)


def _row(**cells):
    return {"Brand": "THACO ", "Model": "X1", **cells}


@pytest.mark.parametrize("raw, amount, basis, review", [
    ("509 (Chassis)", 509_000_000, "CHASSIS", False),
    ("406,8 (chassis)", 406_800_000, "CHASSIS", False),
    ("775,656 (Chassis)", 775_656_000, "CHASSIS", False),
    ("207 (thùng lửng)", 207_000_000, "FLATBED", False),
    ("340 (mui bạt)", 340_000_000, "TARP", False),
    ("162.1", 162_100_000, "UNKNOWN", False),
    ("427 (417,6) (chassis)", 427_000_000, "CHASSIS", True),  # hai mức giá: lấy mức đầu, cần duyệt
])
def test_price(raw, amount, basis, review):
    issues = []
    price = parse_price(raw, issues)
    assert (price["amount_vnd"], price["basis"], price["needs_review"]) == (amount, basis, review)


@pytest.mark.parametrize("raw", ["561816", "819180"])
def test_price_with_wrong_scale_is_not_guessed(raw):
    issues = []
    assert parse_price(raw, issues) is None
    assert [i.code for i in issues] == ["scale_suspect"] and issues[0].resolution is None


def test_power_and_torque_formats():
    assert parse_power("127 (170hp)/2500", []) == (Decimal(127), 2500)
    assert parse_power("91//2600", []) == (Decimal(91), 2600)
    assert parse_power("110/---", []) == (Decimal(110), None)
    assert parse_power("120/3.000", []) == (Decimal(120), 3000)
    assert parse_power("95.6/3800", []) == (Decimal("95.6"), 3800)
    assert parse_torque("320/1.400 - 2.400", []) == (Decimal(320), 1400, 2400)
    assert parse_torque("355/1800–2700", []) == (Decimal(355), 1800, 2700)
    assert parse_torque("285/2000", []) == (Decimal(285), 2000, 2000)
    assert parse_torque("450/---", []) == (Decimal(450), None, None)


def test_implausible_rpm_is_left_null_and_flagged():
    issues = []
    assert parse_power("150/260", issues) == (Decimal(150), None)
    assert [i.code for i in issues] == ["scale_suspect"]


def test_mass_dimensions_and_alternatives():
    assert parse_mass("1665 (chassis)", []) == (1665, "chassis")
    assert parse_mass("1810 - chassis", []) == (1810, "chassis")
    assert parse_mass("2.865", []) == (2865, None)
    assert parse_triple("7440 x 2385 x 2515 (chassis)", "d", []) == ([7440, 2385, 2515], "chassis")
    assert parse_triple("4250 x 1500 x1850", "d", []) == ([4250, 1500, 1850], None)
    assert parse_triple("3300×1710×360", "d", []) == ([3300, 1710, 360], None)
    assert parse_triple("6100 x 2220 x 780/2100", "d", []) == ([6100, 2220, None], None)


def test_row_keeps_alternatives_out_of_typed_columns():
    p = parse_row(9, _row(**{"Payload": "990/1210", "Gross Vehicle Mass": "3490/4300/4990", "ABS systerm": "co",
                             "Exhaust gas treatment": "-", "Transmission Type": "Cơ khí - 06 số tiến 01 số lùi",
                             "Piston displacement": "2.493", "Overall Dimension": "---"}))
    assert p.brand == "THACO" and p.model == "X1"
    assert p.spec["payload_kg"] is None and p.extra["load_alternatives"] == "990/1210"
    assert p.spec["gvm_kg"] is None and p.extra["gvm_alternatives"] == "3490/4300/4990"
    assert p.spec["has_abs"] is True and p.spec["has_scr"] is None  # "-" là chưa biết, không phải false
    assert (p.spec["gears_forward"], p.spec["gears_reverse"]) == (6, 1)
    assert p.spec["displacement_cc"] == 2493
    assert "length_mm" not in p.spec


def test_brand_alias_and_missing_model():
    assert parse_row(5, _row(Brand="ĐÔ THÀNH")).brand == "DOTHANH"
    p = parse_row(73, _row(Model=""))
    assert any(i.code == "identity_missing" and i.resolution is None for i in p.issues)
