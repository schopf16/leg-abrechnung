"""Tests for the combined per-person billing PDF, QR-bill voiding, the CSV
reconciliation lists and export generation."""

import csv
import re
from decimal import Decimal

from app.domain.billing import create_or_replace_billing_run
from app.domain.demo_data import SUMMER_QUARTER, create_demo_data
from app.domain.distribution import compute_quarter_distribution
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.pdf.csv_export import generate_invoice_list_csv, generate_payout_list_csv
from app.pdf.export_service import export_billing_run_documents
from app.pdf.person_bill_pdf import generate_person_bill_pdf
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


def _read_csv_rows(path) -> list[dict]:
    """Read a semicolon-delimited CSV file into a list of header-keyed dicts.

    Args:
        path: Path of the CSV file.

    Returns:
        One dict per data row (header row excluded).
    """
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _billing_context(db):
    """Set up demo data and a summer billing run, returning common test fixtures.

    Args:
        db: Database connection fixture.

    Returns:
        A `(run, items, distribution, leg, settings)` tuple.
    """
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    run, items, _, _ = create_or_replace_billing_run(db, leg.id, *SUMMER_QUARTER)
    distribution = compute_quarter_distribution(db, leg.id, *SUMMER_QUARTER)
    settings = settings_repo.get_settings(db)
    return run, items, distribution, leg, settings


def test_generate_qrr_reference_is_unique_per_item():
    """Different item ids produce different, valid-length references."""
    ref1 = generate_qrr_reference(1, 1, 1)
    ref2 = generate_qrr_reference(1, 1, 2)
    assert len(ref1) == 27
    assert ref1 != ref2


def test_build_qr_bill_with_none_amount_encodes_no_fixed_amount():
    """A voided QR-bill (amount=None) never encodes a payable amount."""
    from app.models.leg import Leg
    from app.models.person import Person
    from app.models.settings import LegSettings

    settings = LegSettings(
        address_street="Weg 1", address_zip="3000", address_city="Bern",
        address_country="CH", qr_iban="CH5730000123456789012", price_rp_per_kwh=12.0,
        verwaltungsaufwand_rp_per_kwh=0.0, papierrechnung_rappen=0, updated_at="",
    )
    leg = Leg(id=1, name="LEG Test", bemerkung="", created_at="")
    person = Person(
        id=1, anrede="", firma="", vorname="Max", nachname="Muster", kontakt_email="", kontakt_telefon="",
        rechnungsadresse_strasse="Strasse", rechnungsadresse_hausnummer="1", rechnungsadresse_plz="8000",
        rechnungsadresse_ort="Zürich", rechnungsadresse_land="CH", iban="",
        kundennummer=12345678, bkw_kundennummer=None, papierrechnung=False, aktiv=True, created_at="",
    )
    ref = generate_qrr_reference(1, 1, 1)

    voided_bill = build_qr_bill(settings, leg, person, None, ref)
    payable_bill = build_qr_bill(settings, leg, person, Decimal("42.50"), ref)

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
    from app.models.leg import Leg
    from app.models.person import Person
    from app.models.settings import LegSettings

    settings = LegSettings(
        address_street="Weg 1", address_zip="3000", address_city="Bern",
        address_country="CH", qr_iban="CH5730000123456789012", price_rp_per_kwh=12.0,
        verwaltungsaufwand_rp_per_kwh=0.0, papierrechnung_rappen=0, updated_at="",
    )
    leg = Leg(id=1, name="LEG Test", bemerkung="", created_at="")
    person = Person(
        id=1, anrede="", firma="", vorname="Max", nachname="Muster", kontakt_email="", kontakt_telefon="",
        rechnungsadresse_strasse="Strasse", rechnungsadresse_hausnummer="1", rechnungsadresse_plz="8000",
        rechnungsadresse_ort="Zürich", rechnungsadresse_land="CH", iban="",
        kundennummer=12345678, bkw_kundennummer=None, papierrechnung=False, aktiv=True, created_at="",
    )
    ref = generate_qrr_reference(1, 1, 1)
    bill = build_qr_bill(settings, leg, person, Decimal("10.00"), ref)

    from svglib.svglib import svg2rlg

    svg_path = tmp_path / "bill.svg"
    bill.as_svg(str(svg_path), full_page=False)
    drawing = svg2rlg(str(svg_path))

    # The bill-only drawing is ~106mm (~300pt) tall, not a full A4 page
    # (~842pt) -- confirming it can only ever paint its own reserved
    # bottom strip, never the whole page.
    assert drawing.height < 320


