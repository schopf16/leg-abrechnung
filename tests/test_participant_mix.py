"""Tests for app.domain.participant_mix (Prosumer:Consumer ratio,
one-sided substation areas, LEG upgrade candidates)."""

import itertools
from datetime import date

from app.domain import participant_mix
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import person as person_repo
from app.models import standort as standort_repo
from app.models import substation_area as substation_area_repo
from app.models import zuordnung as zuordnung_repo
from app.models.leg import Leg
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG, Messpunkt
from app.models.person import Person
from app.models.standort import Standort
from app.models.substation_area import SubstationArea
from app.models.zuordnung import Zuordnung

_bezeichnung_counter = itertools.count(1)


def _person(db, name: str = "Test") -> int:
    return person_repo.create(
        db,
        Person(
            id=None, anrede="", firma="", vorname=name, nachname="",
            kontakt_email=f"{name.lower()}@example.invalid", kontakt_telefon="",
            rechnungsadresse_strasse="Weg", rechnungsadresse_hausnummer="1", rechnungsadresse_plz="3000",
            rechnungsadresse_ort="Bern", rechnungsadresse_land="CH",
            iban="", kundennummer=None, bkw_kundennummer=None,
            papierrechnung=False, aktiv=True, created_at="",
        ),
    )


def _substation_area(db, name: str) -> int:
    return substation_area_repo.create(db, SubstationArea(id=None, name=name, bkw_designation="", note="", created_at=""))


def _leg(db, name: str) -> int:
    return leg_repo.create(db, Leg(id=None, name=name, note="", created_at=""))


def _standort(db, substation_area_id: int, *, adresse: str = "Weg") -> int:
    return standort_repo.create(
        db,
        Standort(
            id=None, adresse=adresse, hausnummer="1", plz="3000", gemeinde="Bern", lage="",
            substation_area_id=substation_area_id, created_at="",
        ),
    )


def _messpunkt(db, standort_id: int, leg_id: int | None, messrichtung: str) -> int:
    return messpunkt_repo.create(
        db,
        Messpunkt(
            id=None, messpunkt_bezeichnung=f"CH{next(_bezeichnung_counter):031d}",
            messrichtung=messrichtung, standort_id=standort_id, leg_id=leg_id,
            pv_leistung_kwp=None, batteriespeicher_kwh=None, created_at="",
        ),
    )


def _zuordnung(db, person_id: int, messpunkt_id: int, von: date, bis: date | None = None) -> int:
    return zuordnung_repo.create(
        db, Zuordnung(id=None, person_id=person_id, messpunkt_id=messpunkt_id, gueltig_von=von, gueltig_bis=bis, created_at="")
    )


def test_consumer_counted_for_bezug_person(db):
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_BEZUG)
    _zuordnung(db, person_id, bezug_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix(db, [standort_id])

    assert mix.consumer_count == 1
    assert mix.prosumer_count == 0
    assert mix.ist_einseitig is True


def test_prosumer_counted_for_einspeisung_person(db):
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    person_id = _person(db)
    einspeisung_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_EINSPEISUNG)
    _zuordnung(db, person_id, einspeisung_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix(db, [standort_id])

    assert mix.prosumer_count == 1
    assert mix.consumer_count == 0
    assert mix.ist_einseitig is True


def test_true_prosumer_with_both_directions_counts_on_both_sides(db):
    """A person with both a Bezug- and an Einspeisung-Messpunkt is
    deliberately counted in both totals -- see module docstring."""
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_EINSPEISUNG)
    _zuordnung(db, person_id, bezug_id, date(2026, 1, 1))
    _zuordnung(db, person_id, einspeisung_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix(db, [standort_id])

    assert mix.prosumer_count == 1
    assert mix.consumer_count == 1
    assert mix.ist_einseitig is False
    assert mix.verhaeltnis == "1:1"
    assert mix.gesamt_personen == 2  # a true prosumer is counted on both sides, see above


def test_ended_zuordnung_before_stichtag_no_longer_counts(db):
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_BEZUG)
    _zuordnung(db, person_id, bezug_id, date(2020, 1, 1), date(2020, 12, 31))

    mix = participant_mix.compute_participant_mix(db, [standort_id], stichtag=date(2026, 1, 1))

    assert mix.consumer_count == 0
    assert mix.prosumer_count == 0


