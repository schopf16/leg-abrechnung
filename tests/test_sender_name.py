"""A sender name and c/o line above every LEG's name, for return post and the QR-bill payee."""

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from nicegui import Client, ui
from pypdf import PdfReader

from app.db.connection import connection_scope
from app.db.schema import initialize_database
from app.domain import dunning
from app.domain.billing import create_or_replace_billing_run
from app.domain.demo_data import SUMMER_QUARTER, create_demo_data
from app.domain.distribution import compute_quarter_distribution
from app.export.data_export import tables_by_name
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person
from app.pdf.dunning_pdf import generate_dunning_pdf
from app.pdf.export_service import _load_metering_point_info
from app.pdf.layout import draw_sender_block, leg_meta_lines
from app.pdf.person_bill_pdf import generate_person_bill_pdf
from app.pdf.qr_bill_render import QR_NAME_MAX_LENGTH, build_qr_bill
from app.pdf.qr_reference import generate_qrr_reference

ORG_NAME = "LEG Association"
CARE_OF = "c/o Max Muster"


@dataclass(frozen=True)
class Scenario:
    """One deployment's sender configuration and what its documents must say."""

    name: str
    sender_name: str
    sender_addition: str
    leg_name: str

    @property
    def expected_sender(self) -> str:
        """Who the letter and the QR-bill come from."""
        return self.sender_name or self.leg_name

    @property
    def expected_letterhead(self) -> list[str]:
        """The return address, top left, line by line."""
        return [self.expected_sender, *([self.sender_addition] if self.sender_addition else [])] + [
            "Musterweg 1",
            "3000 Bern",
        ]

    @property
    def names_the_leg_separately(self) -> bool:
        """Only when the sender hides the LEG's name."""
        return bool(self.sender_name)


#: Ittigen: every LEG is its own contracting party, nothing configured.
ITTIGEN = Scenario("ittigen", "", "", "LEG Ittigen Beispiel")
#: An association sends for all its LEGs and holds the QR-IBAN account.
ASSOCIATION = Scenario("association", ORG_NAME, "", "LEG Beispiel Süd")
#: The association with a c/o line, so return post reaches a named letterbox.
ASSOCIATION_CARE_OF = Scenario("association-c/o", ORG_NAME, CARE_OF, "LEG Beispiel Süd")

SCENARIOS = [ITTIGEN, ASSOCIATION, ASSOCIATION_CARE_OF]
_IDS = [s.name for s in SCENARIOS]


def _configure(db, scenario: Scenario):
    """Settings complete enough for a QR-bill, with the scenario's sender."""
    settings = settings_repo.get_settings(db)
    settings.address_street = "Musterweg"
    settings.address_house_number = "1"
    settings.address_zip = "3000"
    settings.address_city = "Bern"
    settings.address_country = "CH"
    settings.qr_iban = "CH4431999123000889012"
    settings.sender_name = scenario.sender_name
    settings.sender_addition = scenario.sender_addition
    settings_repo.update_settings(db, settings)
    return settings_repo.get_settings(db)


def _leg(scenario: Scenario) -> Leg:
    """An unsaved LEG named as in the scenario."""
    return Leg(id=1, name=scenario.leg_name, note="", created_at="")


def _person(**overrides) -> Person:
    """An invented payer."""
    fields = dict(
        id=1,
        salutation="",
        company="",
        first_name="Anna",
        last_name="Muster",
        contact_email="",
        contact_phone="",
        billing_street="Beispielstrasse",
        billing_house_number="2",
        billing_postal_code="3000",
        billing_city="Bern",
        billing_country="CH",
        iban="",
        customer_number=12345678,
        bkw_customer_number=None,
        paper_invoice=False,
        active=True,
        created_at="",
    )
    fields.update(overrides)
    return Person(**fields)


def _sender_lines(settings, leg: Leg) -> list[str]:
    """The lines `draw_sender_block` writes."""
    canvas = MagicMock()
    draw_sender_block(canvas, settings, leg)
    return [call.args[2] for call in canvas.drawString.call_args_list]


def _pdf_lines(path) -> list[str]:
    """Every text line of a generated PDF, all pages."""
    return [line.strip() for page in PdfReader(path).pages for line in page.extract_text().splitlines()]


def _payee_name(lines: list[str]) -> str:
    """The name under the payment part's "Konto / Zahlbar an" and the IBAN.

    The last occurrence: the receipt comes first and is narrow enough to wrap a long name.
    """
    last = len(lines) - 1 - lines[::-1].index("Konto / Zahlbar an")
    return lines[last + 2]


