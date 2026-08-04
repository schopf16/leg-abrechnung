"""Turns a quarter's distribution result into one combined billing item per
participant, and a sum-balance control check (project brief, section 5,
points 6-9).

Every participant gets exactly one document: their locally-sourced
consumption ("Bezug") and locally-delivered production ("Vergütung") are
computed independently and only *netted together at the very end* --
`consumed_value - produced_value` -- with rounding to the nearest Rappen
happening exactly once, on that final net amount. Intermediate figures
(monthly and quarterly kWh, subtotal values) stay at full precision and
are only *formatted* for display, never independently rounded and re-added,
so rounding error can never compound across a document.
"""

import sqlite3
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.domain.distribution import DistributionResult, compute_quarter_distribution
from app.models import billing_run as billing_run_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRun, BillingRunItem


@dataclass
class ControlCheckResult:
    """Outcome of verifying that the LEG is a pure pass-through.

    At a uniform price, every interval's shared energy `S(t)` is split
    identically between its consumption side and its production side (see
    the distribution engine's docstring), so before rounding, the sum of
    every participant's net amount (`consumed_value - produced_value`)
    across the whole run is exactly zero -- money owed *to* the LEG by
    consumers equals money owed *by* the LEG to producers. Independent
    per-participant Rappen rounding can introduce a tiny difference,
    accepted only up to `tolerance_rappen`.

    Attributes:
        total_owed_to_leg_rappen: Sum of all positive net amounts (money
            consumers owe the LEG), in Rappen.
        total_owed_by_leg_rappen: Sum of the absolute value of all negative
            net amounts (money the LEG owes producers), in Rappen.
        difference_rappen: `total_owed_to_leg_rappen - total_owed_by_leg_rappen`.
        tolerance_rappen: Maximum acceptable absolute difference, one
            Rappen per participant (worst-case independent rounding).
        balanced: Whether `difference_rappen` is within tolerance.
    """

    total_owed_to_leg_rappen: int
    total_owed_by_leg_rappen: int
    difference_rappen: int
    tolerance_rappen: int
    balanced: bool


def round_to_rappen(amount_rappen: float) -> int:
    """Round a fractional Rappen amount to the nearest whole Rappen.

    Uses standard "round half up" as is customary for Swiss franc amounts,
    via `Decimal` to avoid binary floating-point surprises at the boundary.
    This is the *only* place rounding happens in the billing computation.

    Args:
        amount_rappen: Amount in Rappen (1/100 CHF), typically
            `kwh * price_rp_per_kwh`.

    Returns:
        The rounded amount as an integer number of Rappen.
    """
    return int(
        Decimal(str(amount_rappen)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def compute_billing_items(
    distribution: DistributionResult, price_rp_per_kwh: float
) -> list[BillingRunItem]:
    """Derive one combined, netted billing item per participant.

    A participant with locally-sourced consumption and/or locally-delivered
    production during the quarter gets exactly one item; participants with
    neither (no local sharing at all) get none. Consumption value and
    production value are computed at full precision and only netted --
    and rounded -- at the very end, per participant.

    Args:
        distribution: Result of `compute_quarter_distribution`.
        price_rp_per_kwh: Internal energy price in Rappen per kWh.

    Returns:
        Unpersisted `BillingRunItem` instances (`id`, `billing_run_id` and
        `created_at` left as placeholders for the caller to fill in).
    """
    items: list[BillingRunItem] = []
    for participant_id, totals in sorted(distribution.participant_results.items()):
        if totals.consumed_local_kwh <= 0 and totals.produced_local_kwh <= 0:
            continue

        consumed_value_rappen = totals.consumed_local_kwh * price_rp_per_kwh
        produced_value_rappen = totals.produced_local_kwh * price_rp_per_kwh
        net_value_rappen = consumed_value_rappen - produced_value_rappen

        items.append(
            BillingRunItem(
                id=None,
                billing_run_id=0,
                participant_id=participant_id,
                consumed_kwh=totals.consumed_local_kwh,
                produced_kwh=totals.produced_local_kwh,
                price_rp_per_kwh=price_rp_per_kwh,
                net_amount_rappen=round_to_rappen(net_value_rappen),
                pdf_path=None,
                created_at="",
            )
        )
    return items


def verify_sum_balance(items: list[BillingRunItem]) -> ControlCheckResult:
    """Check that money owed to the LEG balances money owed by the LEG.

    Args:
        items: Netted billing run items for one run.

    Returns:
        A `ControlCheckResult` describing the balance and whether it is
        within the accepted rounding tolerance.
    """
    total_owed_to_leg = sum(i.net_amount_rappen for i in items if i.net_amount_rappen > 0)
    total_owed_by_leg = sum(-i.net_amount_rappen for i in items if i.net_amount_rappen < 0)
    difference = total_owed_to_leg - total_owed_by_leg
    tolerance = max(1, len(items))
    return ControlCheckResult(
        total_owed_to_leg_rappen=total_owed_to_leg,
        total_owed_by_leg_rappen=total_owed_by_leg,
        difference_rappen=difference,
        tolerance_rappen=tolerance,
        balanced=abs(difference) <= tolerance,
    )


def create_or_replace_billing_run(
    connection: sqlite3.Connection, year: int, quarter: int
) -> tuple[BillingRun, list[BillingRunItem], ControlCheckResult, DistributionResult]:
    """Compute and persist a full billing run for one quarter.

    If a run for the same `(year, quarter)` already exists, it is deleted
    first (cascading to its line items) so that re-running billing after a
    late import or a price correction always yields a clean, consistent
    result rather than accumulating duplicates.

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        A tuple of `(billing_run, items, control_check, distribution)`,
        where `items` have their `id` and `billing_run_id` populated as
        persisted.
    """
    existing = billing_run_repo.get_run_by_period(connection, year, quarter)
    if existing is not None:
        billing_run_repo.delete_run(connection, existing.id)

    price_rp_per_kwh = settings_repo.get_settings(connection).price_rp_per_kwh
    distribution = compute_quarter_distribution(connection, year, quarter)
    items = compute_billing_items(distribution, price_rp_per_kwh)
    control_check = verify_sum_balance(items)

    run_id = billing_run_repo.create_run(
        connection,
        BillingRun(
            id=None,
            period_year=year,
            period_quarter=quarter,
            created_at="",
            price_rp_per_kwh=price_rp_per_kwh,
            status="erstellt",
            notes="",
        ),
    )
    for item in items:
        item.billing_run_id = run_id
    item_ids = billing_run_repo.add_items(connection, items)
    for item, item_id in zip(items, item_ids):
        item.id = item_id

    run = billing_run_repo.get_run(connection, run_id)
    return run, items, control_check, distribution
