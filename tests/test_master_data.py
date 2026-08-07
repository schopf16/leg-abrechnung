"""Tests for Person/Messpunkt/Zuordnung/Leg/Trafokreis CRUD, consistency
warnings, and LEG/Trafokreis composition."""

from datetime import date

import pytest

from app.domain.leg_composition import compute_leg_composition
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import trafokreis as trafokreis_repo
from app.models import zuordnung as zuordnung_repo
from app.models.leg import Leg
from app.models.messpunkt import MESSRICHTUNG_BEZUG, Messpunkt
from app.models.person import Person
from app.models.trafokreis import Trafokreis
from app.models.zuordnung import Zuordnung


def _make_person(name: str = "Test Person") -> Person:
    """Build an unpersisted `Person` for use in tests.

    Args:
        name: Name to assign.

    Returns:
        A `Person` with `id=None`.
    """
    return Person(
        id=None,
        anrede="",
        name=name,
        kontakt_email="test@example.ch",
        kontakt_telefon="",
        rechnungsadresse_strasse="Musterstrasse 1",
        rechnungsadresse_plz="3000",
        rechnungsadresse_ort="Bern",
        rechnungsadresse_land="CH",
        iban="CH9300762011623852957",
        kundennummer=None,
        papierrechnung=False,
        created_at="",
    )


def _make_messpunkt(
    messpunkt_bezeichnung: str = "CH1234567890123456789012345",
    messrichtung: str = MESSRICHTUNG_BEZUG,
    standort_id: int = 1,
    leg_id: int | None = None,
) -> Messpunkt:
    """Build an unpersisted `Messpunkt` for use in tests.

    Args:
        messpunkt_bezeichnung: Business key to assign.
        messrichtung: Measurement direction.
        standort_id: Foreign key of the site the Messpunkt belongs to.
        leg_id: Foreign key of the assigned LEG, or `None`.

    Returns:
        A `Messpunkt` with `id=None`.
    """
    return Messpunkt(
        id=None,
        messpunkt_bezeichnung=messpunkt_bezeichnung,
        messrichtung=messrichtung,
        standort_id=standort_id,
        leg_id=leg_id,
        created_at="",
    )


def _make_trafokreis(db, name: str = "Bern_TRA00001") -> int:
    """Create a minimal Trafokreis and return its id.

    Args:
        db: Database connection fixture.
        name: Name to assign (must be unique).

    Returns:
        The new Trafokreis's id.
    """
    return trafokreis_repo.create(
        db, Trafokreis(id=None, name=name, gemeinde="Bern", bemerkung="", created_at="")
    )


def _make_standort(db, trafokreis_id: int | None = None) -> int:
    """Create a minimal Standort and return its id.

    Args:
        db: Database connection fixture.
        trafokreis_id: Foreign key of the assigned Trafokreis, or `None`.

    Returns:
        The new Standort's id.
    """
    from app.models import standort as standort_repo
    from app.models.standort import Standort

    return standort_repo.create(
        db,
        Standort(
            id=None, adresse="Musterstrasse", hausnummer="1", plz="3000", gemeinde="Bern", lage="",
            trafokreis_id=trafokreis_id, netzebene="NE7", created_at="",
        ),
    )


def test_person_crud_roundtrip(db):
    """Creating, fetching, updating and deleting a person all work."""
    person_id = person_repo.create(db, _make_person())
    fetched = person_repo.get(db, person_id)
    assert fetched is not None
    assert fetched.name == "Test Person"

    fetched.name = "Geänderter Name"
    person_repo.update(db, fetched)
    assert person_repo.get(db, person_id).name == "Geänderter Name"

    person_repo.delete(db, person_id)
    assert person_repo.get(db, person_id) is None


def test_person_kundennummer_is_auto_assigned_and_unique(db):
    """`create` always assigns a fresh, unique 8-digit Kundennummer."""
    first_id = person_repo.create(db, _make_person("A"))
    second_id = person_repo.create(db, _make_person("B"))

    first = person_repo.get(db, first_id)
    second = person_repo.get(db, second_id)

    assert first.kundennummer is not None
    assert second.kundennummer is not None
    assert 10_000_000 <= first.kundennummer <= 99_999_999
    assert first.kundennummer != second.kundennummer


def test_person_kundennummer_ignores_caller_supplied_value(db):
    """`create` always auto-assigns a Kundennummer, ignoring `person.kundennummer`."""
    person = _make_person("A")
    person.kundennummer = None  # what every caller actually passes for a new Person
    person_id = person_repo.create(db, person)

    fetched = person_repo.get(db, person_id)
    assert fetched.kundennummer is not None


def test_person_kundennummer_survives_update(db):
    """Updating a person never changes their Kundennummer."""
    person_id = person_repo.create(db, _make_person())
    original = person_repo.get(db, person_id)

    original.name = "Neuer Name"
    person_repo.update(db, original)

    assert person_repo.get(db, person_id).kundennummer == original.kundennummer


