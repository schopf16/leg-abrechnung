"""Tests for the LEG production-capacity figure and what it implies.

Art. 19e Abs. 1 StromVV requires a LEG's installed production capacity to
be at least 5% of the participants' total Anschlussleistung. BKW's LEG
portal shows the current figure on every metering point registration; the
app only records it and works out whether another consumer still fits.
"""

import pytest

from app.domain.production_capacity import (
    REQUIRED_PERCENT,
    STATUS_BELOW,
    STATUS_COMFORTABLE,
    STATUS_TIGHT,
    STATUS_UNKNOWN,
    compute_headroom,
)
from app.models import leg as leg_repo
from app.models.leg import Leg


def _leg(name="LEG Ittigen", **overrides) -> Leg:
    fields = dict(id=None, name=name, note="", created_at="")
    fields.update(overrides)
    return Leg(**fields)


# -- the rule itself ---------------------------------------------------------


def test_the_legal_floor_is_five_percent():
    """Art. 19e Abs. 1 StromVV. Not this app's number to choose, so it is a
    constant rather than a setting."""
    assert REQUIRED_PERCENT == 5.0


def test_a_never_recorded_figure_is_unknown_not_a_problem():
    """The value can only come from BKW's portal, so not having looked yet
    must not be reported as trouble."""
    headroom = compute_headroom(None, warn_percent=10.0)

    assert headroom.status == STATUS_UNKNOWN
    assert headroom.percent is None
    assert headroom.growth_factor is None


def test_below_five_percent_is_flagged_as_below():
    headroom = compute_headroom(4.9, warn_percent=10.0)

    assert headroom.status == STATUS_BELOW


def test_exactly_five_percent_still_satisfies_the_rule():
    """ "mindestens 5 %" -- the floor is inclusive, and being exactly on it
    is not a violation."""
    assert compute_headroom(5.0, warn_percent=10.0).status != STATUS_BELOW


def test_between_the_floor_and_the_warning_threshold_is_tight():
    headroom = compute_headroom(8.0, warn_percent=10.0)

    assert headroom.status == STATUS_TIGHT


def test_above_the_warning_threshold_is_comfortable():
    headroom = compute_headroom(37.6, warn_percent=10.0)

    assert headroom.status == STATUS_COMFORTABLE


def test_the_warning_threshold_is_configurable():
    """Where "tight" begins is judgement, so it comes from the settings --
    the same reasoning as `leg_founding_min_persons`."""
    assert compute_headroom(12.0, warn_percent=10.0).status == STATUS_COMFORTABLE
    assert compute_headroom(12.0, warn_percent=20.0).status == STATUS_TIGHT


def test_numbers_are_written_the_german_way_everywhere():
    """A decimal point in one place and a comma in the next is how the
    warning and the overview card ended up disagreeing about 8,2 %."""
    from app.domain.production_capacity import format_factor, format_percent

    assert format_percent(37.6) == "37,6 %"
    assert format_percent(5.0) == "5,0 %"
    assert format_factor(7.52) == "7,5"
    assert "," in compute_headroom(37.6, warn_percent=10.0).label
    assert "." not in compute_headroom(37.6, warn_percent=10.0).label.replace("~", "")


# -- the number the administrator actually asked for -------------------------


@pytest.mark.parametrize(
    "percent,expected_factor",
    [(37.6, 7.52), (10.0, 2.0), (5.0, 1.0), (2.5, 0.5)],
)
def test_growth_factor_says_how_much_more_load_fits(percent, expected_factor):
    """Adding a consumer raises the denominator, so the percentage falls.
    With production unchanged, the participants' total Anschlussleistung may
    grow by percent/5 before hitting the floor -- this is the number that
    answers "does another consumer still fit here?"."""
    assert compute_headroom(percent, warn_percent=10.0).growth_factor == pytest.approx(expected_factor)


def test_a_factor_of_one_means_no_room_left():
    """Exactly on the floor: any further consumer breaks the rule."""
    assert compute_headroom(5.0, warn_percent=10.0).growth_factor == pytest.approx(1.0)


def test_the_label_says_grow_TO_the_factor_not_BY_it():
    """ "darf noch ~1,2× wachsen" reads as +120 % when it means +20 %, and
    in the whole warning band the factor is 1.0-2.0 -- so the misreading
    always errs towards admitting a consumer that breaks the floor."""
    comfortable = compute_headroom(37.6, warn_percent=10.0)

    assert "37,6 %" in comfortable.label
    assert "auf das ~7,5-Fache steigen" in comfortable.label
    assert "×" not in comfortable.label


def test_every_label_names_the_assumption_it_rests_on():
    """The factor only holds with production unchanged, and the subject is
    the consumers' *total* Anschlussleistung, not the new customer's own."""
    for percent in (6.0, 37.6):
        label = compute_headroom(percent, warn_percent=10.0).label
        assert "bei unveränderter Produktion" in label, percent
        assert "gesamte Anschlussleistung der Bezüger" in label, percent


