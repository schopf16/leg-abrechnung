"""Tests for the quarterly billing cycle tracker and its guided page.

The tracker mirrors `person_onboarding`; what is genuinely new is the
gate, so most of the weight here is on that: a red control point must
actually stop the computation, and stepping past it must leave a record.
"""

from datetime import date, timedelta

import pytest

from app.domain.demo_data import WINTER_QUARTER, create_demo_data
from app.models import billing_cycle as billing_cycle_repo
from app.models import metering_point as metering_point_repo
from app.models.billing_cycle import STEPS, BillingCycle

#: The quarter the billing page opens on: the newest one with readings.
DEFAULT_QUARTER = WINTER_QUARTER


def _cycle(db, year=2025, quarter=3):
    """Start a cycle for a quarter.

    Args:
        db: Database connection fixture.
        year: Calendar year.
        quarter: Quarter number.

    Returns:
        The `BillingCycle`.
    """
    return billing_cycle_repo.start_for_period(db, year, quarter)


def test_a_fresh_cycle_sits_on_its_first_step(db):
    """Nothing is done yet, so the first step is the open one."""
    cycle = _cycle(db)

    assert not cycle.is_complete
    assert cycle.current_step == STEPS[0]
    assert cycle.label == "Q3 2025"
    assert not cycle.was_overridden


def test_starting_the_same_quarter_twice_returns_the_same_cycle(db):
    """Pressing the button again must not create a second run of the quarter."""
    first = _cycle(db)
    second = _cycle(db)

    assert first.id == second.id
    assert len(billing_cycle_repo.list_all(db)) == 1


def test_a_cycle_is_complete_only_when_every_step_has_a_date(db):
    """Five of six is still unfinished -- that is the whole point."""
    cycle = _cycle(db)
    for attribute, _ in STEPS[:-1]:
        cycle = billing_cycle_repo.mark_step(db, cycle, attribute)
        assert not cycle.is_complete

    cycle = billing_cycle_repo.mark_step(db, cycle, STEPS[-1][0])

    assert cycle.is_complete
    assert cycle.current_step is None
    assert cycle.days_open() is None
    assert billing_cycle_repo.list_in_progress(db) == []


def test_marking_an_unknown_step_is_refused(db):
    """A typo in a step name must fail loudly, not write nothing."""
    cycle = _cycle(db)

    with pytest.raises(ValueError, match="Unknown billing cycle step"):
        billing_cycle_repo.mark_step(db, cycle, "posted_on_facebook_at")


def test_a_stalled_cycle_reports_how_long_it_has_been_stuck(db):
    """Days open are measured from the previous step, like the onboarding tracker."""
    cycle = _cycle(db)
    cycle = billing_cycle_repo.mark_step(db, cycle, "readings_imported_at", date(2026, 1, 10))

    assert cycle.current_step == STEPS[1]
    assert cycle.current_step_since == date(2026, 1, 10)
    assert cycle.days_open(date(2026, 1, 25)) == 15
    assert cycle.is_overdue(14, date(2026, 1, 25))
    assert not cycle.is_overdue(30, date(2026, 1, 25))


def test_an_untouched_cycle_measures_from_the_day_it_was_started(db):
    """With no step done at all there is no previous date to measure from."""
    cycle = _cycle(db)
    started = date.fromisoformat(cycle.created_at[:10])

    assert cycle.current_step_since == started
    assert cycle.days_open(started + timedelta(days=3)) == 3


def test_an_override_needs_a_reason(db):
    """An override with no stated reason is the oversight this prevents."""
    cycle = _cycle(db)

    with pytest.raises(ValueError, match="Begründung"):
        billing_cycle_repo.record_override(db, cycle, "   ")

    assert not billing_cycle_repo.get(db, cycle.id).was_overridden


def test_a_recorded_override_is_kept_verbatim(db):
    """The reason is evidence, so it is stored as written and timestamped."""
    cycle = _cycle(db)
    cycle = billing_cycle_repo.record_override(
        db, cycle, "BKW liefert für CH1000…007 dauerhaft keine Werte, Abklärung läuft."
    )

    assert cycle.was_overridden
    assert "BKW liefert" in cycle.override_reason
    assert cycle.override_at is not None
    assert billing_cycle_repo.get(db, cycle.id).override_reason == cycle.override_reason