def test_person_kundennummer_formatiert_groups_digits(db):
    """`kundennummer_formatiert` groups the 8 digits as "XX XXX XXX"."""
    person_id = person_repo.create(db, _make_person())
    person = person_repo.get(db, person_id)
    formatted = person.kundennummer_formatiert
    digits = f"{person.kundennummer:08d}"
    assert formatted == f"{digits[:2]} {digits[2:5]} {digits[5:]}"


def test_messpunkt_rejects_unknown_messrichtung(db):
    """Creating a Messpunkt with an invalid messrichtung raises ValueError."""
    standort_id = _make_standort(db)
    with pytest.raises(ValueError):
        messpunkt_repo.create(db, _make_messpunkt(messrichtung="unbekannt", standort_id=standort_id))


def test_messpunkt_bezeichnung_is_unique(db):
    """Two Messpunkte cannot share the same messpunkt_bezeichnung."""
    standort_id = _make_standort(db)
    messpunkt_repo.create(db, _make_messpunkt(messpunkt_bezeichnung="CH1", standort_id=standort_id))
    with pytest.raises(Exception):
        messpunkt_repo.create(db, _make_messpunkt(messpunkt_bezeichnung="CH1", standort_id=standort_id))


def test_messpunkt_direction_properties():
    """`is_bezug`/`is_einspeisung` reflect the Messpunkt's messrichtung."""
    from app.models.messpunkt import MESSRICHTUNG_EINSPEISUNG

    bezug = _make_messpunkt(messrichtung=MESSRICHTUNG_BEZUG)
    einspeisung = _make_messpunkt(messrichtung=MESSRICHTUNG_EINSPEISUNG)
    assert bezug.is_bezug and not bezug.is_einspeisung
    assert einspeisung.is_einspeisung and not einspeisung.is_bezug


def test_zuordnung_covers_respects_open_and_closed_ranges():
    """`Zuordnung.covers` handles open-ended and bounded periods."""
    open_ended = Zuordnung(
        id=1, person_id=1, messpunkt_id=1,
        gueltig_von=date(2025, 1, 1), gueltig_bis=None, created_at="",
    )
    assert open_ended.covers(_dt(2025, 6, 1))
    assert not open_ended.covers(_dt(2024, 12, 31))

    bounded = Zuordnung(
        id=2, person_id=2, messpunkt_id=1,
        gueltig_von=date(2025, 1, 1), gueltig_bis=date(2025, 3, 31), created_at="",
    )
    assert bounded.covers(_dt(2025, 2, 1))
    assert not bounded.covers(_dt(2025, 4, 1))


def _dt(year: int, month: int, day: int):
    """Build a naive `datetime` at midnight for the given date.

    Args:
        year: Calendar year.
        month: Calendar month.
        day: Calendar day.

    Returns:
        A `datetime` at 00:00 on the given date.
    """
    from datetime import datetime

    return datetime(year, month, day)


def test_find_warnings_detects_gap(db):
    """A gap between two Zuordnung periods is reported."""
    standort_id = _make_standort(db)
    person_a = person_repo.create(db, _make_person("A"))
    person_b = person_repo.create(db, _make_person("B"))
    messpunkt_id = messpunkt_repo.create(db, _make_messpunkt(standort_id=standort_id))

    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_a, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 1, 1), gueltig_bis=date(2025, 1, 31), created_at="",
        ),
    )
    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_b, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 2, 5), gueltig_bis=None, created_at="",
        ),
    )

    warnings = zuordnung_repo.find_warnings(db, messpunkt_id)
    assert len(warnings) == 1
    assert warnings[0].kind == "gap"


def test_find_warnings_detects_overlap(db):
    """Overlapping Zuordnung periods are reported."""
    standort_id = _make_standort(db)
    person_a = person_repo.create(db, _make_person("A"))
    person_b = person_repo.create(db, _make_person("B"))
    messpunkt_id = messpunkt_repo.create(db, _make_messpunkt(standort_id=standort_id))

    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_a, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 1, 1), gueltig_bis=date(2025, 2, 15), created_at="",
        ),
    )
    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_b, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 2, 1), gueltig_bis=None, created_at="",
        ),
    )

    warnings = zuordnung_repo.find_warnings(db, messpunkt_id)
    assert len(warnings) == 1
    assert warnings[0].kind == "overlap"


def test_find_warnings_none_for_consecutive_periods(db):
    """Back-to-back Zuordnungen with no gap or overlap raise no warnings."""
    standort_id = _make_standort(db)
    person_a = person_repo.create(db, _make_person("A"))
    person_b = person_repo.create(db, _make_person("B"))
    messpunkt_id = messpunkt_repo.create(db, _make_messpunkt(standort_id=standort_id))

    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_a, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 1, 1), gueltig_bis=date(2025, 8, 15), created_at="",
        ),
    )
    zuordnung_repo.create(
        db,
        Zuordnung(
            id=None, person_id=person_b, messpunkt_id=messpunkt_id,
            gueltig_von=date(2025, 8, 16), gueltig_bis=None, created_at="",
        ),
    )

    assert zuordnung_repo.find_warnings(db, messpunkt_id) == []


