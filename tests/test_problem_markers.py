"""Tests for the one problem marker and filter every list uses.

The triangle carries **no text** -- it says "look at this one" and nothing
more, exactly like the eye and the pencil beside it. What is wrong is shown
by the eye and fixed by the pencil, because a table row has no space to
explain a finding. The first attempt at explaining in the list proved the
point: a name beside "Meinten Sie: Untere Zollgasse?" said neither which
field was meant nor what stood in it.

The filter exists so the marked handful can be worked off without scrolling
ninety entries, and disappears while nothing is marked -- a control that can
only ever empty the list is clutter.
"""

import pytest
from nicegui import Client, ui

from app.db.connection import connection_scope
from app.domain.quality_checks import (
    SUBJECT_METERING_POINT,
    SUBJECT_PERSON,
    QualityWarning,
    problems_for,
)
from app.gui.problem_markers import FILTER_LABEL, ProblemFilter, render_problem_notes
from app.models import person as person_repo
from app.models.person import Person


def _person(last_name: str = "Muster", street: str = "Erstweg") -> int:
    """Create a person with a billing address.

    Args:
        last_name: Their surname.
        street: Billing street.

    Returns:
        The new person's id.
    """
    with connection_scope() as connection:
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
                billing_street=street,
                billing_house_number="4",
                billing_postal_code="3048",
                billing_city="Musterdorf",
                billing_country="CH",
                iban="",
                customer_number=None,
                bkw_customer_number=None,
                paper_invoice=False,
                active=True,
                created_at="",
            ),
        )


def _persons_page() -> Client:
    """Render the Personen page.

    Returns:
        The client holding the rendered page.
    """
    from app.gui.pages import persons as persons_module

    client = Client(ui.page("/probe-problem-filter")(lambda: None), request=None)
    with client:
        persons_module.persons_page()
    return client


def _switch(client: Client):
    """The problem filter on a rendered page.

    Args:
        client: The rendered client.

    Returns:
        The switch element.
    """
    matches = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Switch" and element._props.get("label") == FILTER_LABEL
    ]
    assert len(matches) == 1, f"{len(matches)} Schalter mit {FILTER_LABEL!r}"
    return matches[0]


def _cards(client: Client) -> int:
    """How many person cards are rendered.

    Args:
        client: The rendered client.

    Returns:
        The count.
    """
    return sum(
        1
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", "") == "Anna Muster"
    )


def _markers(client: Client) -> int:
    """How many warning triangles are rendered.

    Args:
        client: The rendered client.

    Returns:
        The count.
    """
    return sum(
        1
        for element in client.elements.values()
        if element.__class__.__name__ == "Icon" and element._props.get("name") == "warning"
    )


# --- What gets marked ------------------------------------------------------


def test_a_faulty_entry_is_marked(address_register):
    """One triangle per affected entry, and none for the sound ones."""
    _person(street="Nirgendweg")
    _person(street="Erstweg")

    assert _markers(_persons_page()) == 1


def test_the_marker_carries_no_text(address_register):
    """It says "look at this one" and nothing else.

    A row cannot explain a finding, and a tooltip nobody hovers is not an
    explanation either -- the eye shows it and the pencil fixes it.
    """
    _person(street="Nirgendweg")
    client = _persons_page()

    triangles = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Icon" and element._props.get("name") == "warning"
    ]

    assert triangles
    for triangle in triangles:
        assert not getattr(triangle, "text", ""), "das Dreieck trägt keinen Text"


# --- The filter ------------------------------------------------------------


def test_the_filter_shows_only_the_marked_entries(address_register):
    """Driven rather than rendered: a switch that is drawn but not wired
    looks exactly the same on screen."""
    _person(street="Nirgendweg")
    _person(street="Erstweg")
    client = _persons_page()
    assert _cards(client) == 2

    _switch(client).value = True

    assert _cards(client) == 1


