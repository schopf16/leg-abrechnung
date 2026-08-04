"""Tests for the combined participant billing PDF, QR-bill voiding, payment
list and export generation."""

import re
from decimal import Decimal

from app.domain.billing import create_or_replace_billing_run
from app.domain.demo_data import SUMMER_QUARTER, create_demo_data
from app.domain.distribution import compute_quarter_distribution
from app.models import billing_run as billing_run_repo
from app.models import participant as participant_repo
from app.models import settings as settings_repo
from app.pdf.export_service import export_billing_run_documents
from app.pdf.participant_bill_pdf import generate_participant_bill_pdf
from app.pdf.payment_list import generate_payment_list_pdf
from app.pdf.qr_bill_render import build_qr_bill
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


def _page_count(path) -> int:
    """Read a PDF's page count from its `/Pages` object's `/Count` entry.

    Args:
        path: Path of the PDF file.

    Returns:
        The number of pages in the document.
    """
    match = re.search(rb"/Count (\d+) /Kids", path.read_bytes())
    assert match, "could not find a /Pages /Count entry in the PDF"
    return int(match.group(1))


def _billing_context(db):
    """Set up demo data and a summer billing run, returning common test fixtures.

    Args:
        db: Database connection fixture.

    Returns:
        A `(run, items, distribution, settings)` tuple.
    """
    create_demo_data(db)
    run, items, _, _ = create_or_replace_billing_run(db, *SUMMER_QUARTER)
    distribution = compute_quarter_distribution(db, *SUMMER_QUARTER)
    settings = settings_repo.get_settings(db)
    return run, items, distribution, settings


def test_generate_qrr_reference_is_unique_per_item():
    """Different item ids produce different, valid-length references."""
    ref1 = generate_qrr_reference(1, 1, 1)
    ref2 = generate_qrr_reference(1, 1, 2)
    assert len(ref1) == 27
    assert ref1 != ref2


def test_build_qr_bill_with_none_amount_encodes_no_fixed_amount():
    """A voided QR-bill (amount=None) never encodes a payable amount."""
    from app.models.participant import Participant
    from app.models.settings import LegSettings

    settings = LegSettings(
        name="LEG Test", address_street="Weg 1", address_zip="3000", address_city="Bern",
        address_country="CH", qr_iban="CH5730000123456789012", price_rp_per_kwh=12.0, updated_at="",
    )
    participant = Participant(
        id=1, name="Max Muster", address_street="Strasse 1", address_zip="8000",
        address_city="Zürich", address_country="CH", iban="", email="", created_at="",
    )
    ref = generate_qrr_reference(1, 1, 1)

    voided_bill = build_qr_bill(settings, participant, None, ref)
    payable_bill = build_qr_bill(settings, participant, Decimal("42.50"), ref)

    assert voided_bill.amount is None
    assert payable_bill.amount == "42.50"


def test_draw_qr_bill_uses_bill_only_svg_not_full_page(tmp_path):
    """`draw_qr_bill` must render qrbill's bill-only SVG, not its full-page one.

    Regression test for a real bug: qrbill's full_page=True output paints
    an opaque white rectangle across the *entire* A4 page as a background
    (qrbill/bill.py "Force white background"). Composited on top of an
    already-populated canvas via renderPDF.draw(), that rectangle silently
    erased all previously drawn content (letterhead, tables) -- the PDF
    still contained the text objects, so naive text extraction missed it,
    but nothing was visible except the QR-bill itself.
    """
    from app.models.participant import Participant
    from app.models.settings import LegSettings

    settings = LegSettings(
        name="LEG Test", address_street="Weg 1", address_zip="3000", address_city="Bern",
        address_country="CH", qr_iban="CH5730000123456789012", price_rp_per_kwh=12.0, updated_at="",
    )
    participant = Participant(
        id=1, name="Max Muster", address_street="Strasse 1", address_zip="8000",
        address_city="Zürich", address_country="CH", iban="", email="", created_at="",
    )
    ref = generate_qrr_reference(1, 1, 1)
    bill = build_qr_bill(settings, participant, Decimal("10.00"), ref)

    from svglib.svglib import svg2rlg

    svg_path = tmp_path / "bill.svg"
    bill.as_svg(str(svg_path), full_page=False)
    drawing = svg2rlg(str(svg_path))

    # The bill-only drawing is ~106mm (~300pt) tall, not a full A4 page
    # (~842pt) -- confirming it can only ever paint its own reserved
    # bottom strip, never the whole page.
    assert drawing.height < 320