def _assert_document(lines: list[str], scenario: Scenario) -> None:
    """What every bill and Mahnung must say about its sender, in this scenario."""
    head = lines[: len(scenario.expected_letterhead)]
    assert head == scenario.expected_letterhead
    assert _payee_name(lines) == scenario.expected_sender
    leg_line = f"LEG: {scenario.leg_name}"
    assert (leg_line in lines) == scenario.names_the_leg_separately
    if not scenario.sender_addition:
        assert not any(line.startswith("c/o") for line in lines)


# --- Storage -----------------------------------------------------------------


def test_a_new_database_has_no_sender_name(db):
    """Migration 60 starts empty, so an existing deployment's documents do not change."""
    settings = settings_repo.get_settings(db)

    assert settings.sender_name == ""
    assert settings.sender_addition == ""


def test_an_existing_database_keeps_its_letterhead_after_the_migration():
    """A database from before migration 60 gains the columns empty, so Ittigen sees no change."""
    from app.db import schema as schema_module

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    original = schema_module.MIGRATIONS
    try:
        schema_module.MIGRATIONS = [m for m in original if m.version < 60]
        initialize_database(connection)
    finally:
        schema_module.MIGRATIONS = original
    connection.execute("UPDATE leg_settings SET address_street = 'Musterweg' WHERE id = 1")
    connection.commit()

    initialize_database(connection)

    settings = settings_repo.get_settings(connection)
    assert (settings.sender_name, settings.sender_addition) == ("", "")
    assert settings.address_street == "Musterweg"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_IDS)
def test_the_sender_is_stored(db, scenario):
    """Round trip through `update_settings`."""
    stored = _configure(db, scenario)

    assert stored.sender_name == scenario.sender_name
    assert stored.sender_addition == scenario.sender_addition


def test_the_export_carries_both_columns():
    """The data export is a contract; a new settings column belongs in it."""
    columns = [c.name for c in tables_by_name()["settings"].columns]

    assert "sender_name" in columns
    assert "sender_addition" in columns


# --- The pieces ----------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_IDS)
def test_the_letterhead(db, scenario):
    """Sender, then the c/o line if any, then the address -- no blank line for a missing c/o."""
    assert _sender_lines(_configure(db, scenario), _leg(scenario)) == scenario.expected_letterhead


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_IDS)
def test_the_leg_line(db, scenario):
    """Members still see which LEG they are billed in, once the sender no longer says it."""
    lines = leg_meta_lines(_configure(db, scenario), _leg(scenario))

    assert lines == ([f"LEG: {scenario.leg_name}"] if scenario.names_the_leg_separately else [])


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_IDS)
def test_the_qr_payee(db, scenario):
    """The payee should be the holder of the QR-IBAN account; the c/o line is not part of it."""
    bill = build_qr_bill(
        _configure(db, scenario), _leg(scenario), _person(), Decimal("10.00"), generate_qrr_reference(1, 1, 1)
    )

    assert bill.creditor.name == scenario.expected_sender


def test_a_name_at_the_qr_limit_is_printed_in_full(db, tmp_path):
    """70 characters is allowed, and the payment part shows all of it (the receipt may wrap it)."""
    long_name = ("LEG Association – Lokale Elektrizitätsgemeinschaft " + "x" * 70)[:QR_NAME_MAX_LENGTH]
    scenario = Scenario("long", long_name, CARE_OF, "LEG Beispiel Süd")
    settings = _configure(db, scenario)
    candidate = _overdue_candidate(db, scenario)
    path = tmp_path / "lang.pdf"

    generate_dunning_pdf(db, candidate, "Guten Tag", settings, path)

    assert len(long_name) == QR_NAME_MAX_LENGTH
    _assert_document(_pdf_lines(path), scenario)


def test_a_blank_sender_name_falls_back_even_with_a_care_of(db):
    """A c/o line alone does not replace the LEG's name."""
    settings = _configure(db, Scenario("co-only", "", CARE_OF, "LEG Beispiel"))

    assert _sender_lines(settings, _leg(ITTIGEN))[:2] == ["LEG Ittigen Beispiel", CARE_OF]
    assert leg_meta_lines(settings, _leg(ITTIGEN)) == []


