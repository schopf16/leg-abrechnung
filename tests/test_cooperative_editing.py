"""Tests for editing a Genossenschaft membership from the Person edit dialog.

Where this lives is the point. The first build put entry, exit and share
changes on the person's **detail** page, and the administrator rejected it
in one sentence: the eye is for looking, the pencil is for changing, and
joining or buying shares is a change. So the controls sit in the edit
dialog, and the detail page only shows the history.

The other point is that three plain controls have to produce correct period
bookkeeping underneath -- a share purchase must not overwrite the old
figure, because a cooperative has to be able to state what somebody held on
a given day.
"""

from datetime import date, timedelta

from nicegui import Client, ui

from app.db.connection import connection_scope
from app.gui.person_form import open_person_form
from app.models import cooperative_membership as coop_repo
from app.models import person as person_repo
from app.models.cooperative_membership import CooperativeMembership
from app.models.person import Person


def _person(connection, last_name: str = "Muster") -> int:
    """Create a person to hang a membership on.

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
            salutation="Frau",
            company="",
            first_name="Anna",
            last_name=last_name,
            contact_email="anna@example.invalid",
            contact_phone="",
            billing_street="Fischrain",
            billing_house_number="68",
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


def _open_edit(person_id: int, probe: str) -> Client:
    """Open the Person edit dialog for one person.

    Args:
        person_id: The person to edit.
        probe: A unique probe route.

    Returns:
        The client holding the rendered dialog.
    """
    with connection_scope() as connection:
        person = person_repo.get(connection, person_id)
    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        open_person_form(existing=person)
    return client


def _element(client: Client, class_name: str, text: str):
    """Find one element by class name and caption.

    Args:
        client: The client to search.
        class_name: NiceGUI element class name, e.g. `"Checkbox"`.
        text: The caption to match.

    Returns:
        The matching element.
    """
    matches = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == class_name and getattr(element, "text", None) == text
    ]
    assert len(matches) == 1, f"{class_name} {text!r} nicht eindeutig: {len(matches)}"
    return matches[0]


def _input(client: Client, label: str):
    """Find one input or number field by its label.

    Args:
        client: The client to search.
        label: The German field label.

    Returns:
        The matching element.
    """
    matches = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ in ("Input", "Number") and element._props.get("label") == label
    ]
    assert len(matches) == 1, f"Feld {label!r} nicht eindeutig: {len(matches)}"
    return matches[0]


def _save(client: Client) -> None:
    """Press the dialog's Speichern button.

    Args:
        client: The client holding the dialog.

    Returns:
        None.
    """
    buttons = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Button" and element._props.get("label") == "Speichern"
    ]
    assert len(buttons) == 1, "genau einen Speichern-Knopf erwartet"
    for listener in buttons[0]._event_listeners.values():
        if listener.type == "click":
            listener.handler(None)
            return
    raise AssertionError("Speichern hat keinen Click-Handler")


def _edit(person_id: int, probe: str, *, member=None, shares=None, effective=None) -> None:
    """Open the edit dialog, set the Genossenschaft controls and save.

    One call per visit to the dialog, so a sequence of steps reads as the
    sequence the administrator actually performs.

    Args:
        person_id: The person to edit.
        probe: A unique probe route.
        member: New value for the membership checkbox, or `None` to leave it.
        shares: New share count, or `None` to leave it.
        effective: Date the change takes effect (ISO), or `None` for today.

    Returns:
        None.
    """
    client = _open_edit(person_id, probe)
    if member is not None:
        _element(client, "Checkbox", "Genossenschaftsmitglied").value = member
    if shares is not None:
        _input(client, "Anzahl Anteile").value = shares
    if effective is not None:
        _input(client, "Änderung gültig ab").value = effective
    _save(client)


def test_the_controls_are_in_the_edit_dialog():
    """The pencil, not the eye -- the correction that prompted this design."""
    with connection_scope() as connection:
        person_id = _person(connection)

    client = _open_edit(person_id, "/probe-coop-edit-present")

    assert _element(client, "Checkbox", "Genossenschaftsmitglied") is not None
    assert _input(client, "Anzahl Anteile") is not None
    assert _input(client, "Änderung gültig ab") is not None


def test_the_detail_page_only_shows_the_history():
    """A view must not carry buttons that change things."""
    from app.gui.pages import persons as persons_module

    with connection_scope() as connection:
        person_id = _person(connection)
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=5,
                valid_from=date(2026, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )

    client = Client(ui.page("/probe-coop-detail-readonly")(lambda: None), request=None)
    with client:
        persons_module.person_detail_page(person_id)

    texts = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ in ("Label", "Badge") and getattr(element, "text", None)
    ]
    buttons = [
        element._props.get("label")
        for element in client.elements.values()
        if element.__class__.__name__ == "Button"
    ]

    assert any("Mitglied mit 5 Anteil(en)" in text for text in texts), "der Stand muss sichtbar sein"
    assert "+ Änderung" not in buttons, "die Ansicht darf nichts ändern können"


def test_joining_opens_a_membership():
    """Ticking the box and saving makes somebody a member from that day."""
    with connection_scope() as connection:
        person_id = _person(connection)

    client = _open_edit(person_id, "/probe-coop-join")
    _element(client, "Checkbox", "Genossenschaftsmitglied").value = True
    _input(client, "Anzahl Anteile").value = 5
    _input(client, "Änderung gültig ab").value = "2026-03-01"
    _save(client)

    with connection_scope() as connection:
        periods = coop_repo.list_for_person(connection, person_id)
        assert len(periods) == 1
        assert periods[0].shares == 5
        assert periods[0].valid_from == date(2026, 3, 1)
        assert periods[0].valid_to is None
        assert coop_repo.shares_for_person(connection, person_id, date(2026, 2, 28)) == 0
        assert coop_repo.shares_for_person(connection, person_id, date(2026, 3, 1)) == 5


def test_buying_shares_keeps_the_previous_figure_answerable():
    """The reason this is a history and not a number.

    Raising the count must close the running period and open a new one, so
    "how many did they hold in March" still has an answer afterwards.
    """
    with connection_scope() as connection:
        person_id = _person(connection)
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=5,
                valid_from=date(2026, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )

    client = _open_edit(person_id, "/probe-coop-buy")
    _input(client, "Anzahl Anteile").value = 12
    _input(client, "Änderung gültig ab").value = "2026-07-01"
    _save(client)

    with connection_scope() as connection:
        periods = sorted(coop_repo.list_for_person(connection, person_id), key=lambda m: m.valid_from)
        assert len(periods) == 2
        assert (periods[0].shares, periods[0].valid_from, periods[0].valid_to) == (
            5,
            date(2026, 1, 1),
            date(2026, 6, 30),
        ), "der alte Zeitraum endet am Tag davor"
        assert (periods[1].shares, periods[1].valid_from, periods[1].valid_to) == (
            12,
            date(2026, 7, 1),
            None,
        )
        assert coop_repo.shares_for_person(connection, person_id, date(2026, 3, 15)) == 5
        assert coop_repo.shares_for_person(connection, person_id, date(2026, 8, 15)) == 12


def test_leaving_closes_the_period_the_day_before_it_takes_effect():
    """An exit is a date, not a deletion -- the membership still happened.

    The date is the first day of *not* being a member, the same reading the
    join and the share change use, so the period ends the day before.
    Ending it *on* the date left somebody counted as a member for the rest
    of that day -- which is what the administrator reported.
    """
    with connection_scope() as connection:
        person_id = _person(connection)
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=8,
                valid_from=date(2024, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )

    client = _open_edit(person_id, "/probe-coop-leave")
    _element(client, "Checkbox", "Genossenschaftsmitglied").value = False
    _input(client, "Änderung gültig ab").value = "2026-06-30"
    _save(client)

    with connection_scope() as connection:
        periods = coop_repo.list_for_person(connection, person_id)
        assert len(periods) == 1, "der Zeitraum bleibt bestehen"
        assert periods[0].valid_to == date(2026, 6, 29), "der Tag davor"
        assert coop_repo.shares_for_person(connection, person_id, date(2026, 6, 29)) == 8
        assert coop_repo.current_for_person(connection, person_id, date(2026, 6, 30)) is None
        assert person_id not in coop_repo.member_person_ids(connection, date(2026, 6, 30))


def test_correcting_on_the_start_day_does_not_open_a_second_period():
    """A typo fixed the same day it started is a correction, not a change."""
    with connection_scope() as connection:
        person_id = _person(connection)
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=50,
                valid_from=date(2026, 1, 1),
                valid_to=None,
                created_at="",
            ),
        )

    client = _open_edit(person_id, "/probe-coop-correct")
    _input(client, "Anzahl Anteile").value = 5
    _input(client, "Änderung gültig ab").value = "2026-01-01"
    _save(client)

    with connection_scope() as connection:
        periods = coop_repo.list_for_person(connection, person_id)
        assert len(periods) == 1, "kein Zeitraum von null Tagen"
        assert periods[0].shares == 5


def test_a_date_before_the_running_period_is_refused():
    """Otherwise the change would silently produce an overlapping history."""
    with connection_scope() as connection:
        person_id = _person(connection)
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=5,
                valid_from=date(2026, 6, 1),
                valid_to=None,
                created_at="",
            ),
        )

    client = _open_edit(person_id, "/probe-coop-backdate")
    _input(client, "Anzahl Anteile").value = 9
    _input(client, "Änderung gültig ab").value = "2026-01-01"
    _save(client)

    with connection_scope() as connection:
        periods = coop_repo.list_for_person(connection, person_id)
        assert len(periods) == 1, "nichts darf gespeichert worden sein"
        assert periods[0].shares == 5

    errors = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and "Genossenschaft:" in (element.text or "")
    ]
    assert errors, "der Dialog muss sagen, warum nichts passiert ist"


def test_saving_a_person_without_touching_the_membership_changes_nothing():
    """The commonest save of all must not disturb the history."""
    with connection_scope() as connection:
        person_id = _person(connection)
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=7,
                valid_from=date.today() - timedelta(days=100),
                valid_to=None,
                created_at="",
            ),
        )

    client = _open_edit(person_id, "/probe-coop-untouched")
    _save(client)

    with connection_scope() as connection:
        periods = coop_repo.list_for_person(connection, person_id)
        assert len(periods) == 1
        assert periods[0].shares == 7
        assert periods[0].valid_to is None


def test_a_new_person_says_the_membership_comes_after_saving():
    """A membership cannot hang on a person who does not exist yet."""
    client = Client(ui.page("/probe-coop-new")(lambda: None), request=None)
    with client:
        open_person_form()

    texts = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", None)
    ]
    assert any("Nach dem Speichern" in text for text in texts)


def test_the_administrators_own_three_steps(db):
    """Aktivieren, 10 Anteile geben, deaktivieren -- alles am selben Tag.

    Reported from real use: after these three steps the person was still
    badged as a Genossenschafter. The exit wrote `valid_to = heute`, and
    `covers()` counts that day, so they stayed a member for the rest of it
    -- while the administrator had just removed them.

    The date means the same thing in all three actions now: the day the new
    state takes effect. Ending a membership on the day it began leaves it
    covering no day at all, so there was none.
    """
    with connection_scope() as connection:
        person_id = _person(connection)

    _edit(person_id, "/probe-three-steps-1", member=True)
    _edit(person_id, "/probe-three-steps-2", member=True, shares=10)

    with connection_scope() as connection:
        periods = coop_repo.list_for_person(connection, person_id)
        assert len(periods) == 1, "derselbe Tag ist eine Korrektur, kein zweiter Zeitraum"
        assert periods[0].shares == 10

    _edit(person_id, "/probe-three-steps-3", member=False)

    with connection_scope() as connection:
        assert coop_repo.list_for_person(connection, person_id) == []
        assert coop_repo.current_for_person(connection, person_id) is None
        assert person_id not in coop_repo.member_person_ids(connection)


def test_the_badge_disappears_from_the_list_after_deactivating(db):
    """What the administrator actually looked at: the card in the Personen list."""
    from app.gui.pages import persons as persons_module

    with connection_scope() as connection:
        person_id = _person(connection)

    _edit(person_id, "/probe-badge-on", member=True, shares=10)
    client = Client(ui.page("/probe-badge-list-on")(lambda: None), request=None)
    with client:
        persons_module.persons_page()
    badges = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Badge" and getattr(element, "text", None)
    ]
    assert any("Genossenschafter" in text for text in badges), "erst da"

    _edit(person_id, "/probe-badge-off", member=False)
    client = Client(ui.page("/probe-badge-list-off")(lambda: None), request=None)
    with client:
        persons_module.persons_page()
    badges = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Badge" and getattr(element, "text", None)
    ]
    assert not any("Genossenschafter" in text for text in badges), "und dann weg"