def test_an_overdue_cycle_reaches_the_dashboard(db):
    """A stalled billing run belongs where the administrator already looks."""
    from app.domain.quality_checks import check_open_billing_cycle
    from app.models import settings as settings_repo

    settings = settings_repo.get_settings(db)
    settings.onboarding_overdue_days = 7
    settings_repo.update_settings(db, settings)

    cycle = _cycle(db)
    cycle.created_at = (date.today() - timedelta(days=30)).isoformat()
    billing_cycle_repo.update(db, cycle)
    db.execute(
        "UPDATE billing_cycle SET created_at = ? WHERE id = ?",
        ((date.today() - timedelta(days=30)).isoformat(), cycle.id),
    )
    db.commit()

    warnings = check_open_billing_cycle(db)

    assert len(warnings) == 1
    assert "Q3 2025" in warnings[0].message
    assert warnings[0].link == "/billing"


def test_a_finished_cycle_does_not_nag(db):
    """Nothing is open, so nothing is overdue."""
    from app.domain.quality_checks import check_open_billing_cycle

    cycle = _cycle(db)
    for attribute, _ in STEPS:
        cycle = billing_cycle_repo.mark_step(db, cycle, attribute, date(2020, 1, 1))

    assert check_open_billing_cycle(db) == []


# --- The click path -----------------------------------------------------
#
# The domain tests above would all pass with the page wired to nothing.
# These render the real page and press its buttons, because "the gate
# exists" and "the gate is connected" are different claims.


def _render_billing_page():
    """Render the billing page and hand back its client.

    Returns:
        The NiceGUI `Client` holding the rendered elements.
    """
    from nicegui import Client, ui

    from app.gui.pages import billing as billing_page_module

    client = Client(ui.page("/probe-cycle")(lambda: None), request=None)
    with client:
        billing_page_module.billing_page()
    return client


def _buttons(client, label_ends_with=None, label=None):
    """Find buttons by their label.

    Args:
        client: The rendered client.
        label_ends_with: Match labels ending in this.
        label: Match this exact label.

    Returns:
        The matching button elements.
    """
    found = []
    for element in client.elements.values():
        if element.__class__.__name__ != "Button":
            continue
        text = element._props.get("label") or ""
        if label is not None and text == label:
            found.append(element)
        elif label_ends_with is not None and text.endswith(label_ends_with):
            found.append(element)
    return found


def _press(button) -> None:
    """Invoke a button's click handler.

    Args:
        button: The button to press.

    Returns:
        None.
    """
    for listener in button._event_listeners.values():
        if listener.type == "click":
            listener.handler(None)
            return
    raise AssertionError("Knopf ohne Click-Handler")


def test_a_run_can_be_started_for_a_quarter_that_has_no_readings_yet(monkeypatch, tmp_path):
    """The assistant decides the quarter; the readings come afterwards.

    Regression test for a circular design: the period was offered only
    for quarters that already had readings, while the run's first step
    was importing them -- so a run could not be started until its first
    step was long done. The quarter chosen here deliberately has no data
    at all.
    """
    from app.db.connection import connection_scope

    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    with connection_scope() as connection:
        create_demo_data(connection)
        assert billing_cycle_repo.list_all(connection) == []

    client = _render_billing_page()
    new_buttons = _buttons(client, label="+ Neuer Rechnungslauf")
    assert len(new_buttons) == 1, "die Seite muss einen Knopf für einen neuen Lauf zeigen"
    _press(new_buttons[0])

    # A quarter far from the demo data, so nothing has been imported for it.
    year_input = next(
        e
        for e in client.elements.values()
        if e.__class__.__name__ == "Number" and e._props.get("label") == "Jahr"
    )
    quarter_input = next(
        e
        for e in client.elements.values()
        if e.__class__.__name__ == "Select" and e._props.get("label") == "Quartal"
    )
    year_input.value = 2026
    quarter_input.value = 2
    _press(_buttons(client, label="Rechnungslauf starten")[0])

    with connection_scope() as connection:
        cycles = billing_cycle_repo.list_all(connection)
    assert [(c.period_year, c.period_quarter) for c in cycles] == [(2026, 2)]
    # The first step stays open, because the readings really are missing.
    assert cycles[0].readings_imported_at is None


