"""Tests for invoice/credit-note computation, rounding and the sum-balance check."""

import pytest

from app.domain.billing import (
    compute_billing_items,
    create_or_replace_billing_run,
    verify_sum_balance,
)
from app.domain.demo_data import SUMMER_QUARTER, WINTER_QUARTER, create_demo_data
from app.domain.distribution import DistributionResult, ParticipantQuarterResult
from app.models import billing_run as billing_run_repo
from app.models.billing_run import BillingRunItem


def test_compute_billing_items_splits_prosumer_into_invoice_and_credit():
    """A prosumer (consumption and production both nonzero) gets both a
    "rechnung" and a "gutschrift" line, never netted."""
    distribution = DistributionResult(
        year=2025, quarter=1,
        participant_results={
            1: ParticipantQuarterResult(participant_id=1, consumed_local_kwh=10.0, produced_local_kwh=4.0),
        },
    )
    items = compute_billing_items(distribution, price_rp_per_kwh=12.0)

    kinds = {item.kind for item in items}
    assert kinds == {"rechnung", "gutschrift"}
    invoice = next(i for i in items if i.kind == "rechnung")
    credit = next(i for i in items if i.kind == "gutschrift")
    assert invoice.amount_rappen == 120  # 10 kWh * 12 Rp.
    assert credit.amount_rappen == 48  # 4 kWh * 12 Rp.


def test_compute_billing_items_skips_zero_amounts():
    """A participant with zero consumption or zero production gets no line for it."""
    distribution = DistributionResult(
        year=2025, quarter=1,
        participant_results={
            1: ParticipantQuarterResult(participant_id=1, consumed_local_kwh=5.0, produced_local_kwh=0.0),
        },
    )
    items = compute_billing_items(distribution, price_rp_per_kwh=12.0)
    assert len(items) == 1
    assert items[0].kind == "rechnung"


def test_rounding_uses_half_up():
    """0.5 Rappen rounds up, matching standard Swiss amount rounding."""
    distribution = DistributionResult(
        year=2025, quarter=1,
        participant_results={
            1: ParticipantQuarterResult(participant_id=1, consumed_local_kwh=0.125, produced_local_kwh=0.0),
        },
    )
    # 0.125 kWh * 12 Rp./kWh = 1.5 Rappen -> rounds to 2.
    items = compute_billing_items(distribution, price_rp_per_kwh=12.0)
    assert items[0].amount_rappen == 2


def test_verify_sum_balance_detects_balanced_items():
    """Equal invoice and credit totals are reported as balanced."""
    items = [
        BillingRunItem(id=None, billing_run_id=0, participant_id=1, kind="rechnung", kwh=1, price_rp_per_kwh=12, amount_rappen=120, pdf_path=None, created_at=""),
        BillingRunItem(id=None, billing_run_id=0, participant_id=2, kind="gutschrift", kwh=1, price_rp_per_kwh=12, amount_rappen=120, pdf_path=None, created_at=""),
    ]
    check = verify_sum_balance(items)
    assert check.balanced
    assert check.difference_rappen == 0


def test_verify_sum_balance_flags_large_mismatch():
    """A large mismatch well outside rounding tolerance is flagged as unbalanced."""
    items = [
        BillingRunItem(id=None, billing_run_id=0, participant_id=1, kind="rechnung", kwh=100, price_rp_per_kwh=12, amount_rappen=1200, pdf_path=None, created_at=""),
        BillingRunItem(id=None, billing_run_id=0, participant_id=2, kind="gutschrift", kwh=1, price_rp_per_kwh=12, amount_rappen=120, pdf_path=None, created_at=""),
    ]
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


def test_full_billing_run_summer_quarter_balances_and_bills_prosumers(db):
    """With demo data, the summer run produces balanced, nonzero invoices and credits."""
    create_demo_data(db)
    run, items, control_check, distribution = create_or_replace_billing_run(db, *SUMMER_QUARTER)

    assert items, "expected nonzero billing items for the summer quarter"
    assert control_check.balanced, (
        f"invoices={control_check.total_invoices_rappen} "
        f"credits={control_check.total_credits_rappen}"
    )
    assert any(i.kind == "rechnung" for i in items)
    assert any(i.kind == "gutschrift" for i in items)

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
