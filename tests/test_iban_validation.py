"""Tests for `app.domain.iban_validation`."""

from stdnum import iban as iban_stdnum

from app.domain.iban_validation import (
    format_iban,
    normalize_iban,
    validate_iban,
    validate_qr_iban,
)


def _valid_qr_iban() -> str:
    """Build a structurally valid QR-IBAN (IID in the QR range) for tests."""
    base = "CH00" + "30076" + "000000123456"
    check = iban_stdnum.calc_check_digits(base)
    return "CH" + check + base[4:]


def test_validate_iban_accepts_empty_value():
    """The field is optional -- an empty value is not an error."""
    assert validate_iban("") is None
    assert validate_iban("   ") is None


def test_validate_iban_accepts_valid_iban_with_spaces():
    assert validate_iban("CH93 0076 2011 6238 5295 7") is None


def test_validate_iban_rejects_bad_checksum():
    error = validate_iban("CH93 0076 2011 6238 5295 0")
    assert error is not None
    assert "IBAN" in error


def test_validate_iban_rejects_garbage():
    error = validate_iban("not an iban!!")
    assert error is not None


def test_normalize_iban_strips_spaces_and_uppercases():
    assert normalize_iban("ch93 0076 2011 6238 5295 7") == "CH9300762011623852957"


def test_format_iban_groups_in_blocks_of_four():
    assert format_iban("CH9300762011623852957") == "CH93 0076 2011 6238 5295 7"


def test_format_iban_groups_even_malformed_input_without_raising():
    """format_iban is display-only -- it must never raise, even on garbage."""
    assert format_iban("not an iban") == "NOTA NIBA N"


def test_validate_qr_iban_accepts_real_qr_iban():
    assert validate_qr_iban(_valid_qr_iban()) is None


def test_validate_qr_iban_rejects_regular_iban():
    error = validate_qr_iban("CH9300762011623852957")
    assert error is not None
    assert "QR-IBAN" in error


def test_validate_qr_iban_accepts_empty_value():
    assert validate_qr_iban("") is None


def test_validate_qr_iban_still_reports_checksum_errors():
    error = validate_qr_iban("CH93 0076 2011 6238 5295 0")
    assert error is not None
    assert "Prüfziffer" in error