def test_a_quarter_with_readings_ticks_its_import_step_by_itself(monkeypatch, tmp_path):
    """Whether data is there is a question the app answers, not the user.

    Asking for a manual confirmation of something observable only invites
    a tick that is not true.
    """
    from app.db.connection import connection_scope

    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    with connection_scope() as connection:
        create_demo_data(connection)
        cycle = billing_cycle_repo.start_for_period(connection, *DEFAULT_QUARTER)
        assert cycle.readings_imported_at is None

    _render_billing_page()

    with connection_scope() as connection:
        refreshed = billing_cycle_repo.get_by_period(connection, *DEFAULT_QUARTER)
    assert refreshed.readings_imported_at is not None
    assert refreshed.current_step[0] == "readings_checked_at"


def test_without_readings_nothing_beyond_the_import_can_be_done(monkeypatch, tmp_path):
    """A run for an empty quarter may exist, but must not compute anything.

    Uses a quarter *before* anyone was assigned, so the control points
    have nothing to object to and all come out green -- the readings
    guard is then the only thing standing between an empty quarter and a
    pointless run over it. A quarter that merely lacks an import would
    have been stopped by the control points anyway, and would have tested
    nothing.
    """
    from app.db.connection import connection_scope
    from app.domain.billing_checks import control_points_passed, run_control_points

    empty_quarter = (2024, 1)
    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    with connection_scope() as connection:
        create_demo_data(connection)
        billing_cycle_repo.start_for_period(connection, *empty_quarter)
        points = run_control_points(connection, *empty_quarter)
    assert control_points_passed(points), (
        "Testaufbau: die Kontrollpunkte müssen grün sein, sonst prüft dieser Test sie statt der Sperre"
    )

    client = _render_billing_page()

    compute = _buttons(client, label="Abrechnung erstellen (alle LEGs)")
    assert len(compute) == 1
    assert _is_disabled(compute[0]), "ohne Messdaten darf nicht gerechnet werden"
    check = _buttons(client, label="Kontrollpunkte übernehmen")
    assert len(check) == 1
    assert _is_disabled(check[0]), "ohne Messdaten gibt es nichts zu prüfen"


def test_a_failing_control_point_disables_the_compute_button(monkeypatch, tmp_path):
    """The gate has to be wired, not merely displayed.

    Deletes one metering point's readings, then checks that the button
    which would compute the quarter is actually disabled -- and that it
    becomes usable once the override is on record. Uses the quarter the
    page opens on (the newest with readings), so no selector has to be
    driven for the claim to hold.
    """
    from app.db.connection import connection_scope

    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    with connection_scope() as connection:
        create_demo_data(connection)
        billing_cycle_repo.start_for_period(connection, *DEFAULT_QUARTER)
        victim = metering_point_repo.list_all(connection)[0]
        connection.execute("DELETE FROM readings WHERE metering_point_id = ?", (victim.id,))
        connection.commit()

    def compute_button(client):
        buttons = _buttons(client, label="Abrechnung erstellen (alle LEGs)")
        assert len(buttons) == 1, "genau einen Berechnen-Knopf im Ablauf erwartet"
        return buttons[0]

    blocked = compute_button(_render_billing_page())
    assert _is_disabled(blocked), "ein roter Kontrollpunkt muss das Berechnen sperren"

    with connection_scope() as connection:
        cycle = billing_cycle_repo.get_by_period(connection, *DEFAULT_QUARTER)
        billing_cycle_repo.record_override(connection, cycle, "BKW liefert keine Werte.")

    allowed = compute_button(_render_billing_page())
    assert not _is_disabled(allowed), "nach festgehaltener Umgehung muss das Berechnen möglich sein"


def _is_disabled(button) -> bool:
    """Whether a NiceGUI button is currently disabled.

    Args:
        button: The button element.

    Returns:
        `True` if it cannot be pressed.
    """
    return bool(button._props.get("disable") or button._props.get("disabled"))