def test_switching_the_filter_off_shows_everybody_again(address_register):
    """It filters; it does not hide anything permanently."""
    _person(street="Nirgendweg")
    _person(street="Erstweg")
    client = _persons_page()
    switch = _switch(client)

    switch.value = True
    switch.value = False

    assert _cards(client) == 2


def test_the_filter_is_hidden_when_nothing_is_marked(address_register):
    """A control that can only ever empty the list is clutter."""
    _person(street="Erstweg")

    assert _switch(_persons_page()).visible is False


def test_the_filter_appears_as_soon_as_something_is_marked(address_register):
    """And it has to come back, or it is simply gone."""
    _person(street="Nirgendweg")

    assert _switch(_persons_page()).visible is True


def test_the_filter_switches_itself_off_when_the_last_finding_goes(address_register):
    """Otherwise the list is filtered to nothing from off-screen."""
    person_id = _person(street="Nirgendweg")
    first = _persons_page()
    _switch(first).value = True

    with connection_scope() as connection:
        person = person_repo.get(connection, person_id)
        person.billing_street = "Erstweg"
        person_repo.update(connection, person)

    second = _persons_page()
    assert _switch(second).visible is False
    assert _switch(second).value is False
    assert _cards(second) == 1


def test_nothing_is_marked_without_a_register():
    """No register, no address findings, nothing to filter."""
    _person(street="Nirgendweg")

    assert _markers(_persons_page()) == 0


# --- The control itself ----------------------------------------------------


def test_the_filter_control_tracks_what_is_marked():
    """Visibility and the reset, without a page around it."""
    client = Client(ui.page("/probe-filter-control")(lambda: None), request=None)
    with client:
        control = ProblemFilter(lambda: None)

    control.update({1, 2})
    assert control.switch.visible is True
    control.switch.value = True
    assert control.active is True

    control.update(set())
    assert control.switch.visible is False
    assert control.active is False, "ein unsichtbarer Schalter darf nicht weiterfiltern"


# --- Where the text belongs ------------------------------------------------


def test_the_notes_spell_the_findings_out():
    """The eye and the pencil show the text the list withholds."""
    client = Client(ui.page("/probe-problem-notes")(lambda: None), request=None)
    with client:
        render_problem_notes(
            [
                QualityWarning(category="x", message="Erster Befund"),
                QualityWarning(category="y", message="Zweiter Befund"),
            ]
        )

    texts = [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", "")
    ]
    assert "Erster Befund" in texts
    assert "Zweiter Befund" in texts


@pytest.mark.parametrize("warnings", [None, []])
def test_the_notes_draw_nothing_when_there_is_nothing_to_say(warnings):
    """A sound record shows no empty box."""
    client = Client(ui.page(f"/probe-notes-empty-{warnings is None}")(lambda: None), request=None)
    with client:
        render_problem_notes(warnings)

    assert not [element for element in client.elements.values() if element.__class__.__name__ == "Card"]


def test_problems_are_grouped_by_the_record_they_belong_to(address_register):
    """Every check names its subject, so a list can mark the right row."""
    person_id = _person(street="Nirgendweg")

    with connection_scope() as connection:
        found = problems_for(connection, SUBJECT_PERSON)
        assert person_id in found
        assert found[person_id]
        assert problems_for(connection, SUBJECT_METERING_POINT) == {}


# --- Every list that carries a marker --------------------------------------


