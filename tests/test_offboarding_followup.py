"""Tests for the gap that let a fully offboarded member stay active.

Found in the real database: all four Austritt steps dated, the person still
`active = 1`, still under Personen weeks later. The removal offer exists
(`app.gui.offboarding_form.open_remove_person_dialog`) but comes exactly
once, and the finished tracker drops off the Austritte worklist, so nothing
mentions the person again. These tests are that second chance.
"""

from datetime import date

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.quality_checks import check_offboarding_completed_but_active
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models import person_offboarding as person_offboarding_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person


def _person(connection, last_name: str = "Wyder") -> int:
    """Create a person with no dependent rows.

    Args:
        connection: Open SQLite connection.
        last_name: Their surname.

    Returns:
        The new person's id.
    """
    return person_repo.create(
        connection,
        Person(
            id=None,
            salutation="Herr",
            company="",
            first_name="Daniel",
            last_name=last_name,
            contact_email="daniel@example.invalid",
            contact_phone="",
            billing_street="Ittigenstrasse",
            billing_house_number="37",
            billing_postal_code="3063",
            billing_city="Ittigen",
            billing_country="CH",
            iban="",
            customer_number=None,
            bkw_customer_number=None,
            paper_invoice=False,
            active=True,
            created_at="",
        ),
    )


def _complete_offboarding(connection, person_id: int) -> None:
    """Walk one person's offboarding through all four steps.

    Args:
        connection: Open SQLite connection.
        person_id: The person being offboarded.

    Returns:
        None.
    """
    offboarding = person_offboarding_repo.start_for_person(
        connection, person_id, reason="voluntary", decided_at=date(2026, 9, 13)
    )
    offboarding.metering_point_exit_at = date(2026, 9, 13)
    offboarding.bkw_informed_at = date(2026, 9, 13)
    offboarding.person_confirmed_at = date(2026, 9, 13)
    person_offboarding_repo.update(connection, offboarding)
    assert person_offboarding_repo.get(connection, offboarding.id).is_complete


def test_a_finished_offboarding_with_an_active_person_is_reported(db):
    """The warning that did not exist when this happened for real."""
    person_id = _person(db)
    _complete_offboarding(db, person_id)

    warnings = check_offboarding_completed_but_active(db)
    assert len(warnings) == 1
    assert "Daniel Wyder" in warnings[0].message
    assert warnings[0].link == f"/persons/{person_id}"


def test_the_report_stops_once_the_person_is_gone(db):
    """Nothing references them, so `delete` removes them outright."""
    person_id = _person(db)
    _complete_offboarding(db, person_id)

    assert person_repo.delete(db, person_id) is True
    assert check_offboarding_completed_but_active(db) == []


def test_the_report_stops_once_the_person_is_deactivated(db):
    """Deactivating is the other acceptable outcome, and also silences it."""
    person_id = _person(db)
    _complete_offboarding(db, person_id)

    person_repo.set_active(db, person_id, False)
    assert check_offboarding_completed_but_active(db) == []


def test_an_unfinished_offboarding_is_not_reported(db):
    """A process still running is the Austritte page's business, not this."""
    person_id = _person(db)
    person_offboarding_repo.start_for_person(db, person_id, reason="voluntary", decided_at=date(2026, 9, 13))

    assert check_offboarding_completed_but_active(db) == []