# --- The documents, end to end -------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_IDS)
def test_the_quarterly_bill(db, tmp_path, scenario):
    """The real bill PDF: letterhead, LEG line and the QR slip's payee."""
    create_demo_data(db)
    leg = leg_repo.list_all(db)[0]
    leg.name = scenario.leg_name
    leg_repo.update(db, leg)
    settings = _configure(db, scenario)
    run, items, _, _ = create_or_replace_billing_run(db, leg.id, *SUMMER_QUARTER)
    distribution = compute_quarter_distribution(db, leg.id, *SUMMER_QUARTER)
    item = next(i for i in items if i.is_owed_to_leg)
    item.due_date = date.today().isoformat()
    path = tmp_path / "bill.pdf"

    generate_person_bill_pdf(
        run,
        item,
        distribution.person_results[item.person_id],
        person_repo.get(db, item.person_id),
        leg_repo.get(db, leg.id),
        settings,
        path,
        metering_point_info=_load_metering_point_info(db),
    )

    _assert_document(_pdf_lines(path), scenario)


def _overdue_candidate(db, scenario: Scenario):
    """One person with one overdue invoice in a LEG named as in the scenario."""
    person_id = person_repo.create(db, _person(id=None, customer_number=None))
    leg_id = leg_repo.create(db, Leg(id=None, name=scenario.leg_name, note="", created_at=""))
    run_id = billing_run_repo.create_run(
        db,
        BillingRun(
            id=None,
            leg_id=leg_id,
            period_year=2026,
            period_quarter=1,
            created_at="",
            price_rp_per_kwh=20.0,
            status="created",
            notes="",
        ),
    )
    (item_id,) = billing_run_repo.add_items(
        db,
        [
            BillingRunItem(
                id=None,
                billing_run_id=run_id,
                person_id=person_id,
                consumed_kwh=10.0,
                produced_kwh=0.0,
                price_rp_per_kwh=20.0,
                admin_fee_consumption_rappen=0,
                paper_invoice_rappen=0,
                net_amount_rappen=10_000,
                pdf_path=None,
                created_at="",
            )
        ],
    )
    billing_run_repo.set_item_due_date(db, item_id, (date.today() - timedelta(days=1)).isoformat())
    (candidate,) = dunning.list_due_dunnings(db)
    return candidate


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_IDS)
def test_the_dunning_notice(db, tmp_path, scenario):
    """The real Mahnung PDF: same letterhead, LEG line and payee as the bill."""
    settings = _configure(db, scenario)
    candidate = _overdue_candidate(db, scenario)
    path = tmp_path / "mahnung.pdf"

    generate_dunning_pdf(db, candidate, "Guten Tag", settings, path)

    _assert_document(_pdf_lines(path), scenario)


# --- The settings page -----------------------------------------------------------


def _render_settings(probe: str) -> Client:
    """Render the Allgemein page."""
    from app.gui.pages.settings import settings_page

    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        settings_page()
    return client


def _field(client: Client, label: str):
    """One input on the page, by its label."""
    return next(
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Input" and element.label == label
    )


def _click_first_save(client: Client) -> None:
    """The sender form's own Speichern, the first on the page."""
    save = next(
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Button" and element.text == "Speichern"
    )
    next(listener.handler for listener in save._event_listeners.values() if listener.type == "click")(None)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_IDS)
def test_the_page_saves_the_sender(scenario):
    """Driven through the page's own save button, not the repo; surrounding blanks are trimmed."""
    client = _render_settings(f"/probe-sender-name-save-{scenario.name.replace('/', '-')}")

    with client:
        _field(client, "Name des Absenders").value = f"  {scenario.sender_name}  "
        _field(client, "Zusatz (z. B. c/o Vorname Name)").value = scenario.sender_addition
        _click_first_save(client)

    with connection_scope() as connection:
        stored = settings_repo.get_settings(connection)
    assert stored.sender_name == scenario.sender_name
    assert stored.sender_addition == scenario.sender_addition


def test_the_page_shows_what_is_stored():
    """Reopening the page shows the saved sender, not empty fields."""
    with connection_scope() as connection:
        settings = settings_repo.get_settings(connection)
        settings.sender_name = ORG_NAME
        settings.sender_addition = CARE_OF
        settings_repo.update_settings(connection, settings)

    client = _render_settings("/probe-sender-name-reload")

    assert _field(client, "Name des Absenders").value == ORG_NAME
    assert _field(client, "Zusatz (z. B. c/o Vorname Name)").value == CARE_OF


def test_the_page_refuses_a_name_too_long_for_the_qr_bill():
    """`qrbill` would raise at billing time; say so at saving instead."""
    client = _render_settings("/probe-sender-name-too-long")

    with client:
        _field(client, "Name des Absenders").value = "x" * (QR_NAME_MAX_LENGTH + 1)
        _click_first_save(client)

    with connection_scope() as connection:
        assert settings_repo.get_settings(connection).sender_name == ""