def test_the_below_label_names_the_legal_minimum_not_the_headroom():
    """Under the floor, how much it could grow is not the point."""
    below = compute_headroom(3.0, warn_percent=10.0)

    assert "5 %" in below.label
    assert "wachsen" not in below.label


# -- storage -----------------------------------------------------------------


def test_a_new_leg_has_no_recorded_figure(db):
    leg_id = leg_repo.create(db, _leg())
    stored = leg_repo.get(db, leg_id)

    assert stored.production_capacity_percent is None
    assert stored.production_capacity_recorded_at is None


def test_the_figure_and_its_date_round_trip(db):
    leg_id = leg_repo.create(
        db,
        _leg(production_capacity_percent=37.6, production_capacity_recorded_at="2026-09-18"),
    )
    stored = leg_repo.get(db, leg_id)

    assert stored.production_capacity_percent == pytest.approx(37.6)
    assert stored.production_capacity_recorded_at == "2026-09-18"


def test_the_figure_can_be_updated_after_a_new_registration(db):
    """Every metering point registration yields a fresh figure from the
    portal; recording it must overwrite both the value and its date."""
    leg_id = leg_repo.create(
        db, _leg(production_capacity_percent=37.6, production_capacity_recorded_at="2026-09-18")
    )
    stored = leg_repo.get(db, leg_id)
    stored.production_capacity_percent = 21.4
    stored.production_capacity_recorded_at = "2026-11-02"
    leg_repo.update(db, stored)

    after = leg_repo.get(db, leg_id)
    assert after.production_capacity_percent == pytest.approx(21.4)
    assert after.production_capacity_recorded_at == "2026-11-02"


def test_a_name_only_edit_keeps_the_recorded_figure(db):
    """The real editing path: open the LEG, change the name, save."""
    leg_id = leg_repo.create(
        db, _leg(production_capacity_percent=37.6, production_capacity_recorded_at="2026-09-18")
    )
    stored = leg_repo.get(db, leg_id)
    stored.name = "LEG Ittigen (neu)"
    leg_repo.update(db, stored)

    after = leg_repo.get(db, leg_id)
    assert after.production_capacity_percent == pytest.approx(37.6)
    assert after.production_capacity_recorded_at == "2026-09-18"


# -- the GUI row builder -----------------------------------------------------
#
# `_to_row` takes a plain connection and returns a dict, so it is testable
# the same way `app.gui.pages.dashboard._load_overview` already is. It
# carries the staleness suffix and the colour, neither of which any other
# test touched.


def _row_for(db, **overrides):
    from app.gui.pages.legs import _to_row

    leg_id = leg_repo.create(db, _leg(**overrides))
    return _to_row(db, leg_repo.get(db, leg_id), min_persons=7, warn_percent=10.0)


def test_the_row_appends_the_recording_date(db):
    row = _row_for(db, production_capacity_percent=37.6, production_capacity_recorded_at="2026-09-18")

    assert "(Stand 2026-09-18)" in row["production_capacity"]
    assert row["production_capacity_status"] == STATUS_COMFORTABLE


def test_the_row_omits_the_date_when_nothing_was_recorded(db):
    row = _row_for(db)

    assert "Stand" not in row["production_capacity"]
    assert row["production_capacity_status"] == STATUS_UNKNOWN


def test_a_date_without_a_percentage_does_not_produce_a_contradiction(db):
    """Otherwise the card reads "nicht erfasst (Stand 2026-01-02)"."""
    row = _row_for(db, production_capacity_recorded_at="2026-01-02")

    assert "Stand" not in row["production_capacity"]


def test_the_row_status_drives_the_colour(db):
    from app.domain.production_capacity import status_classes

    below = _row_for(db, name="A", production_capacity_percent=3.0)
    tight = _row_for(db, name="B", production_capacity_percent=8.0)

    assert below["production_capacity_status"] == STATUS_BELOW
    assert "text-negative" in status_classes(below["production_capacity_status"])
    assert tight["production_capacity_status"] == STATUS_TIGHT
    assert "text-warning" in status_classes(tight["production_capacity_status"])


def test_a_percentage_above_one_hundred_is_allowed(db):
    """Art. 19e sets only a minimum -- a big producer with few consumers
    legitimately shows more than 100 % in the portal, and an upper bound on
    the input would silently clamp it."""
    leg_id = leg_repo.create(db, _leg(production_capacity_percent=150.0))

    assert leg_repo.get(db, leg_id).production_capacity_percent == pytest.approx(150.0)
    assert compute_headroom(150.0, warn_percent=10.0).status == STATUS_COMFORTABLE
