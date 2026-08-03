"""Tests for QR-invoice, credit note, payment list and export generation."""

from decimal import Decimal

import pytest

from app.domain.billing import create_or_replace_billing_run
from app.domain.demo_data import SUMMER_QUARTER, create_demo_data
from app.models import billing_run as billing_run_repo
from app.models import participant as participant_repo
from app.models import settings as settings_repo
from app.pdf.credit_pdf import generate_credit_pdf
from app.pdf.export_service import export_billing_run_documents
from app.pdf.invoice_pdf import generate_invoice_pdf
from app.pdf.payment_list import generate_payment_list_pdf
from app.pdf.qr_reference import generate_qrr_reference


def _assert_is_pdf(path) -> None:
    """Assert that a file exists and starts with the PDF magic bytes.

    Args:
        path: Path of the file to check.

    Returns:
        None.
    """
    assert path.exists()
    with open(path, "rb") as f:
        assert f.read(5) == b"%PDF-"


def test_generate_qrr_reference_is_unique_per_item():
    """Different item ids produce different, valid-length references."""
    ref1 = generate_qrr_reference(1, 1, 1)
    ref2 = generate_qrr_reference(1, 1, 2)
    assert len(ref1) == 27
    assert ref1 != ref2


def test_generate_invoice_pdf_creates_valid_pdf_file(db, tmp_path):
    """A "rechnung" item renders to a real PDF file including the QR-bill."""
    create_demo_data(db)
    run, items, _, _ = create_or_replace_billing_run(db, *SUMMER_QUARTER)
    invoice_item = next(i for i in items if i.kind == "rechnung")
    participant = participant_repo.get(db, invoice_item.participant_id)
    settings = settings_repo.get_settings(db)

    output_path = tmp_path / "invoice.pdf"
    generate_invoice_pdf(run, invoice_item, participant, settings, output_path)

    _assert_is_pdf(output_path)


def test_generate_invoice_pdf_rejects_credit_item(db, tmp_path):
    """Passing a "gutschrift" item to the invoice generator is a programming error."""
    create_demo_data(db)
    run, items, _, _ = create_or_replace_billing_run(db, *SUMMER_QUARTER)
    credit_item = next(i for i in items if i.kind == "gutschrift")
    participant = participant_repo.get(db, credit_item.participant_id)
    settings = settings_repo.get_settings(db)

    with pytest.raises(ValueError):
        generate_invoice_pdf(run, credit_item, participant, settings, tmp_path / "x.pdf")


def test_generate_credit_pdf_creates_valid_pdf_file(db, tmp_path):
    """A "gutschrift" item renders to a real PDF file without a QR-bill."""
    create_demo_data(db)
    run, items, _, _ = create_or_replace_billing_run(db, *SUMMER_QUARTER)
    credit_item = next(i for i in items if i.kind == "gutschrift")
    participant = participant_repo.get(db, credit_item.participant_id)
    settings = settings_repo.get_settings(db)

    output_path = tmp_path / "credit.pdf"
    generate_credit_pdf(run, credit_item, participant, settings, output_path)

    _assert_is_pdf(output_path)


def test_generate_payment_list_pdf(db, tmp_path):
    """The payment list renders for all credit note items in a run."""
    create_demo_data(db)
    run, items, _, _ = create_or_replace_billing_run(db, *SUMMER_QUARTER)
    participants = {p.id: p for p in participant_repo.list_all(db)}

    output_path = tmp_path / "zahlliste.pdf"
    generate_payment_list_pdf(run, items, participants, output_path)

    _assert_is_pdf(output_path)


def test_export_billing_run_documents_writes_all_files_and_updates_db(db, tmp_path, monkeypatch):
    """The export service writes one PDF per item plus a payment list, and
    records each item's PDF path back into the database."""
    import app.pdf.export_service as export_service

    monkeypatch.setattr(export_service, "OUTPUT_DIR", tmp_path)

    create_demo_data(db)
    run, items, _, _ = create_or_replace_billing_run(db, *SUMMER_QUARTER)

    result = export_service.export_billing_run_documents(db, run)

    assert not result.errors
    assert len(result.document_paths) == len(items)
    for path in result.document_paths:
        _assert_is_pdf(path)
    assert result.payment_list_path is not None
    _assert_is_pdf(result.payment_list_path)

    stored_items = billing_run_repo.list_items(db, run.id)
    assert all(item.pdf_path for item in stored_items)