def _make_leg(name: str = "Ittigen_TRA21359") -> Leg:
    """Build an unpersisted `Leg` for use in tests.

    Args:
        name: Name to assign.

    Returns:
        A `Leg` with `id=None`.
    """
    return Leg(id=None, name=name, bemerkung="", created_at="")


def test_leg_get_by_name_finds_exact_match(db):
    """`get_by_name` finds a LEG by its exact name."""
    leg_repo.create(db, _make_leg("Ittigen_TRA21359"))

    found = leg_repo.get_by_name(db, "Ittigen_TRA21359")
    assert found is not None
    assert found.name == "Ittigen_TRA21359"


def test_leg_get_by_name_returns_none_for_unknown_name(db):
    """`get_by_name` returns `None` when no LEG has that name."""
    assert leg_repo.get_by_name(db, "Unbekannt_TRA00000") is None


def test_leg_name_is_unique(db):
    """Two LEGs cannot share the same name."""
    leg_repo.create(db, _make_leg("Ittigen_TRA21359"))
    with pytest.raises(Exception):
        leg_repo.create(db, _make_leg("Ittigen_TRA21359"))


def test_trafokreis_get_by_name_finds_exact_match(db):
    """`get_by_name` finds a Trafokreis by its exact name."""
    trafokreis_repo.create(
        db, Trafokreis(id=None, name="Bern_TRA00001", gemeinde="Bern", bemerkung="", created_at="")
    )

    found = trafokreis_repo.get_by_name(db, "Bern_TRA00001")
    assert found is not None
    assert found.name == "Bern_TRA00001"


def test_trafokreis_get_by_name_returns_none_for_unknown_name(db):
    """`get_by_name` returns `None` when no Trafokreis has that name."""
    assert trafokreis_repo.get_by_name(db, "Unbekannt_TRA00000") is None


def test_trafokreis_name_is_unique(db):
    """Two Trafokreise cannot share the same name."""
    trafokreis_repo.create(
        db, Trafokreis(id=None, name="Bern_TRA00001", gemeinde="Bern", bemerkung="", created_at="")
    )
    with pytest.raises(Exception):
        trafokreis_repo.create(
            db, Trafokreis(id=None, name="Bern_TRA00001", gemeinde="Bern", bemerkung="", created_at="")
        )


def test_leg_composition_is_not_mixed_when_all_messpunkte_share_one_trafokreis(db):
    """A LEG whose Messpunkte are all on one Trafokreis is not flagged as mixed."""
    trafokreis_id = _make_trafokreis(db, "Bern_TRA00001")
    standort_a = _make_standort(db, trafokreis_id)
    standort_b = _make_standort(db, trafokreis_id)
    leg_id = leg_repo.create(db, _make_leg("Bern_TRA00001"))
    messpunkt_repo.create(db, _make_messpunkt("CH1", standort_id=standort_a, leg_id=leg_id))
    messpunkt_repo.create(db, _make_messpunkt("CH2", standort_id=standort_b, leg_id=leg_id))

    composition = compute_leg_composition(db, leg_id)
    assert not composition.is_mixed
    assert [t.name for t in composition.trafokreise] == ["Bern_TRA00001"]


def test_leg_composition_is_mixed_when_messpunkte_span_two_trafokreise(db):
    """A LEG whose Messpunkte span two Trafokreise is flagged as mixed."""
    trafokreis_a = _make_trafokreis(db, "Bern_TRA00001")
    trafokreis_b = _make_trafokreis(db, "Bern_TRA00002")
    standort_a = _make_standort(db, trafokreis_a)
    standort_b = _make_standort(db, trafokreis_b)
    leg_id = leg_repo.create(db, _make_leg("Gemeinsame_LEG"))
    messpunkt_repo.create(db, _make_messpunkt("CH1", standort_id=standort_a, leg_id=leg_id))
    messpunkt_repo.create(db, _make_messpunkt("CH2", standort_id=standort_b, leg_id=leg_id))

    composition = compute_leg_composition(db, leg_id)
    assert composition.is_mixed
    assert [t.name for t in composition.trafokreise] == ["Bern_TRA00001", "Bern_TRA00002"]


def test_leg_composition_ignores_other_legs_messpunkte(db):
    """Messpunkte belonging to a different LEG don't count toward this LEG's composition."""
    trafokreis_a = _make_trafokreis(db, "Bern_TRA00001")
    trafokreis_b = _make_trafokreis(db, "Bern_TRA00002")
    standort_a = _make_standort(db, trafokreis_a)
    standort_b = _make_standort(db, trafokreis_b)
    leg_id = leg_repo.create(db, _make_leg("Bern_TRA00001"))
    other_leg_id = leg_repo.create(db, _make_leg("Bern_TRA00002"))
    messpunkt_repo.create(db, _make_messpunkt("CH1", standort_id=standort_a, leg_id=leg_id))
    messpunkt_repo.create(db, _make_messpunkt("CH2", standort_id=standort_b, leg_id=other_leg_id))

    composition = compute_leg_composition(db, leg_id)
    assert not composition.is_mixed
    assert [t.name for t in composition.trafokreise] == ["Bern_TRA00001"]