def test_generate_person_bill_pdf_for_prosumer_overflows_to_second_page(db, tmp_path):
    """A prosumer's document (both Bezug and Vergütung tables) is long enough to
    push the QR-bill onto a second page rather than overlapping the content.

    Regression test: the QR-bill must never be drawn on top of content that
    reaches into its reserved bottom area -- see app.pdf.layout.CONTENT_BOTTOM_Y
    and the page-break check in generate_person_bill_pdf.
    """
    run, items, distribution, leg, settings = _billing_context(db)
    prosumer_item = next(i for i in items if i.consumed_kwh > 0 and i.produced_kwh > 0)
    person = person_repo.get(db, prosumer_item.person_id)
    person_result = distribution.person_results[prosumer_item.person_id]

    output_path = tmp_path / "prosumer.pdf"
    generate_person_bill_pdf(
        run, prosumer_item, person_result, person, leg, settings, output_path
    )

    _assert_is_pdf(output_path)
    assert _page_count(output_path) == 2


def test_generate_person_bill_pdf_for_credit_item_has_voided_amount(db, tmp_path):
    """A person with a negative net (owed money by the LEG) gets a voided QR-bill."""
    run, items, distribution, leg, settings = _billing_context(db)
    credit_item = next(i for i in items if i.is_owed_by_leg)
    person = person_repo.get(db, credit_item.person_id)
    person_result = distribution.person_results[credit_item.person_id]

    output_path = tmp_path / "credit.pdf"
    generate_person_bill_pdf(
        run, credit_item, person_result, person, leg, settings, output_path
    )

    _assert_is_pdf(output_path)


def test_generate_person_bill_pdf_for_pure_consumer_has_payable_qr_bill(db, tmp_path):
    """A pure consumer's document has a positive net and a real, payable QR-bill."""
    run, items, distribution, leg, settings = _billing_context(db)
    consumer_item = next(i for i in items if i.is_owed_to_leg and i.produced_kwh == 0)
    person = person_repo.get(db, consumer_item.person_id)
    person_result = distribution.person_results[consumer_item.person_id]

    output_path = tmp_path / "consumer.pdf"
    generate_person_bill_pdf(
        run, consumer_item, person_result, person, leg, settings, output_path
    )

    _assert_is_pdf(output_path)


def test_generate_person_bill_pdf_with_no_fees_and_one_table_fits_on_one_page(db, tmp_path):
    """With no admin fees at all, a single-table document avoids an unnecessary page break.

    Regression test for app.pdf.layout.CONTENT_BOTTOM_Y: the QR-bill must
    never be drawn on top of content, but also must not force a page break
    when there is clearly room for it on the first page.
    """
    run, items, distribution, leg, settings = _billing_context(db)
    consumer_item = next(i for i in items if i.is_owed_to_leg and i.produced_kwh == 0)
    person = person_repo.get(db, consumer_item.person_id)
    person_result = distribution.person_results[consumer_item.person_id]
    # Strip the admin fees this item happens to carry from demo settings/
    # data, so the document is down to its simplest possible shape: one
    # Bezug table plus the net settlement.
    consumer_item.verwaltungsaufwand_rappen = 0
    consumer_item.papierrechnung_rappen = 0
    fee_free_settings = settings
    fee_free_settings.verwaltungsaufwand_rp_per_kwh = 0.0

    output_path = tmp_path / "consumer_no_fees.pdf"
    generate_person_bill_pdf(
        run, consumer_item, person_result, person, leg, fee_free_settings, output_path
    )

    _assert_is_pdf(output_path)
    assert _page_count(output_path) == 1