def _build_a_broken_deployment() -> None:
    """One Trafokreis with a single feed-in meter and no LEG.

    That is two findings at once, on two different lists: the meter has no
    LEG, and the Trafokreis has only one direction so nothing can be shared
    there.

    Returns:
        None.
    """
    from app.models import metering_point as metering_point_repo
    from app.models import site as site_repo
    from app.models import substation_area as substation_area_repo
    from app.models.metering_point import DIRECTION_FEED_IN, MeteringPoint
    from app.models.site import Site
    from app.models.substation_area import SubstationArea

    with connection_scope() as connection:
        area = substation_area_repo.create(
            connection,
            SubstationArea(id=None, name="TRA700", bkw_designation="TRA700", note="", created_at=""),
        )
        site = site_repo.create(
            connection,
            Site(
                id=None,
                street="Erstweg",
                house_number="4",
                postal_code="3048",
                municipality="Musterdorf",
                address_detail="",
                substation_area_id=area,
                created_at="",
            ),
        )
        metering_point_repo.create(
            connection,
            MeteringPoint(
                id=None,
                designation="CH1018000000000000000000001",
                direction=DIRECTION_FEED_IN,
                site_id=site,
                leg_id=None,
                pv_capacity_kwp=None,
                battery_capacity_kwh=None,
                created_at="",
            ),
        )


def _page(module: str, function: str, probe: str) -> Client:
    """Render one list page.

    Args:
        module: Module under `app.gui.pages`.
        function: The page function's name.
        probe: A unique probe route -- every `ui.page` registers itself.

    Returns:
        The client holding the rendered page.
    """
    import importlib

    page_module = importlib.import_module(f"app.gui.pages.{module}")
    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        getattr(page_module, function)()
    return client


@pytest.mark.parametrize(
    "module, function, probe",
    [
        ("metering_points", "metering_points_page", "/probe-marker-mp"),
        ("substation_areas", "substation_areas_page", "/probe-marker-ta"),
    ],
)
def test_a_card_list_marks_and_offers_the_filter(module, function, probe):
    """The overview says "7 Messpunkte ohne LEG" and links to the list.

    Before the summarising it named every one of them; without a marker the
    reader arrives and cannot tell which. That is the gap this closes, and
    it is the administrator's own report.
    """
    _build_a_broken_deployment()

    client = _page(module, function, probe)

    assert _markers(client) == 1
    assert _switch(client).visible is True


def test_a_sound_list_shows_no_marker_and_no_filter():
    """Nothing wrong, nothing shown -- the filter would only empty it."""
    client = _page("legs", "legs_page", "/probe-marker-leg")

    assert _markers(client) == 0
    assert _switch(client).visible is False


def test_the_table_list_marks_its_rows(address_register):
    """Standorte is a table, so its marker is markup rather than an element.

    The flag on the row is what the slot reads, so that is what is checked
    here; `TABLE_MARKER_HTML` keeps the two renderings from drifting.
    """
    from app.models import site as site_repo
    from app.models.site import Site

    with connection_scope() as connection:
        for street in ("Erstweg", "Nirgendweg"):
            site_repo.create(
                connection,
                Site(
                    id=None,
                    street=street,
                    house_number="4",
                    postal_code="3048",
                    municipality="Musterdorf",
                    address_detail="",
                    substation_area_id=None,
                    created_at="",
                ),
            )

    client = _page("sites", "sites_page", "/probe-marker-site")
    table = next(e for e in client.elements.values() if e.__class__.__name__ == "Table")

    assert {r["address"]: r["has_problem"] for r in table.rows} == {
        "Erstweg 4": False,
        "Nirgendweg 4": True,
    }


def test_the_table_filter_reduces_to_the_marked_rows(address_register):
    """Driven through the switch, like every other filter here."""
    from app.models import site as site_repo
    from app.models.site import Site

    with connection_scope() as connection:
        for street in ("Erstweg", "Nirgendweg"):
            site_repo.create(
                connection,
                Site(
                    id=None,
                    street=street,
                    house_number="4",
                    postal_code="3048",
                    municipality="Musterdorf",
                    address_detail="",
                    substation_area_id=None,
                    created_at="",
                ),
            )
    client = _page("sites", "sites_page", "/probe-marker-site-filter")
    table = next(e for e in client.elements.values() if e.__class__.__name__ == "Table")

    _switch(client).value = True

    assert [r["address"] for r in table.rows] == ["Nirgendweg 4"]


