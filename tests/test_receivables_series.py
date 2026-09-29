"""Tests for the receivables series: invoiced, received, still open.

Money, so signs are the whole risk here. `app.models.account_entry`
stores an incoming payment as a **negative** amount, because it reduces a
debt, and this app negates that in exactly one place -- where a person's
balance is displayed. A second negation anywhere else is how a wrong
number reaches a real invoice, so the arithmetic is pinned here rather
than trusted.

Nothing in this series is ever broken down per person. That is a decision
the administrator made on data-protection grounds, not an oversight, and
`test_nothing_here_is_per_person` holds it in place.
"""

from datetime import datetime

from app.domain import period
from app.domain.period import GRANULARITY_DAY, GRANULARITY_MONTH
from app.domain.statistics import ReceivablesBucket, receivables_series
from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg
from app.models.person import Person


def _person(db, last_name: str = "Muster") -> int:
    """Create a person to bill.

    Args:
        db: Database connection fixture.
        last_name: Their surname.

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
            billing_street="Weg",
            billing_house_number="1",
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


def _invoice(db, person_id: int, rappen: int, created_at: datetime, quarter: int = 1) -> None:
    """Record one billing run holding one item.

    Args:
        db: Database connection fixture.
        person_id: Who is billed.
        rappen: The item's net amount, positive when the person owes.
        created_at: When the run was created -- what dates the receivable.
        quarter: Which quarter the run covers, to keep runs distinct.

    Returns:
        None.
    """
    run_id = billing_run_repo.create_run(
        db,
        BillingRun(
            id=None,
            leg_id=leg_repo.create(
                db,
                Leg(
                    id=None,
                    name=f"LEG {created_at.isoformat()} {quarter}",
                    note="",
                    created_at="",
                    production_capacity_percent=None,
                    production_capacity_recorded_at=None,
                ),
            ),
            period_year=created_at.year,
            period_quarter=quarter,
            created_at=created_at.isoformat(),
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
                net_amount_rappen=rappen,
                pdf_path=None,
                created_at="",
            )
        ],
    )
    db.execute("UPDATE billing_runs SET created_at = ? WHERE id = ?", (created_at.isoformat(), run_id))
    db.commit()


def _payment(db, person_id: int, rappen: int, booked_at: datetime) -> None:
    """Record one incoming payment.

    Args:
        db: Database connection fixture.
        person_id: Who paid.
        rappen: How much arrived, as a positive number -- stored negative,
            which is the convention this test is about.
        booked_at: When it arrived.

    Returns:
        None.
    """
    account_entry_repo.create(
        db,
        person_id=person_id,
        kind="payment_received",
        amount_rappen=-rappen,
        booked_at=booked_at.isoformat(),
    )


def _bucket(series: list[ReceivablesBucket], day: int) -> ReceivablesBucket:
    """Pick one bucket out of a series by its day of month.

    Args:
        series: The series to search.
        day: Day of month.

    Returns:
        The matching bucket.
    """
    return next(bucket for bucket in series if bucket.start.day == day)


def test_an_invoice_raises_the_open_amount(db):
    """The mountain goes up when something is billed."""
    person_id = _person(db)
    _invoice(db, person_id, 12_000, datetime(2026, 7, 10))

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 10))
    series = receivables_series(db, GRANULARITY_DAY, window)

    assert _bucket(series, 10).invoiced_rappen == 12_000
    assert _bucket(series, 10).open_rappen == 12_000
    assert _bucket(series, 10).invoiced_chf == 120.0


def test_a_payment_brings_the_open_amount_down(db):
    """And is reported as money that arrived, not as a negative number.

    The ledger stores it negative; a chart reading "-12'000 eingegangen"
    would be nonsense, so the one sign flip in this module happens here.
    """
    person_id = _person(db)
    _invoice(db, person_id, 12_000, datetime(2026, 7, 10))
    _payment(db, person_id, 12_000, datetime(2026, 7, 20))

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 10))
    series = receivables_series(db, GRANULARITY_DAY, window)

    assert _bucket(series, 20).received_rappen == 12_000, "positiv, nicht negativ"
    assert _bucket(series, 20).received_chf == 120.0
    assert _bucket(series, 20).open_rappen == 0
    assert _bucket(series, 20).invoiced_rappen == 0, "die Zahlung ist keine Rechnung"


def test_the_open_amount_stays_flat_between_events(db):
    """It is a running total, not a per-bucket figure.

    A day with no invoice and no payment does not reset the mountain to
    zero -- which is what a naive per-bucket sum would draw.
    """
    person_id = _person(db)
    _invoice(db, person_id, 5_000, datetime(2026, 7, 5))

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 5))
    series = receivables_series(db, GRANULARITY_DAY, window)

    assert _bucket(series, 4).open_rappen == 0
    assert _bucket(series, 5).open_rappen == 5_000
    assert _bucket(series, 6).open_rappen == 5_000
    assert _bucket(series, 30).open_rappen == 5_000


def test_what_was_open_before_the_window_is_carried_in(db):
    """A debt does not stop existing because the chart starts later."""
    person_id = _person(db)
    _invoice(db, person_id, 9_000, datetime(2026, 1, 15))

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 5))
    series = receivables_series(db, GRANULARITY_DAY, window)

    assert series[0].invoiced_rappen == 0, "die Rechnung fällt nicht in dieses Fenster"
    assert series[0].open_rappen == 9_000, "offen ist sie trotzdem"


def test_a_credit_note_lowers_the_open_amount(db):
    """A payout owed to a member is a negative receivable.

    Read as stored (see `app.models.billing_run`), not negated here.
    """
    person_id = _person(db)
    _invoice(db, person_id, 10_000, datetime(2026, 7, 5))
    _invoice(db, _person(db, "Gutschrift"), -4_000, datetime(2026, 7, 6), quarter=2)

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 5))
    series = receivables_series(db, GRANULARITY_DAY, window)

    assert _bucket(series, 6).invoiced_rappen == -4_000
    assert _bucket(series, 6).open_rappen == 6_000


def test_several_people_are_summed_into_one_line(db):
    """The chart shows the LEG, not a stack of individuals."""
    first = _person(db, "Eins")
    second = _person(db, "Zwei")
    _invoice(db, first, 3_000, datetime(2026, 7, 5))
    _invoice(db, second, 7_000, datetime(2026, 7, 5), quarter=2)
    _payment(db, first, 1_000, datetime(2026, 7, 6))

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 5))
    series = receivables_series(db, GRANULARITY_DAY, window)

    assert _bucket(series, 5).invoiced_rappen == 10_000
    assert _bucket(series, 6).received_rappen == 1_000
    assert _bucket(series, 6).open_rappen == 9_000


def test_nothing_here_is_per_person(db):
    """A data-protection decision, held in place by a test.

    The administrator asked for the LEG's account and explicitly not for
    individual balances: a statistics page is read over somebody's
    shoulder, a person's detail page is not.
    """
    person_id = _person(db)
    _invoice(db, person_id, 1_000, datetime(2026, 7, 5))

    window = period.window_for(GRANULARITY_DAY, datetime(2026, 7, 5))
    bucket = _bucket(receivables_series(db, GRANULARITY_DAY, window), 5)

    fields = set(vars(bucket))
    assert not any("person" in name for name in fields), fields
    assert fields == {"start", "invoiced_rappen", "received_rappen", "open_rappen"}


def test_an_empty_database_draws_a_flat_line_at_zero(db):
    """Which is the true picture before the first billing run."""
    window = period.window_for(GRANULARITY_MONTH, datetime(2026, 7, 5))
    series = receivables_series(db, GRANULARITY_MONTH, window)

    assert len(series) == 12
    assert all(bucket.open_rappen == 0 for bucket in series)
    assert all(bucket.invoiced_rappen == 0 for bucket in series)
