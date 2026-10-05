"""Tests for the two formatting helpers."""

from datetime import date, datetime

import pytest

from app.formatting import MISSING, format_chf, format_date


@pytest.mark.parametrize(
    "rappen, expected",
    [
        (0, "0.00"),
        (5, "0.05"),
        (50, "0.50"),
        (100, "1.00"),
        (12345, "123.45"),
        (123456, "1'234.56"),
        (100000000, "1'000'000.00"),
        (-2000, "-20.00"),
        (-5, "-0.05"),
    ],
)
def test_an_amount_reads_as_swiss_francs(rappen, expected):
    """Grouped with an apostrophe, two decimals, minus in front."""
    assert format_chf(rappen) == expected


def test_the_unit_is_optional():
    """A column header or a neighbouring label often says it already."""
    assert format_chf(2050, with_unit=True) == "20.50 CHF"
    assert format_chf(2050) == "20.50"


def test_no_amount_is_not_zero():
    """ "Not recorded" and "zero francs" are different statements."""
    assert format_chf(None) == MISSING
    assert format_chf(0) == "0.00"


def test_the_sign_survives_a_single_rappen():
    """A credit of one Rappen must not read as a debit."""
    assert format_chf(-1) == "-0.01"


@pytest.mark.parametrize(
    "value, expected",
    [
        (date(2026, 10, 4), "04.10.2026"),
        (datetime(2026, 10, 4, 15, 30), "04.10.2026"),
        ("2026-10-04", "04.10.2026"),
        ("2026-10-04T15:30:00", "04.10.2026"),
        (date(2026, 1, 1), "01.01.2026"),
    ],
)
def test_a_date_reads_the_way_the_rest_of_the_app_writes_it(value, expected):
    """Including an ISO string, which is how the database stores one."""
    assert format_date(value) == expected


def test_no_date_is_not_today():
    """A made-up date prints as though it were recorded."""
    assert format_date(None) == MISSING
    assert format_date("") == MISSING


def test_something_unparseable_is_shown_rather_than_swallowed():
    """A stored value nobody expected should be visible."""
    assert format_date("irgendwas") == "irgendwas"


def test_a_stray_float_is_rounded_rather_than_truncated():
    """Amounts are integer Rappen by convention; this is about when one is not."""
    assert format_chf(1234.9) == "12.35"
    assert format_chf(-1234.9) == "-12.35"
    assert format_chf(1234.0) == "12.34"
