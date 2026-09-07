"""Round-trip tests for app.pdf.qr_reference -- these run first, before any
other bank-reconciliation code is built, to empirically pin down the exact
digit-padding/checksum behavior `parse_qrr_reference` relies on."""

import pytest

from app.pdf.qr_reference import generate_qrr_reference, parse_qrr_reference


@pytest.mark.parametrize(
    "kundennummer, billing_run_id, item_id",
    [
        (123456, 42, 999),
        (100000, 1, 1),
        (999999, 999999, 999999999999),
    ],
)
def test_round_trip_recovers_original_ids(kundennummer, billing_run_id, item_id):
    reference = generate_qrr_reference(kundennummer, billing_run_id, item_id)
    decoded = parse_qrr_reference(reference)

    assert decoded is not None
    assert decoded.kundennummer == kundennummer
    assert decoded.billing_run_id == billing_run_id
    assert decoded.item_id == item_id


def test_round_trip_works_with_full_27_digit_zero_padded_form():
    """The reference as it actually appears on a printed QR-bill/bank
    statement is zero-padded to 27 digits (2 leading zeros in front of
    `generate_qrr_reference`'s own 25-character output) -- must decode
    identically to the unpadded form."""
    reference = generate_qrr_reference(123456, 42, 999)
    padded = "00" + reference
    assert len(padded) == 27

    assert parse_qrr_reference(padded) == parse_qrr_reference(reference)


def test_round_trip_tolerates_grouping_spaces():
    reference = generate_qrr_reference(123456, 42, 999)
    grouped = " ".join(reference[i : i + 5] for i in range(0, len(reference), 5))

    assert parse_qrr_reference(grouped) == parse_qrr_reference(reference)


def test_corrupted_checksum_returns_none():
    reference = generate_qrr_reference(123456, 42, 999)
    last_digit = int(reference[-1])
    corrupted = reference[:-1] + str((last_digit + 1) % 10)

    assert parse_qrr_reference(corrupted) is None


@pytest.mark.parametrize("garbage", ["", "not a reference", "123", "   "])
def test_non_numeric_or_too_short_input_returns_none(garbage):
    assert parse_qrr_reference(garbage) is None


def test_none_input_returns_none():
    assert parse_qrr_reference(None) is None