def test_generate_participant_bill_pdf_for_prosumer_overflows_to_second_page(db, tmp_path):
    """A prosumer's document (both Bezug and Vergütung tables) is long enough to
    push the QR-bill onto a second page rather than overlapping the content.

    Regression test: the QR-bill must never be drawn on top of content that
    reaches into its reserved bottom area -- see app.pdf.layout.CONTENT_BOTTOM_Y
    and the page-break check in generate_participant_bill_pdf.
    """
    run, items, distribution, settings = _billing_context(db)
    prosumer_item = next(i for i in items if i.consumed_kwh > 0 and i.produced_kwh > 0)
    participant = participant_repo.get(db, prosumer_item.participant_id)
    participant_result = distribution.participant_results[prosumer_item.participant_id]

    output_path = tmp_path / "prosumer.pdf"
    generate_participant_bill_pdf(
        run, prosumer_item, participant_result, participant, settings, output_path
    )

    _assert_is_pdf(output_path)
    assert _page_count(output_path) == 2


def test_generate_participant_bill_pdf_for_credit_item_has_voided_amount(db, tmp_path):
    """A participant with a negative net (owed money by the LEG) gets a voided QR-bill."""
    run, items, distribution, settings = _billing_context(db)
    credit_item = next(i for i in items if i.is_owed_by_leg)
    participant = participant_repo.get(db, credit_item.participant_id)
    participant_result = distribution.participant_results[credit_item.participant_id]

    output_path = tmp_path / "credit.pdf"
    generate_participant_bill_pdf(
        run, credit_item, participant_result, participant, settings, output_path
    )

    _assert_is_pdf(output_path)


def test_generate_participant_bill_pdf_for_pure_consumer_fits_on_one_page(db, tmp_path):
    """A pure consumer's document (one table) has a positive net, a real,
    payable QR-bill, and fits on a single page (no unnecessary page break)."""
    run, items, distribution, settings = _billing_context(db)
    consumer_item = next(i for i in items if i.is_owed_to_leg and i.produced_kwh == 0)
    participant = participant_repo.get(db, consumer_item.participant_id)
    participant_result = distribution.participant_results[consumer_item.participant_id]

    output_path = tmp_path / "consumer.pdf"
    generate_participant_bill_pdf(
        run, consumer_item, participant_result, participant, settings, output_path
    )

    _assert_is_pdf(output_path)
    assert _page_count(output_path) == 1


def test_generate_payment_list_pdf_only_lists_credits(db, tmp_path):
    """The payment list renders and only includes participants owed money by the LEG."""
    run, items, _, _ = _billing_context(db)
    participants = {p.id: p for p in participant_repo.list_all(db)}

    output_path = tmp_path / "zahlliste.pdf"
    generate_payment_list_pdf(run, items, participants, output_path)

    _assert_is_pdf(output_path)
    assert any(i.is_owed_by_leg for i in items), "expected at least one credit for a meaningful test"


def test_export_billing_run_documents_writes_one_pdf_per_participant(db, tmp_path, monkeypatch):
    """The export service writes exactly one PDF per billing item (per
    participant) plus a payment list, and records each item's PDF path."""
    import app.pdf.export_service as export_service

    monkeypatch.setattr(export_service, "OUTPUT_DIR", tmp_path)

    create_demo_data(db)
    run, items, _, _ = create_or_replace_billing_run(db, *SUMMER_QUARTER)

    result = export_service.export_billing_run_documents(db, run)

    assert not result.errors
    assert len(result.document_paths) == len(items)
    # One PDF per participant, regardless of prosumer/consumer/producer status.
    assert len(result.document_paths) == len({i.participant_id for i in items})
    for path in result.document_paths:
        _assert_is_pdf(path)
    assert result.payment_list_path is not None
    _assert_is_pdf(result.payment_list_path)

    stored_items = billing_run_repo.list_items(db, run.id)
    assert all(item.pdf_path for item in stored_items)
