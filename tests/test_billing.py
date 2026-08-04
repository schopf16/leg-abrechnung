"""Tests for the combined per-participant net billing computation, final-step
rounding, and the sum-balance check."""

from app.domain.billing import (
    compute_billing_items,
    create_or_replace_billing_run,
    round_to_rappen,
    verify_sum_balance,
)
from app.domain.demo_data import SUMMER_QUARTER, WINTER_QUARTER, create_demo_data
from app.domain.distribution import DistributionResult, ParticipantQuarterResult
from app.models import billing_run as billing_run_repo
from app.models.billing_run import BillingRunItem


def _item(participant_id: int, net_amount_rappen: int) -> BillingRunItem:
    """Build a minimal unpersisted `BillingRunItem` for balance-check tests.

    Args:
        participant_id: Participant the item belongs to.
        net_amount_rappen: Net amount to test with.

    Returns:
        A `BillingRunItem` with placeholder kWh/price fields.
    """
    return BillingRunItem(
        id=None, billing_run_id=0, participant_id=participant_id,
        consumed_kwh=0, produced_kwh=0, price_rp_per_kwh=12,
        net_amount_rappen=net_amount_rappen, pdf_path=None, created_at="",
    )


def test_prosumer_gets_a_single_netted_item_not_two():
    """A prosumer (consumption and production both nonzero) gets exactly one
    item -- consumption and production are netted, never billed separately."""
    distribution = DistributionResult(
        year=2025, quarter=1,
        participant_results={
            1: ParticipantQuarterResult(participant_id=1, consumed_local_kwh=10.0, produced_local_kwh=4.0),
        },
    )
    items = compute_billing_items(distribution, price_rp_per_kwh=12.0)

    assert len(items) == 1
    item = items[0]
    # 10 kWh * 12 Rp. (owed to LEG) - 4 kWh * 12 Rp. (owed by LEG) = 72 Rp.
    assert item.net_amount_rappen == 72
    assert item.is_owed_to_leg
    assert not item.is_owed_by_leg


def test_pure_consumer_gets_positive_net_owed_to_leg():
    """A participant with only consumption gets a positive net (an invoice)."""
    distribution = DistributionResult(
        year=2025, quarter=1,
        participant_results={
            1: ParticipantQuarterResult(participant_id=1, consumed_local_kwh=5.0, produced_local_kwh=0.0),
        },
    )
    items = compute_billing_items(distribution, price_rp_per_kwh=12.0)
    assert len(items) == 1
    assert items[0].net_amount_rappen == 60
    assert items[0].is_owed_to_leg


def test_pure_producer_gets_negative_net_owed_by_leg():
    """A participant with only production gets a negative net (a credit)."""
    distribution = DistributionResult(
        year=2025, quarter=1,
        participant_results={
            1: ParticipantQuarterResult(participant_id=1, consumed_local_kwh=0.0, produced_local_kwh=5.0),
        },
    )
    items = compute_billing_items(distribution, price_rp_per_kwh=12.0)
    assert len(items) == 1
    assert items[0].net_amount_rappen == -60
    assert items[0].is_owed_by_leg
    assert not items[0].is_owed_to_leg


def test_participant_with_no_local_sharing_gets_no_item():
    """A participant absent from the distribution result gets no billing item."""
    distribution = DistributionResult(year=2025, quarter=1, participant_results={})
    assert compute_billing_items(distribution, price_rp_per_kwh=12.0) == []


def test_rounding_uses_half_up_and_happens_only_once():
    """0.5 Rappen rounds up; only the final net amount is rounded, per
    app.domain.billing's "round only once, at the end" design."""
    distribution = DistributionResult(
        year=2025, quarter=1,
        participant_results={
            1: ParticipantQuarterResult(participant_id=1, consumed_local_kwh=0.125, produced_local_kwh=0.0),
        },
    )
    # 0.125 kWh * 12 Rp./kWh = 1.5 Rappen -> rounds to 2.
    items = compute_billing_items(distribution, price_rp_per_kwh=12.0)
    assert items[0].net_amount_rappen == 2


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


def test_full_billing_run_winter_quarter_has_zero_amounts(db):
    """With demo data, the winter run (P=0 throughout) bills nothing."""
    create_demo_data(db)
    run, items, control_check, distribution = create_or_replace_billing_run(db, *WINTER_QUARTER)

    assert run.period_year == WINTER_QUARTER[0]
    assert run.period_quarter == WINTER_QUARTER[1]
    assert items == []
    assert control_check.balanced
    assert distribution.total_consumed_local_kwh() == 0.0
    assert distribution.total_produced_local_kwh() == 0.0


def test_full_billing_run_summer_quarter_balances_one_item_per_participant(db):
    """With demo data, the summer run produces balanced nets, one item per
    participant even for prosumers."""
    create_demo_data(db)
    run, items, control_check, distribution = create_or_replace_billing_run(db, *SUMMER_QUARTER)

    assert items, "expected nonzero billing items for the summer quarter"
    assert control_check.balanced, (
        f"owed_to_leg={control_check.total_owed_to_leg_rappen} "
        f"owed_by_leg={control_check.total_owed_by_leg_rappen}"
    )
    assert any(i.is_owed_to_leg for i in items)
    assert any(i.is_owed_by_leg for i in items)
    # One item per participant, never two for the same person.
    participant_ids = [i.participant_id for i in items]
    assert len(participant_ids) == len(set(participant_ids))

    stored_items = billing_run_repo.list_items(db, run.id)
    assert len(stored_items) == len(items)


def test_rerunning_billing_replaces_previous_run(db):
    """Running billing twice for the same quarter does not duplicate runs or items."""
    create_demo_data(db)
    create_or_replace_billing_run(db, *SUMMER_QUARTER)
    run_2, items_2, _, _ = create_or_replace_billing_run(db, *SUMMER_QUARTER)

    all_runs = billing_run_repo.list_runs(db)
    matching = [r for r in all_runs if r.period_year == SUMMER_QUARTER[0] and r.period_quarter == SUMMER_QUARTER[1]]
    assert len(matching) == 1
    assert matching[0].id == run_2.id
    assert len(billing_run_repo.list_items(db, run_2.id)) == len(items_2)