def test_not_yet_started_zuordnung_counts_as_prosumer_and_consumer(db):
    """Real customer data surfaced this: every LEG showed 0:0 because every
    Zuordnung was pre-entered for the following quarter's move-ins. A
    not-yet-started Zuordnung is now always relevant here (`Zuordnung.
    is_current_or_upcoming`), not just once its start date arrives."""
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_EINSPEISUNG)
    _zuordnung(db, person_id, bezug_id, date(2026, 12, 1))
    _zuordnung(db, person_id, einspeisung_id, date(2026, 12, 1))

    mix = participant_mix.compute_participant_mix(db, [standort_id], stichtag=date(2026, 9, 10))

    assert mix.consumer_count == 1
    assert mix.prosumer_count == 1


def test_empty_scope_is_not_flagged_as_einseitig(db):
    """No participants at all yet is not the same problem as one-sided --
    nothing to warn about."""
    mix = participant_mix.compute_participant_mix(db, [])

    assert mix.prosumer_count == 0
    assert mix.consumer_count == 0
    assert mix.ist_einseitig is True
    assert mix.hinweis is None


def test_hinweis_nur_lieferanten(db):
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    person_id = _person(db)
    einspeisung_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_EINSPEISUNG)
    _zuordnung(db, person_id, einspeisung_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix_for_substation_area(db, substation_area_id)

    assert "Nur Prosumer" in mix.hinweis


def test_hinweis_nur_bezueger(db):
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, None, MESSRICHTUNG_BEZUG)
    _zuordnung(db, person_id, bezug_id, date(2026, 1, 1))

    mix = participant_mix.compute_participant_mix_for_substation_area(db, substation_area_id)

    assert "Nur Consumer" in mix.hinweis


def test_upgrade_candidate_found_when_mixed_leg_and_substation_area_now_workable(db):
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    standort_id = _standort(db, substation_area_id)
    other_standort_id = _standort(db, other_substation_area_id, adresse="Anderswo")

    mixed_leg_id = _leg(db, "Gemischte LEG")
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    _zuordnung(db, person_id, bezug_id, date(2026, 1, 1))
    _zuordnung(db, person_id, einspeisung_id, date(2026, 1, 1))
    other_person_id = _person(db, "Andere")
    other_mp_id = _messpunkt(db, other_standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    _zuordnung(db, other_person_id, other_mp_id, date(2026, 1, 1))

    candidates = participant_mix.find_upgrade_candidates(db)

    matching = [c for c in candidates if c.substation_area.id == substation_area_id]
    assert len(matching) == 1
    assert matching[0].mixed_legs[0].id == mixed_leg_id
    assert matching[0].person_count == 1


def test_no_upgrade_candidate_for_an_already_dedicated_leg(db):
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    dedicated_leg_id = _leg(db, "Dedizierte LEG")
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, dedicated_leg_id, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, dedicated_leg_id, MESSRICHTUNG_EINSPEISUNG)
    _zuordnung(db, person_id, bezug_id, date(2026, 1, 1))
    _zuordnung(db, person_id, einspeisung_id, date(2026, 1, 1))

    candidates = participant_mix.find_upgrade_candidates(db)

    assert [c for c in candidates if c.substation_area.id == substation_area_id] == []