def test_the_cycle_shows_a_warning_when_the_data_changed_after_checking(monkeypatch, tmp_path):
    """A tick that no longer holds must say so rather than reassure."""
    from app.db.connection import connection_scope

    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    with connection_scope() as connection:
        create_demo_data(connection)
        cycle = billing_cycle_repo.start_for_period(connection, *DEFAULT_QUARTER)
        billing_cycle_repo.mark_step(connection, cycle, "readings_checked_at")
        victim = metering_point_repo.list_all(connection)[0]
        connection.execute("DELETE FROM readings WHERE metering_point_id = ?", (victim.id,))
        connection.commit()

    client = _render_billing_page()
    texts = [
        getattr(e, "text", "") or "" for e in client.elements.values() if e.__class__.__name__ == "Label"
    ]

    assert any("sind die Kontrollpunkte nicht mehr erfüllt" in text for text in texts), (
        "die Seite muss melden, dass sich die Messdaten seit der Prüfung geändert haben"
    )


def test_the_cycle_survives_a_round_trip_through_the_database(db):
    """Dates and the override note come back exactly as they went in."""
    cycle = _cycle(db)
    cycle = billing_cycle_repo.mark_step(db, cycle, "readings_imported_at", date(2026, 2, 3))
    cycle = billing_cycle_repo.record_override(db, cycle, "Zwei Zähler defekt.")

    reloaded = billing_cycle_repo.get_by_period(db, 2025, 3)

    assert isinstance(reloaded, BillingCycle)
    assert reloaded.readings_imported_at == date(2026, 2, 3)
    assert reloaded.override_reason == "Zwei Zähler defekt."


def test_the_check_step_cannot_be_ticked_while_a_control_point_is_red(monkeypatch, tmp_path):
    """ "Messdaten geprüft" means the data was found sound, so it needs sound data.

    Found by walking the flow: the button was pressable on red data, which
    recorded a date that read as "checked and fine" -- and the page then
    told the user the readings must have changed since, which was untrue.
    The red case has its own route, the recorded override.
    """
    from app.db.connection import connection_scope

    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    with connection_scope() as connection:
        create_demo_data(connection)
        billing_cycle_repo.start_for_period(connection, *DEFAULT_QUARTER)
        victim = metering_point_repo.list_all(connection)[0]
        connection.execute("DELETE FROM readings WHERE metering_point_id = ?", (victim.id,))
        connection.commit()

    client = _render_billing_page()
    check = _buttons(client, label="Kontrollpunkte übernehmen")
    assert len(check) == 1
    assert _is_disabled(check[0]), "ein roter Kontrollpunkt darf nicht als geprüft gelten"

    with connection_scope() as connection:
        cycle = billing_cycle_repo.get_by_period(connection, *DEFAULT_QUARTER)
        billing_cycle_repo.record_override(connection, cycle, "BKW liefert nichts.")

    client = _render_billing_page()
    check = _buttons(client, label="Kontrollpunkte übernehmen")
    assert not _is_disabled(check[0]), "mit festgehaltener Umgehung muss der Schritt möglich sein"


def test_an_overridden_run_is_not_announced_as_blocked(monkeypatch, tmp_path):
    """Saying "gesperrt" beside a working button reads as a malfunction."""
    from app.db.connection import connection_scope

    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    with connection_scope() as connection:
        create_demo_data(connection)
        cycle = billing_cycle_repo.start_for_period(connection, *DEFAULT_QUARTER)
        victim = metering_point_repo.list_all(connection)[0]
        connection.execute("DELETE FROM readings WHERE metering_point_id = ?", (victim.id,))
        connection.commit()
        billing_cycle_repo.record_override(connection, cycle, "BKW liefert nichts.")

    client = _render_billing_page()
    texts = [
        getattr(e, "text", "") or "" for e in client.elements.values() if e.__class__.__name__ == "Label"
    ]

    assert not any("Abrechnung gesperrt" in text for text in texts)
    assert any("bewusst umgangen" in text for text in texts)
    # And the stale-data warning must stay away: nothing changed, the
    # control points were never green in the first place.
    assert not any("nicht mehr erfüllt" in text for text in texts)
