"""Tests for `app.domain.metering_point_validation`."""

from app.domain.metering_point_validation import (
    assemble_metering_point_designation,
    validate_identifier,
    validate_country,
    validate_metering_point_designation,
)

#: Example from the VSE guideline.
_EXAMPLE = "CH9876501234500A7T839KH38O2D78R45"


def test_assemble_pads_metering_point_number_with_leading_zeros():
    assembled = assemble_metering_point_designation("ch", "98765012345", "A7T839KH38O2D78R45")
    assert assembled == _EXAMPLE


def test_assemble_uppercases_and_strips():
    assembled = assemble_metering_point_designation(" ch ", " 98765012345 ", " a7 ")
    assert assembled.startswith("CH98765012345")
    assert assembled.endswith("A7")
    assert len(assembled) == 33


def test_validate_metering_point_designation_accepts_guideline_example():
    assert validate_metering_point_designation(_EXAMPLE) is None


def test_validate_metering_point_designation_rejects_empty():
    error = validate_metering_point_designation("")
    assert error is not None
    assert "leer" in error


def test_validate_metering_point_designation_rejects_wrong_length():
    error = validate_metering_point_designation("CH123")
    assert error is not None
    assert "33" in error


def test_validate_metering_point_designation_rejects_lowercase_or_special_chars():
    error = validate_metering_point_designation(_EXAMPLE.lower())
    # lowercase gets uppercased internally, so this must still be valid
    assert error is None

    error = validate_metering_point_designation(_EXAMPLE[:-1] + "!")
    assert error is not None


def test_validate_land_accepts_empty_and_two_letters():
    assert validate_country("") is None
    assert validate_country("ch") is None
    assert validate_country("CH") is None


def test_validate_land_rejects_wrong_length_or_digits():
    assert validate_country("C") is not None
    assert validate_country("CHE") is not None
    assert validate_country("C1") is not None


def test_validate_identifier_accepts_empty_and_eleven_chars():
    assert validate_identifier("") is None
    assert validate_identifier("98765012345") is None


def test_validate_identifier_rejects_wrong_length():
    assert validate_identifier("123") is not None
    assert validate_identifier("123456789012") is not None
