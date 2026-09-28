"""Tests for the Genossenschaft membership history.

The point of storing this as dated periods rather than two columns on
`person` is that a cooperative has to be able to answer "who held how many
shares when" years later. So the tests below are mostly about the history
surviving a change, and about the two places this deliberately behaves
differently from `app.models.assignment`: a gap is legitimate, and
membership is judged strictly on today.
"""

import sqlite3
from datetime import date

import pytest

from app.models import cooperative_membership as coop_repo
from app.models import person as person_repo
from app.models.cooperative_membership import CooperativeMembership
from app.models.person import Person


def _person(db, last_name: str = "Muster") -> int:
    """Create a minimal Person to hang memberships off.

    Args:
        db: Database connection fixture.
        last_name: Surname, so several persons can be told apart.

    Returns:
        The new person's id.
    """
    return person_repo.create(
        db,
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


def _add(db, person_id, shares, valid_from, valid_to=None) -> int:
    """Add one membership period.

    Args:
        db: Database connection fixture.
        person_id: The member.
        shares: Share count for this period.
        valid_from: First day of the period.
        valid_to: Last day, or `None` for a running membership.

    Returns:
        The new period's id.
    """
    return coop_repo.create(
        db,
        CooperativeMembership(
            id=None,
            person_id=person_id,
            shares=shares,
            valid_from=valid_from,
            valid_to=valid_to,
            created_at="",
        ),
    )


def test_covers_is_strict_at_both_ends(db):
    """A closed period covers its own first and last day and nothing beyond."""
    person_id = _person(db)
    _add(db, person_id, 5, date(2026, 3, 1), date(2026, 6, 30))
    membership = coop_repo.list_for_person(db, person_id)[0]

    assert membership.covers(date(2026, 3, 1))
    assert membership.covers(date(2026, 6, 30))
    assert not membership.covers(date(2026, 2, 28))
    assert not membership.covers(date(2026, 7, 1))
    assert not membership.is_open


def test_an_open_period_runs_indefinitely(db):
    """No end date means the membership is still in force."""
    person_id = _person(db)
    _add(db, person_id, 3, date(2026, 1, 1))
    membership = coop_repo.list_for_person(db, person_id)[0]

    assert membership.is_open
    assert membership.covers(date(2026, 1, 1))
    assert membership.covers(date(2099, 12, 31))
    assert not membership.covers(date(2025, 12, 31))


def test_changing_the_share_count_keeps_the_previous_figure(db):
    """The whole reason this is a history and not a column.

    Closing the running period and opening a new one must leave the old
    share count answerable for the days it applied to.
    """
    person_id = _person(db)
    _add(db, person_id, 5, date(2026, 1, 1), date(2026, 5, 31))
    _add(db, person_id, 12, date(2026, 6, 1))

    assert coop_repo.shares_for_person(db, person_id, date(2026, 3, 15)) == 5
    assert coop_repo.shares_for_person(db, person_id, date(2026, 8, 15)) == 12
    assert len(coop_repo.list_for_person(db, person_id)) == 2


def test_leaving_and_rejoining_produces_no_warning(db):
    """A gap is the truth about a membership, not an inconsistency.

    Deliberately different from `app.models.assignment.find_warnings`,
    where an uncovered day means somebody's energy belongs to nobody.
    """
    person_id = _person(db)
    _add(db, person_id, 5, date(2024, 1, 1), date(2024, 12, 31))
    _add(db, person_id, 8, date(2026, 6, 1))

    assert coop_repo.find_warnings(db, person_id) == []
    assert coop_repo.current_for_person(db, person_id, date(2025, 6, 1)) is None
    assert coop_repo.shares_for_person(db, person_id, date(2025, 6, 1)) == 0


def test_overlapping_periods_are_reported(db):
    """Two share counts on the same day cannot both be true."""
    person_id = _person(db)
    _add(db, person_id, 5, date(2026, 1, 1), date(2026, 6, 30))
    _add(db, person_id, 9, date(2026, 5, 1))

    warnings = coop_repo.find_warnings(db, person_id)
    assert len(warnings) == 1
    assert warnings[0].kind == "overlap"
    assert "Überlappende" in warnings[0].message


def test_an_earlier_period_left_open_overlaps_everything_after_it(db):
    """Only the last period may be open-ended."""
    person_id = _person(db)
    _add(db, person_id, 5, date(2024, 1, 1))
    _add(db, person_id, 9, date(2026, 1, 1))

    assert [w.kind for w in coop_repo.find_warnings(db, person_id)] == ["overlap"]


def test_membership_is_judged_strictly_on_today(db):
    """A membership starting next month is not a membership yet.

    This is the one place the cooperative deliberately diverges from
    `Assignment.is_current_or_upcoming`: a pre-entered assignment counts
    as planning, but mailing "die Genossenschafter" must not reach
    somebody who has not joined.
    """
    member_id = _person(db, "Mitglied")
    future_id = _person(db, "Kuenftig")
    _add(db, member_id, 4, date(2020, 1, 1))
    _add(db, future_id, 4, date(2099, 1, 1))

    today = coop_repo.member_person_ids(db)
    assert member_id in today
    assert future_id not in today, "eine künftige Mitgliedschaft ist noch keine"


def test_a_member_without_shares_is_a_member(db):
    """Zero shares is a real state, distinguishable from non-membership."""
    person_id = _person(db)
    _add(db, person_id, 0, date(2026, 1, 1))

    membership = coop_repo.current_for_person(db, person_id)
    assert membership is not None
    assert membership.shares == 0
    assert coop_repo.shares_for_person(db, person_id) == 0
    assert person_id in coop_repo.member_person_ids(db)


def test_a_negative_share_count_is_refused_by_the_schema(db):
    """The CHECK constraint, not just the form, guards this."""
    person_id = _person(db)
    with pytest.raises(sqlite3.IntegrityError):
        _add(db, person_id, -1, date(2026, 1, 1))


def test_deleting_a_person_removes_their_membership_rows(db):
    """`ON DELETE CASCADE`: no membership may outlive its member."""
    person_id = _person(db)
    _add(db, person_id, 5, date(2026, 1, 1))
    assert coop_repo.list_for_person(db, person_id)

    assert person_repo.delete(db, person_id) is True
    assert coop_repo.list_for_person(db, person_id) == []


def test_update_and_delete_of_one_period(db):
    """Editing a period changes only that period."""
    person_id = _person(db)
    first = _add(db, person_id, 5, date(2026, 1, 1), date(2026, 5, 31))
    second = _add(db, person_id, 9, date(2026, 6, 1))

    record = coop_repo.get(db, first)
    record.shares = 6
    coop_repo.update(db, record)
    assert coop_repo.get(db, first).shares == 6
    assert coop_repo.get(db, second).shares == 9

    coop_repo.delete(db, second)
    assert coop_repo.get(db, second) is None
    assert coop_repo.get(db, first) is not None