def test_the_table_marker_comes_from_the_shared_constant():
    """A table renders markup, a card renders elements.

    The triangle therefore exists twice, and the slot is built from the
    constant so the two cannot drift apart.
    """
    from app.gui.problem_markers import TABLE_MARKER_HTML

    client = _page("sites", "sites_page", "/probe-marker-site-slot")
    table = next(e for e in client.elements.values() if e.__class__.__name__ == "Table")

    markup = "".join(str(part) for part in table.slots["body-cell-actions"].children)
    markup += str(table.slots["body-cell-actions"].template or "")

    assert TABLE_MARKER_HTML in markup


# --- The eye and the pencil show what the triangle withholds ---------------


def _labels(client: Client) -> list[str]:
    """Every label text on a rendered page or dialog.

    Args:
        client: The rendered client.

    Returns:
        The non-empty texts.
    """
    return [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", "")
    ]


def test_the_detail_page_names_the_finding(address_register):
    """The marker says "look at this one"; this is the looking.

    Without it the triangle is a dead end -- which is how the administrator
    met it: a summary line in the overview, a link to a list, and nothing
    saying what was wrong or where.
    """
    person_id = _person(street="Nirgendweg")

    from app.gui.pages import persons as persons_module

    client = Client(ui.page("/probe-detail-notes")(lambda: None), request=None)
    with client:
        persons_module.person_detail_page(person_id)

    texts = _labels(client)
    assert "Zu prüfen" in texts
    assert any("amtlichen Verzeichnis" in text for text in texts)


def test_a_sound_record_shows_no_box_on_its_detail_page(address_register):
    """Silence is the normal case; an empty box would be noise."""
    person_id = _person(street="Erstweg")

    from app.gui.pages import persons as persons_module

    client = Client(ui.page("/probe-detail-clean")(lambda: None), request=None)
    with client:
        persons_module.person_detail_page(person_id)

    assert "Zu prüfen" not in _labels(client)


def test_the_dialog_leaves_the_address_finding_at_its_field(address_register):
    """It is already shown beside the input it is about.

    Repeating it in the block at the top would say the same thing twice,
    once far from the field it concerns.
    """
    from app.db.connection import connection_scope as scope
    from app.gui.person_form import open_person_form
    from app.models import person as repo

    person_id = _person(street="Nirgendweg")
    with scope() as connection:
        person = repo.get(connection, person_id)

    client = Client(ui.page("/probe-dialog-address")(lambda: None), request=None)
    with client:
        open_person_form(existing=person)

    texts = _labels(client)
    assert "Zu prüfen" not in texts, "der Adressbefund gehört ans Feld, nicht in den Kasten"
    assert any("Meinten Sie" in text or "amtlichen Verzeichnis" in text for text in texts)


def test_the_dialog_names_a_finding_that_has_no_field(address_register):
    """Everything that is not shown beside an input belongs in the block.

    A cooperative member with no shares has no field of its own in this
    dialog, so without the block the pencil would say nothing.
    """
    from datetime import date

    from app.db.connection import connection_scope as scope
    from app.gui.person_form import open_person_form
    from app.models import cooperative_membership as coop_repo
    from app.models import person as repo
    from app.models.cooperative_membership import CooperativeMembership

    person_id = _person(street="Erstweg")
    with scope() as connection:
        coop_repo.create(
            connection,
            CooperativeMembership(
                id=None,
                person_id=person_id,
                shares=0,
                valid_from=date.today(),
                valid_to=None,
                created_at="",
            ),
        )
        person = repo.get(connection, person_id)

    client = Client(ui.page("/probe-dialog-shares")(lambda: None), request=None)
    with client:
        open_person_form(existing=person)

    texts = _labels(client)
    assert "Zu prüfen" in texts
    assert any("Anteile" in text for text in texts)


def test_a_new_record_shows_no_findings(address_register):
    """There is nothing to have a finding about yet."""
    from app.gui.person_form import open_person_form

    client = Client(ui.page("/probe-dialog-new")(lambda: None), request=None)
    with client:
        open_person_form()

    assert "Zu prüfen" not in _labels(client)