def test_a_person_with_billing_history_is_deactivated_not_deleted(db):
    """The accounting trail wins, and the date is recorded either way."""
    person_id = _person(db)
    _complete_offboarding(db, person_id)
    leg_id = leg_repo.create(
        db,
        Leg(
            id=None,
            name="LEG-Test",
            note="",
            created_at="",
            production_capacity_percent=None,
            production_capacity_recorded_at=None,
        ),
    )
    run_id = billing_run_repo.create_run(
        db,
        BillingRun(
            id=None,
            leg_id=leg_id,
            period_year=2026,
            period_quarter=2,
            created_at="",
            price_rp_per_kwh=10.0,
            status="created",
            notes="",
        ),
    )
    billing_run_repo.add_items(
        db,
        [
            BillingRunItem(
                id=None,
                billing_run_id=run_id,
                person_id=person_id,
                consumed_kwh=1.0,
                produced_kwh=0.0,
                price_rp_per_kwh=10.0,
                admin_fee_consumption_rappen=0,
                paper_invoice_rappen=0,
                net_amount_rappen=100,
                pdf_path=None,
                created_at="",
            )
        ],
    )

    assert person_repo.delete(db, person_id) is False, "die Abrechnung verhindert das Löschen"
    person = person_repo.get(db, person_id)
    assert person is not None
    assert person.active is False
    assert person.deactivated_at == date.today()
    assert person.customer_number is not None, "die Kundennummer bleibt für die Buchhaltung"
    assert check_offboarding_completed_but_active(db) == []


# --- The Austritte page keeps it visible ------------------------------


def test_the_austritte_page_shows_a_finished_offboarding_that_needs_action():
    """The regression test for the real case.

    With the default filter, a finished process used to disappear while the
    person stayed active. It has to stay, badged as unfinished business,
    with the removal one click away.
    """
    from app.gui.pages import offboardings as offboardings_module

    with connection_scope() as connection:
        _complete_offboarding(connection, _person(connection, "Wyder"))
        # A second, still-running process, so the list is not trivially
        # everything.
        person_offboarding_repo.start_for_person(
            connection, _person(connection, "Laufend"), reason="voluntary"
        )

    client = Client(ui.page("/probe-offboardings")(lambda: None), request=None)
    with client:
        offboardings_module.offboardings_page()

    texts = [
        element.text
        for element in client.elements.values()
        # Links too: the card puts the person's name in a `ui.link`.
        if element.__class__.__name__ in ("Label", "Badge", "Link") and getattr(element, "text", None)
    ]
    buttons = [
        element._props.get("label")
        for element in client.elements.values()
        if element.__class__.__name__ == "Button"
    ]

    assert any("Daniel Wyder" in text for text in texts), "der abgeschlossene Austritt muss sichtbar sein"
    assert any("Daniel Laufend" in text for text in texts)
    assert "Person noch aktiv" in texts, "und als offene Sache markiert, nicht grün abgehakt"
    assert "Person entfernen" in buttons, "mit der Handlung direkt daneben"


def test_the_austritte_page_hides_a_finished_offboarding_that_is_settled():
    """Once the person is gone, the finished process is out of the way again."""
    from app.gui.pages import offboardings as offboardings_module

    with connection_scope() as connection:
        person_id = _person(connection, "Erledigt")
        _complete_offboarding(connection, person_id)
        person_repo.set_active(connection, person_id, False)

    client = Client(ui.page("/probe-offboardings-settled")(lambda: None), request=None)
    with client:
        offboardings_module.offboardings_page()

    texts = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ in ("Label", "Badge", "Link") and getattr(element, "text", None)
    ]
    assert not any("Daniel Erledigt" in text for text in texts)
    assert "Person noch aktiv" not in texts


def test_the_two_destructive_buttons_do_not_read_alike():
    """A card can carry both, and they destroy very different things.

    Reported from first use: "Löschen" (discard the tracking) sat beside
    "Person entfernen" (remove the person), both red, and the only thing
    telling them apart was the confirmation text -- which is read after the
    click, not before it. Each label now names its own object.
    """
    from app.gui.pages import offboardings as offboardings_module

    with connection_scope() as connection:
        _complete_offboarding(connection, _person(connection, "Wyder"))

    client = Client(ui.page("/probe-offboarding-labels")(lambda: None), request=None)
    with client:
        offboardings_module.offboardings_page()

    labels = {
        element._props.get("label")
        for element in client.elements.values()
        if element.__class__.__name__ == "Button" and element._props.get("label")
    }

    assert "Austritt verwerfen" in labels
    assert "Person entfernen" in labels
    assert "Löschen" not in labels, "zwei rote Knöpfe dürfen nicht beide nur „löschen“ heissen"
