"""Tests for app.importers.camt_parser (camt.053 and camt.054 XML parsing).

Uses the hand-built fixtures in tests/fixtures/ -- no real bank data
involved, all counterparty names are fantasy ("Muster ..."), matching the
project's test-isolation discipline."""

from pathlib import Path

import pytest

from app.importers.base import ImportValidationError
from app.importers.camt_parser import parse_camt_file

_FIXTURES = Path(__file__).parent / "fixtures"


def _by_reference(transactions, reference):
    matches = [tx for tx in transactions if tx.bank_reference == reference]
    assert len(matches) == 1, f"expected exactly one match for {reference!r}, got {len(matches)}"
    return matches[0]


def test_parses_camt053_statement_metadata():
    result = parse_camt_file(_FIXTURES / "sample_camt053.xml")

    assert result.source_format == "camt053"
    assert result.account_iban == "CH9300762011623852957"
    assert result.statement_from == "2026-01-01"
    assert result.statement_to == "2026-01-31"


def test_camt053_extracts_qrr_referenced_credit():
    result = parse_camt_file(_FIXTURES / "sample_camt053.xml")
    tx = _by_reference(result.transactions, "ACCTSVCR-0001")

    assert tx.amount_rappen == 12_345
    assert tx.currency == "CHF"
    assert tx.credit_debit_indicator == "CRDT"
    assert tx.counterparty_name == "Muster Anna"
    assert tx.counterparty_iban == "CH5604835012345678009"
    assert tx.structured_reference == "1002340000050000000000427"
    assert tx.is_reversal is False


def test_camt053_extracts_free_text_credit_without_structured_reference():
    result = parse_camt_file(_FIXTURES / "sample_camt053.xml")
    tx = _by_reference(result.transactions, "ACCTSVCR-0002")

    assert tx.structured_reference == ""
    assert "200500" in tx.remittance_text


def test_camt053_extracts_outgoing_payout_with_creditor_iban():
    result = parse_camt_file(_FIXTURES / "sample_camt053.xml")
    tx = _by_reference(result.transactions, "ACCTSVCR-0003")

    assert tx.credit_debit_indicator == "DBIT"
    assert tx.counterparty_name == "Muster Bettina"
    assert tx.counterparty_iban == "CH1234835098765432001"


def test_camt053_skips_foreign_currency_with_warning():
    result = parse_camt_file(_FIXTURES / "sample_camt053.xml")

    assert not any(tx.bank_reference == "ACCTSVCR-0004" for tx in result.transactions)
    assert any("EUR" in w and "CHF" in w for w in result.warnings)


def test_camt053_marks_reversal_entry_and_still_extracts_its_reference():
    """A reversal must still be parsed (so it can be shown for manual
    review), but flagged -- it is app.domain.bank_reconciliation's job to
    refuse auto-matching it, not the parser's job to drop it."""
    result = parse_camt_file(_FIXTURES / "sample_camt053.xml")
    tx = _by_reference(result.transactions, "ACCTSVCR-0005")

    assert tx.is_reversal is True
    assert tx.structured_reference == "3007770000050000000000444"


def test_camt053_synthesizes_fallback_reference_when_none_present():
    result = parse_camt_file(_FIXTURES / "sample_camt053.xml")
    fallback_tx = [tx for tx in result.transactions if tx.counterparty_name == "Ohne Referenz AG"]

    assert len(fallback_tx) == 1
    assert fallback_tx[0].bank_reference.startswith("FALLBACK-")
    assert fallback_tx[0].bank_reference != ""


def test_camt054_expands_batched_ntry_into_individually_referenced_transactions():
    """The whole point of supporting camt.054: a bank's collective
    QR-payment booking (one Ntry, several TxDtls) must become one
    ParsedBankTransaction per TxDtls, each with its own reference and its
    own (not the batch total) amount."""
    result = parse_camt_file(_FIXTURES / "sample_camt054.xml")

    assert result.source_format == "camt054"
    assert len(result.transactions) == 2

    tx_a = _by_reference(result.transactions, "ACCTSVCR-C054-0001")
    tx_b = _by_reference(result.transactions, "ACCTSVCR-C054-0002")
    assert tx_a.amount_rappen == 8_000
    assert tx_a.structured_reference == "1002340000050000000000427"
    assert tx_b.amount_rappen == 7_000
    assert tx_b.structured_reference == "2005000000050000000000430"
    # Neither individual amount equals the Ntry-level batch total (150.00) --
    # confirms the per-TxDtls amount was used, not the parent Ntry's.
    assert tx_a.amount_rappen + tx_b.amount_rappen == 15_000


def test_raises_import_validation_error_for_unrecognized_root_element(tmp_path):
    bogus = tmp_path / "not_a_camt_file.xml"
    bogus.write_text('<?xml version="1.0"?><SomethingElse/>', encoding="utf-8")

    with pytest.raises(ImportValidationError):
        parse_camt_file(bogus)


def test_raises_import_validation_error_for_malformed_xml(tmp_path):
    bogus = tmp_path / "broken.xml"
    bogus.write_text("<Document><Unclosed>", encoding="utf-8")

    with pytest.raises(ImportValidationError):
        parse_camt_file(bogus)
