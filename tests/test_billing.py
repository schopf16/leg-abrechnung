"""Tests for the combined per-person net billing computation, final-step
rounding, the admin fees (admin fee/paper invoice), and the
sum-balance check."""

from app.domain.billing import (
    compute_billing_items,
    create_or_replace_billing_run,
    round_to_rappen,
    verify_sum_balance,
)
from app.domain.demo_data import SUMMER_QUARTER, WINTER_QUARTER, create_demo_data
from app.domain.distribution import DistributionResult, PersonQuarterResult
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRunItem


def _item(person_id: int, net_amount_rappen: int) -> BillingRunItem:
    """Build a minimal unpersisted `BillingRunItem` for balance-check tests.

    Args:
        person_id: Person the item belongs to.
        net_amount_rappen: Net amount to test with.

    Returns:
        A `BillingRunItem` with placeholder kWh/price fields and no admin fees.
    """
    return BillingRunItem(
        id=None, billing_run_id=0, person_id=person_id,
        consumed_kwh=0, produced_kwh=0, price_rp_per_kwh=12,
        admin_fee_consumption_rappen=0, paper_invoice_rappen=0,
        net_amount_rappen=net_amount_rappen, pdf_path=None, created_at="",
    )


def _demo_leg_id(db) -> int:
    """Return the id of the single LEG created by `create_demo_data`."""
    return leg_repo.list_all(db)[0].id


def test_prosumer_gets_a_single_netted_item_not_two():
    """A prosumer (consumption and production both nonzero) gets exactly one
    item -- consumption and production are netted, never billed separately."""
    distribution = DistributionResult(
        leg_id=1, year=2025, quarter=1,
        person_results={
            1: PersonQuarterResult(person_id=1, consumed_local_kwh=10.0, produced_local_kwh=4.0),
        },
    )
    items = compute_billing_items(distribution, 12.0, 0.0, 0.0, 0, {})

    assert len(items) == 1
    item = items[0]
    # 10 kWh * 12 Rp. (owed to LEG) - 4 kWh * 12 Rp. (owed by LEG) = 72 Rp.
    assert item.net_amount_rappen == 72
    assert item.is_owed_to_leg
    assert not item.is_owed_by_leg


def test_pure_consumer_gets_positive_net_owed_to_leg():
    """A person with only consumption gets a positive net (an invoice)."""
    distribution = DistributionResult(
        leg_id=1, year=2025, quarter=1,
        person_results={
            1: PersonQuarterResult(person_id=1, consumed_local_kwh=5.0, produced_local_kwh=0.0),
        },
    )
    items = compute_billing_items(distribution, 12.0, 0.0, 0.0, 0, {})
    assert len(items) == 1
    assert items[0].net_amount_rappen == 60
    assert items[0].is_owed_to_leg


def test_pure_producer_gets_negative_net_owed_by_leg():
    """A person with only production gets a negative net (a credit)."""
    distribution = DistributionResult(
        leg_id=1, year=2025, quarter=1,
        person_results={
            1: PersonQuarterResult(person_id=1, consumed_local_kwh=0.0, produced_local_kwh=5.0),
        },
    )
    items = compute_billing_items(distribution, 12.0, 0.0, 0.0, 0, {})
    assert len(items) == 1
    assert items[0].net_amount_rappen == -60
    assert items[0].is_owed_by_leg
    assert not items[0].is_owed_to_leg


def test_person_with_no_local_sharing_gets_no_item():
    """A person absent from the distribution result gets no billing item."""
    distribution = DistributionResult(leg_id=1, year=2025, quarter=1, person_results={})
    assert compute_billing_items(distribution, 12.0, 0.0, 0.0, 0, {}) == []


def test_rounding_uses_half_up_and_happens_only_once():
    """0.5 Rappen rounds up; only the final net amount is rounded, per
    app.domain.billing's "round only once, at the end" design."""
    distribution = DistributionResult(
        leg_id=1, year=2025, quarter=1,
        person_results={
            1: PersonQuarterResult(person_id=1, consumed_local_kwh=0.125, produced_local_kwh=0.0),
        },
    )
    # 0.125 kWh * 12 Rp./kWh = 1.5 Rappen -> rounds to 2.
    items = compute_billing_items(distribution, 12.0, 0.0, 0.0, 0, {})
    assert items[0].net_amount_rappen == 2


def test_admin_fee_consumption_is_charged_on_consumption_only():
    """The consumption admin surcharge applies to consumed_local_kwh only, even
    when an feed-in rate of zero means production is untouched."""
    distribution = DistributionResult(
        leg_id=1, year=2025, quarter=1,
        person_results={
            1: PersonQuarterResult(person_id=1, consumed_local_kwh=100.0, produced_local_kwh=50.0),
        },
    )
    # admin_fee_consumption_rp_per_kwh=0.5 -> 100 * 0.5 = 50 Rappen.
    items = compute_billing_items(distribution, 12.0, 0.5, 0.0, 0, {})
    assert items[0].admin_fee_consumption_rappen == 50
    assert items[0].admin_fee_feed_in_rappen == 0
    assert items[0].admin_fee_consumption_rp_per_kwh == 0.5
    # Energy net: 100*12 - 50*12 = 600 Rappen; total = 600 + 50 = 650.
    assert items[0].net_amount_rappen == 650