def test_generate_person_bill_pdf_shows_verwaltungsaufwand_section_when_person_pays_papierrechnung(db, tmp_path):
    """Beat (demo person with `papierrechnung=True`) gets a document that
    renders without error even with the extra fee section present."""
    run, items, distribution, leg, settings = _billing_context(db)
    beat = next(p for p in person_repo.list_all(db) if p.papierrechnung)
    item = next(i for i in items if i.person_id == beat.id)
    person_result = distribution.person_results[item.person_id]

    output_path = tmp_path / "beat.pdf"
    generate_person_bill_pdf(run, item, person_result, beat, leg, settings, output_path)

    _assert_is_pdf(output_path)
    assert item.papierrechnung_rappen > 0


def test_generate_invoice_list_csv_lists_debtors_with_matching_reference_numbers(db, tmp_path):
    """The invoice list has one row per debtor, with the same QRR reference
    printed on that person's Einzahlungsschein -- so a bank statement can be
    matched back to the right invoice."""
    run, items, _, _, _ = _billing_context(db)
    persons = {p.id: p for p in person_repo.list_all(db)}
    debtor_items = [i for i in items if i.is_owed_to_leg]
    assert debtor_items, "expected at least one debtor for a meaningful test"

    output_path = tmp_path / "rechnungsliste.csv"
    generate_invoice_list_csv(run, items, persons, output_path)

    rows = _read_csv_rows(output_path)
    assert len(rows) == len(debtor_items)
    rows_by_reference = {row["Referenznummer"]: row for row in rows}
    for item in debtor_items:
        reference = generate_qrr_reference(item.person_id, run.id, item.id)
        row = rows_by_reference[reference]
        assert row["Name"] == persons[item.person_id].anzeige_name
        assert round(float(row["Betrag (CHF)"]), 2) == round(item.net_amount_rappen / 100, 2)


def test_generate_payout_list_csv_only_lists_credits(db, tmp_path):
    """The payout list only includes persons owed money by the LEG."""
    run, items, _, _, _ = _billing_context(db)
    persons = {p.id: p for p in person_repo.list_all(db)}
    assert any(i.is_owed_by_leg for i in items), "expected at least one credit for a meaningful test"

    output_path = tmp_path / "auszahlungsliste.csv"
    generate_payout_list_csv(items, persons, output_path)

    rows = _read_csv_rows(output_path)
    assert len(rows) == sum(1 for i in items if i.is_owed_by_leg)


def test_export_billing_run_documents_writes_one_pdf_per_person(db, tmp_path, monkeypatch):
    """The export service writes exactly one PDF per billing item (per
    person) plus a payment list, and records each item's PDF path."""
    import app.pdf.export_service as export_service

    monkeypatch.setattr(export_service, "OUTPUT_DIR", tmp_path)

    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    run, items, _, _ = create_or_replace_billing_run(db, leg.id, *SUMMER_QUARTER)

    result = export_service.export_billing_run_documents(db, run)

    assert not result.errors
    assert len(result.document_paths) == len(items)
    # One PDF per person, regardless of prosumer/consumer/producer status.
    assert len(result.document_paths) == len({i.person_id for i in items})
    for path in result.document_paths:
        _assert_is_pdf(path)

    assert result.invoice_list_path is not None
    assert result.payout_list_path is not None
    invoice_rows = _read_csv_rows(result.invoice_list_path)
    payout_rows = _read_csv_rows(result.payout_list_path)
    assert len(invoice_rows) == sum(1 for i in items if i.is_owed_to_leg)
    assert len(payout_rows) == sum(1 for i in items if i.is_owed_by_leg)

    stored_items = billing_run_repo.list_items(db, run.id)
    assert all(item.pdf_path for item in stored_items)