def test_upgrade_candidate_hidden_below_min_personen(db):
    """A substation area with both sides present but too few people overall is
    not suggested -- the same setup that produces a candidate with
    `min_personen=0` (the default) produces none once the threshold
    exceeds the 2 people actually present."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    standort_id = _standort(db, substation_area_id)
    other_standort_id = _standort(db, other_substation_area_id, adresse="Anderswo")

    mixed_leg_id = _leg(db, "Gemischte LEG")
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    _zuordnung(db, person_id, bezug_id, date(2026, 1, 1))
    _zuordnung(db, person_id, einspeisung_id, date(2026, 1, 1))
    other_person_id = _person(db, "Andere")
    other_mp_id = _messpunkt(db, other_standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    _zuordnung(db, other_person_id, other_mp_id, date(2026, 1, 1))

    candidates = participant_mix.find_upgrade_candidates(db, min_personen=3)

    assert [c for c in candidates if c.substation_area.id == substation_area_id] == []

    candidates = participant_mix.find_upgrade_candidates(db, min_personen=2)

    assert len([c for c in candidates if c.substation_area.id == substation_area_id]) == 1


def test_no_upgrade_candidate_for_a_still_one_sided_substation_area(db):
    """Even in a mixed LEG, a substation area with only one side present is not
    an upgrade candidate -- it genuinely cannot stand alone yet."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    standort_id = _standort(db, substation_area_id)
    other_standort_id = _standort(db, other_substation_area_id, adresse="Anderswo")
    mixed_leg_id = _leg(db, "Gemischte LEG")

    person_id = _person(db)
    einspeisung_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    other_person_id = _person(db, "Andere")
    other_mp_id = _messpunkt(db, other_standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    _zuordnung(db, person_id, einspeisung_id, date(2026, 1, 1))
    _zuordnung(db, other_person_id, other_mp_id, date(2026, 1, 1))

    candidates = participant_mix.find_upgrade_candidates(db)

    assert [c for c in candidates if c.substation_area.id == substation_area_id] == []


def test_leg_should_split_when_every_substation_area_is_independently_green(db):
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    standort_id = _standort(db, substation_area_id)
    other_standort_id = _standort(db, other_substation_area_id, adresse="Anderswo")
    mixed_leg_id = _leg(db, "Gemischte LEG")

    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    other_person_id = _person(db, "Andere")
    other_bezug_id = _messpunkt(db, other_standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    other_einspeisung_id = _messpunkt(db, other_standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    for pid, mp_id in (
        (person_id, bezug_id), (person_id, einspeisung_id),
        (other_person_id, other_bezug_id), (other_person_id, other_einspeisung_id),
    ):
        _zuordnung(db, pid, mp_id, date(2026, 1, 1))

    assert participant_mix.leg_should_split(db, mixed_leg_id) is True


def test_leg_should_not_split_when_one_substation_area_would_be_one_sided_alone(db):
    """Splitting would strand this substation area's participants -- the LEG
    stays better off shared, even though it is mixed."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    standort_id = _standort(db, substation_area_id)
    other_standort_id = _standort(db, other_substation_area_id, adresse="Anderswo")
    mixed_leg_id = _leg(db, "Gemischte LEG")

    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    # This substation area only has a producer -- would be one-sided alone.
    other_person_id = _person(db, "Andere")
    other_mp_id = _messpunkt(db, other_standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    for pid, mp_id in ((person_id, bezug_id), (person_id, einspeisung_id), (other_person_id, other_mp_id)):
        _zuordnung(db, pid, mp_id, date(2026, 1, 1))

    assert participant_mix.leg_should_split(db, mixed_leg_id) is False


def test_leg_should_not_split_below_min_personen(db):
    """Every substation area is independently non-one-sided (would split under
    the default `min_personen=0`), but each only has 2 people -- raising
    the threshold above that turns the recommendation off again."""
    substation_area_id = _substation_area(db, "TK1")
    other_substation_area_id = _substation_area(db, "TK2")
    standort_id = _standort(db, substation_area_id)
    other_standort_id = _standort(db, other_substation_area_id, adresse="Anderswo")
    mixed_leg_id = _leg(db, "Gemischte LEG")

    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    other_person_id = _person(db, "Andere")
    other_bezug_id = _messpunkt(db, other_standort_id, mixed_leg_id, MESSRICHTUNG_BEZUG)
    other_einspeisung_id = _messpunkt(db, other_standort_id, mixed_leg_id, MESSRICHTUNG_EINSPEISUNG)
    for pid, mp_id in (
        (person_id, bezug_id), (person_id, einspeisung_id),
        (other_person_id, other_bezug_id), (other_person_id, other_einspeisung_id),
    ):
        _zuordnung(db, pid, mp_id, date(2026, 1, 1))

    assert participant_mix.leg_should_split(db, mixed_leg_id, min_personen=2) is True
    assert participant_mix.leg_should_split(db, mixed_leg_id, min_personen=3) is False


def test_leg_should_not_split_when_not_mixed(db):
    substation_area_id = _substation_area(db, "TK1")
    standort_id = _standort(db, substation_area_id)
    dedicated_leg_id = _leg(db, "Dedizierte LEG")
    person_id = _person(db)
    bezug_id = _messpunkt(db, standort_id, dedicated_leg_id, MESSRICHTUNG_BEZUG)
    einspeisung_id = _messpunkt(db, standort_id, dedicated_leg_id, MESSRICHTUNG_EINSPEISUNG)
    _zuordnung(db, person_id, bezug_id, date(2026, 1, 1))
    _zuordnung(db, person_id, einspeisung_id, date(2026, 1, 1))

    assert participant_mix.leg_should_split(db, dedicated_leg_id) is False
