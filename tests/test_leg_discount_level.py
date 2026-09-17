"""Tests for the BKW discount tier stored per LEG (`leg.discount_level`).

BKW grants 40% off the Netznutzung where the shared electricity needs no
transformation stage and 20% where it does, and calls the two "hohe" and
"niedrige Rabattstufe" on its own LEG pages. The value is entered by
hand, so what matters here is that it round-trips, defaults to "unknown",
and that the schema refuses anything else.
"""

import sqlite3

import pytest

from app.models import leg as leg_repo
from app.models.leg import (
    DISCOUNT_LEVEL_HIGH,
    DISCOUNT_LEVEL_LOW,
    DISCOUNT_LEVEL_OPTIONS,
    DISCOUNT_LEVEL_SHORT,
    DISCOUNT_LEVEL_UNKNOWN,
    Leg,
)


def _leg(name="LEG Ittigen", **overrides) -> Leg:
    fields = dict(id=None, name=name, note="", created_at="")
    fields.update(overrides)
    return Leg(**fields)


def test_a_new_leg_starts_with_an_unknown_tier(db):
    """Nobody has asked BKW yet, and the app must not pretend otherwise."""
    leg_id = leg_repo.create(db, _leg())

    assert leg_repo.get(db, leg_id).discount_level == DISCOUNT_LEVEL_UNKNOWN


@pytest.mark.parametrize("level", [DISCOUNT_LEVEL_HIGH, DISCOUNT_LEVEL_LOW, DISCOUNT_LEVEL_UNKNOWN])
def test_the_tier_round_trips_through_create(db, level):
    leg_id = leg_repo.create(db, _leg(discount_level=level))

    assert leg_repo.get(db, leg_id).discount_level == level


def test_the_tier_can_be_changed_later(db):
    """The usual case: the LEG is entered first, BKW answers afterwards."""
    leg_id = leg_repo.create(db, _leg())
    stored = leg_repo.get(db, leg_id)

    stored.discount_level = DISCOUNT_LEVEL_HIGH
    leg_repo.update(db, stored)

    assert leg_repo.get(db, leg_id).discount_level == DISCOUNT_LEVEL_HIGH


def test_the_schema_refuses_a_tier_that_is_not_one_of_the_three(db):
    """A typo must not become a silently stored third tier."""
    with pytest.raises(sqlite3.IntegrityError):
        leg_repo.create(db, _leg(discount_level="40%"))


def test_every_stored_value_has_a_german_label(db):
    """The overview and the select both render from these maps, so a value
    without a label would show a raw English enum to the user."""
    for level in (DISCOUNT_LEVEL_HIGH, DISCOUNT_LEVEL_LOW, DISCOUNT_LEVEL_UNKNOWN):
        assert level in DISCOUNT_LEVEL_OPTIONS
        assert level in DISCOUNT_LEVEL_SHORT


def test_the_labels_name_the_percentages_bkw_actually_grants():
    """40% and 20% are BKW's two tiers; getting these backwards in the UI
    would misinform the administrator about real money."""
    assert "40%" in DISCOUNT_LEVEL_OPTIONS[DISCOUNT_LEVEL_HIGH]
    assert "20%" in DISCOUNT_LEVEL_OPTIONS[DISCOUNT_LEVEL_LOW]
    assert "40%" in DISCOUNT_LEVEL_SHORT[DISCOUNT_LEVEL_HIGH]
    assert "20%" in DISCOUNT_LEVEL_SHORT[DISCOUNT_LEVEL_LOW]


def test_the_tier_is_independent_of_the_computed_composition(db):
    """Whether a LEG spans several Trafokreise correlates with the tier but
    does not determine it -- BKW confirms it per location. Storing a high
    tier on a LEG the app considers non-optimised must therefore be
    possible, not silently corrected."""
    leg_id = leg_repo.create(db, _leg(discount_level=DISCOUNT_LEVEL_HIGH))

    assert leg_repo.get(db, leg_id).discount_level == DISCOUNT_LEVEL_HIGH
