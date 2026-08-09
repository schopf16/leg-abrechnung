"""Tests for `app.domain.messpunkt_validation`."""

from app.domain.messpunkt_validation import (
    assemble_messpunkt_bezeichnung,
    validate_identifikator,
    validate_land,
    validate_messpunkt_bezeichnung,
)

#: Example from the VSE guideline.
_EXAMPLE = "CH9876501234500A7T839KH38O2D78R45"


def test_assemble_pads_messpunktnummer_with_leading_zeros():
    assembled = assemble_messpunkt_bezeichnung("ch", "98765012345", "A7T839KH38O2D78R45")
    assert assembled == _EXAMPLE


def test_assemble_uppercases_and_strips():
    assembled = assemble_messpunkt_bezeichnung(" ch ", " 98765012345 ", " a7 ")
    assert assembled.startswith("CH98765012345")
    assert assembled.endswith("A7")
    assert len(assembled) == 33


def test_validate_messpunkt_bezeichnung_accepts_guideline_example():
    assert validate_messpunkt_bezeichnung(_EXAMPLE) is None


def test_validate_messpunkt_bezeichnung_rejects_empty():
    error = validate_messpunkt_bezeichnung("")
    assert error is not None
    assert "leer" in error


def test_validate_messpunkt_bezeichnung_rejects_wrong_length():
    error = validate_messpunkt_bezeichnung("CH123")
    assert error is not None
    assert "33" in error


def test_validate_messpunkt_bezeichnung_rejects_lowercase_or_special_chars():
    error = validate_messpunkt_bezeichnung(_EXAMPLE.lower())
    # lowercase gets uppercased internally, so this must still be valid
    assert error is None

    error = validate_messpunkt_bezeichnung(_EXAMPLE[:-1] + "!")
    assert error is not None


def test_validate_land_accepts_empty_and_two_letters():
    assert validate_land("") is None
    assert validate_land("ch") is None
    assert validate_land("CH") is None


def test_validate_land_rejects_wrong_length_or_digits():
    assert validate_land("C") is not None
    assert validate_land("CHE") is not None
    assert validate_land("C1") is not None


def test_validate_identifikator_accepts_empty_and_eleven_chars():
    assert validate_identifikator("") is None
    assert validate_identifikator("98765012345") is None


def test_validate_identifikator_rejects_wrong_length():
    assert validate_identifikator("123") is not None
    assert validate_identifikator("123456789012") is not None
