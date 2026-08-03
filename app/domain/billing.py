"""Turns a quarter's distribution result into invoices, credit notes and a
sum-balance control check (project brief, section 5, points 6-8).

Prosumers (consumption *and* production) always get both a separate
invoice and a separate credit note -- amounts are never netted.
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
    """Outcome of verifying that invoices and credit notes balance.

    At a uniform price, the sum of all invoices must equal the sum of all
    credit notes (see the distribution engine's docstring: every interval's
    shared energy `S(t)` is split identically on both sides). Independent
    per-item Rappen rounding can introduce a tiny difference, which is
    accepted only up to `tolerance_rappen`.

    Attributes:
        total_invoices_rappen: Sum of all "rechnung" line items, in Rappen.
        total_credits_rappen: Sum of all "gutschrift" line items, in Rappen.
        difference_rappen: `total_invoices_rappen - total_credits_rappen`.
        tolerance_rappen: Maximum acceptable absolute difference, one
            Rappen per line item (worst-case independent rounding).
        balanced: Whether `difference_rappen` is within tolerance.
    """

    total_invoices_rappen: int
    total_credits_rappen: int
    difference_rappen: int
    tolerance_rappen: int
    balanced: bool


def _round_to_rappen(amount_rappen: float) -> int:
    """Round a fractional Rappen amount to the nearest whole Rappen.

    Uses standard "round half up" as is customary for Swiss franc amounts,
    via `Decimal` to avoid binary floating-point surprises at the boundary.

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
    """Derive invoice and credit note line items from a distribution result.

    A participant with zero locally-sourced consumption gets no invoice
    line; a participant with zero locally-delivered production gets no
    credit note line. A prosumer with both gets one of each.

    Args:
        distribution: Result of `compute_quarter_distribution`.
        price_rp_per_kwh: Internal energy price in Rappen per kWh.

    Returns:
        Unpersisted `BillingRunItem` instances (`id`, `billing_run_id` and
        `created_at` left as placeholders for the caller to fill in).
    """
    items: list[BillingRunItem] = []
    for participant_id, totals in sorted(distribution.participant_results.items()):
        if totals.consumed_local_kwh > 0:
            amount = totals.consumed_local_kwh * price_rp_per_kwh
            items.append(
                BillingRunItem(
                    id=None,
                    billing_run_id=0,
                    participant_id=participant_id,
                    kind="rechnung",
                    kwh=totals.consumed_local_kwh,
                    price_rp_per_kwh=price_rp_per_kwh,
                    amount_rappen=_round_to_rappen(amount),
                    pdf_path=None,
                    created_at="",
                )
            )
        if totals.produced_local_kwh > 0:
            amount = totals.produced_local_kwh * price_rp_per_kwh
            items.append(
                BillingRunItem(
                    id=None,
                    billing_run_id=0,
                    participant_id=participant_id,
                    kind="gutschrift",
                    kwh=totals.produced_local_kwh,
                    price_rp_per_kwh=price_rp_per_kwh,
                    amount_rappen=_round_to_rappen(amount),
                    pdf_path=None,
                    created_at="",
                )
            )
    return items


def verify_sum_balance(items: list[BillingRunItem]) -> ControlCheckResult:
    """Check that total invoices and total credit notes balance out.

    Args:
        items: Billing run line items (invoices and credit notes mixed).

    Returns:
        A `ControlCheckResult` describing the balance and whether it is
        within the accepted rounding tolerance.
    """
    total_invoices = sum(i.amount_rappen for i in items if i.kind == "rechnung")
    total_credits = sum(i.amount_rappen for i in items if i.kind == "gutschrift")
    difference = total_invoices - total_credits
    tolerance = max(1, len(items))
    return ControlCheckResult(
        total_invoices_rappen=total_invoices,
        total_credits_rappen=total_credits,
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