def test_admin_fee_feed_in_is_independent_of_consumption():
    """The feed-in admin surcharge applies to produced_local_kwh only,
    with its own independent rate -- charging one direction must not
    imply anything about the other."""
    distribution = DistributionResult(
        leg_id=1, year=2025, quarter=1,
        person_results={
            1: PersonQuarterResult(person_id=1, consumed_local_kwh=100.0, produced_local_kwh=50.0),
        },
    )
    # consumption rate 0 (no consumption fee), feed-in rate 0.2 -> 50 * 0.2 = 10 Rappen.
    items = compute_billing_items(distribution, 12.0, 0.0, 0.2, 0, {})
    assert items[0].admin_fee_consumption_rappen == 0
    assert items[0].admin_fee_feed_in_rappen == 10
    assert items[0].admin_fee_feed_in_rp_per_kwh == 0.2
    # Energy net: 100*12 - 50*12 = 600 Rappen; total = 600 + 10 = 610.
    assert items[0].net_amount_rappen == 610


def test_admin_fee_rates_are_frozen_onto_the_item():
    """Both actual rates used must be stored on the item itself, not just
    the resulting fee amount -- this is what lets a later rate change in
    Einstellungen leave already-billed items' displayed rate untouched
    (see app.domain.billing's module docstring)."""
    distribution = DistributionResult(
        leg_id=1, year=2025, quarter=1,
        person_results={
            1: PersonQuarterResult(person_id=1, consumed_local_kwh=10.0, produced_local_kwh=10.0),
        },
    )
    items = compute_billing_items(distribution, 12.0, 0.7, 0.3, 0, {})
    assert items[0].admin_fee_consumption_rp_per_kwh == 0.7
    assert items[0].admin_fee_feed_in_rp_per_kwh == 0.3


def test_paper_invoice_applied_only_when_person_opted_in():
    """The flat paper-invoice fee only applies to persons flagged for it."""
    distribution = DistributionResult(
        leg_id=1, year=2025, quarter=1,
        person_results={
            1: PersonQuarterResult(person_id=1, consumed_local_kwh=10.0, produced_local_kwh=0.0),
            2: PersonQuarterResult(person_id=2, consumed_local_kwh=10.0, produced_local_kwh=0.0),
        },
    )
    items = compute_billing_items(distribution, 12.0, 0.0, 0.0, 200, {1: True, 2: False})
    items_by_person = {i.person_id: i for i in items}

    assert items_by_person[1].paper_invoice_rappen == 200
    assert items_by_person[1].net_amount_rappen == 10 * 12 + 200
    assert items_by_person[2].paper_invoice_rappen == 0
    assert items_by_person[2].net_amount_rappen == 10 * 12


def test_round_to_rappen_half_up():
    """The shared rounding helper rounds halves up, not to even."""
    assert round_to_rappen(1.5) == 2
    assert round_to_rappen(2.5) == 3
    assert round_to_rappen(-1.5) == -2


def test_verify_sum_balance_detects_balanced_items():
    """Equal positive and negative net totals are reported as balanced."""
    items = [_item(1, 120), _item(2, -120)]
    check = verify_sum_balance(items)
    assert check.balanced
    assert check.difference_rappen == 0
    assert check.total_owed_to_leg_rappen == 120
    assert check.total_owed_by_leg_rappen == 120


def test_verify_sum_balance_flags_large_mismatch():
    """A large mismatch well outside rounding tolerance is flagged as unbalanced."""
    items = [_item(1, 1200), _item(2, -120)]
    check = verify_sum_balance(items)
    assert not check.balanced


def test_verify_sum_balance_ignores_admin_fees():
    """Admin fees added on top of a balanced energy net don't break the balance check."""
    balanced_energy_item_to_leg = BillingRunItem(
        id=None, billing_run_id=0, person_id=1, consumed_kwh=0, produced_kwh=0,
        price_rp_per_kwh=12, admin_fee_consumption_rappen=50, paper_invoice_rappen=200,
        net_amount_rappen=120 + 50 + 200, pdf_path=None, created_at="",
    )
    balanced_energy_item_by_leg = BillingRunItem(
        id=None, billing_run_id=0, person_id=2, consumed_kwh=0, produced_kwh=0,
        price_rp_per_kwh=12, admin_fee_consumption_rappen=0, paper_invoice_rappen=0,
        net_amount_rappen=-120, pdf_path=None, created_at="",
    )
    check = verify_sum_balance([balanced_energy_item_to_leg, balanced_energy_item_by_leg])
    assert check.balanced
    assert check.total_owed_to_leg_rappen == 120
    assert check.total_owed_by_leg_rappen == 120


def test_verify_sum_balance_ignores_admin_fee_feed_in_too():
    """The feed-in admin fee must be excluded from the energy balance
    check exactly like the consumption one -- both are pure LEG revenue with no
    producer-side counterpart."""
    item = BillingRunItem(
        id=None, billing_run_id=0, person_id=1, consumed_kwh=0, produced_kwh=0,
        price_rp_per_kwh=12, admin_fee_consumption_rappen=0, paper_invoice_rappen=0,
        admin_fee_feed_in_rappen=30,
        net_amount_rappen=-120 + 30, pdf_path=None, created_at="",
    )
    check = verify_sum_balance([item, _item(2, 120)])
    assert check.balanced
    assert check.total_owed_by_leg_rappen == 120


def test_full_billing_run_winter_quarter_has_zero_amounts(db):
    """With demo data, the winter run (P=0 throughout) bills nothing."""
    create_demo_data(db)
    leg_id = _demo_leg_id(db)
    run, items, control_check, distribution = create_or_replace_billing_run(db, leg_id, *WINTER_QUARTER)

    assert run.leg_id == leg_id
    assert run.period_year == WINTER_QUARTER[0]
    assert run.period_quarter == WINTER_QUARTER[1]
    assert items == []
    assert control_check.balanced
    assert distribution.total_consumed_local_kwh() == 0.0
    assert distribution.total_produced_local_kwh() == 0.0


def test_full_billing_run_summer_quarter_balances_one_item_per_person(db):
    """With demo data, the summer run produces balanced nets, one item per
    person even for prosumers."""
    create_demo_data(db)
    leg_id = _demo_leg_id(db)
    run, items, control_check, distribution = create_or_replace_billing_run(db, leg_id, *SUMMER_QUARTER)

    assert items, "expected nonzero billing items for the summer quarter"
    assert control_check.balanced, (
        f"owed_to_leg={control_check.total_owed_to_leg_rappen} "
        f"owed_by_leg={control_check.total_owed_by_leg_rappen}"
    )
    assert any(i.is_owed_to_leg for i in items)
    assert any(i.is_owed_by_leg for i in items)
    # One item per person, never two for the same person.
    person_ids = [i.person_id for i in items]
    assert len(person_ids) == len(set(person_ids))

    stored_items = billing_run_repo.list_items(db, run.id)
    assert len(stored_items) == len(items)


def test_rerunning_billing_replaces_previous_run(db):
    """Running billing twice for the same LEG and quarter does not duplicate runs or items."""
    create_demo_data(db)
    leg_id = _demo_leg_id(db)
    create_or_replace_billing_run(db, leg_id, *SUMMER_QUARTER)
    run_2, items_2, _, _ = create_or_replace_billing_run(db, leg_id, *SUMMER_QUARTER)

    all_runs = billing_run_repo.list_runs(db)
    matching = [
        r for r in all_runs
        if r.leg_id == leg_id and r.period_year == SUMMER_QUARTER[0] and r.period_quarter == SUMMER_QUARTER[1]
    ]
    assert len(matching) == 1
    assert matching[0].id == run_2.id
    assert len(billing_run_repo.list_items(db, run_2.id)) == len(items_2)


def test_admin_fee_rates_survive_persistence_round_trip(db):
    """The frozen rate fields must actually be stored/reloaded via the
    database, not just present on the in-memory dataclass before
    `add_items` ever runs -- this exercises the real INSERT/SELECT
    columns, not just Python construction."""
    create_demo_data(db)
    leg_id = _demo_leg_id(db)
    settings = settings_repo.get_settings(db)
    settings.admin_fee_consumption_rp_per_kwh = 0.8
    settings.admin_fee_feed_in_rp_per_kwh = 0.4
    settings_repo.update_settings(db, settings)

    run, items, _, _ = create_or_replace_billing_run(db, leg_id, *SUMMER_QUARTER)
    reloaded = billing_run_repo.list_items(db, run.id)

    assert reloaded, "expected items for the summer quarter"
    for item in reloaded:
        assert item.admin_fee_consumption_rp_per_kwh == 0.8
        assert item.admin_fee_feed_in_rp_per_kwh == 0.4


def test_set_item_email_sent_at_round_trips(db):
    """`email_sent_at` starts unset and is stored/reloaded correctly once set."""
    create_demo_data(db)
    leg_id = _demo_leg_id(db)
    run, items, _, _ = create_or_replace_billing_run(db, leg_id, *SUMMER_QUARTER)
    item = items[0]
    assert billing_run_repo.list_items(db, run.id)[0].email_sent_at is None

    billing_run_repo.set_item_email_sent_at(db, item.id, "2026-01-01T12:00:00+00:00")

    reloaded = next(i for i in billing_run_repo.list_items(db, run.id) if i.id == item.id)
    assert reloaded.email_sent_at == "2026-01-01T12:00:00+00:00"
