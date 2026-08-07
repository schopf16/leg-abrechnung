"""Turns a quarter's distribution result into one combined billing item per
person, and a sum-balance control check (project brief, section 5,
points 6-9).

Every person gets exactly one document per LEG they participate in: their
locally-sourced consumption ("Bezug") and locally-delivered production
("Vergütung") are computed independently and only *netted together at the
very end* -- `consumed_value - produced_value` -- with rounding to the
nearest Rappen happening exactly once, on that energy net amount.
Intermediate figures (kWh, subtotal values) stay at full precision and are
only *formatted* for display, never independently rounded and re-added,
so rounding error can never compound across a document.

On top of that energy net, two admin fees are added -- each its own
distinct, independently rounded (or exact, for the flat fee) billed line,
not subject to the "round only once" rule above since they are not
derived from repeated addition of the same rounded figure:

- `verwaltungsaufwand_rappen`: `consumed_local_kwh * verwaltungsaufwand_rp_per_kwh`,
  rounded to the nearest Rappen. Charged on consumption only, never on
  production.
- `papierrechnung_rappen`: a flat fee, copied verbatim from
  `LegSettings.papierrechnung_rappen` if the person has
  `Person.papierrechnung` set, else 0.

These fees are pure LEG revenue with no producer-side counterpart, so
`verify_sum_balance` deliberately excludes them and checks only the energy
portion -- see its docstring.
"""

import sqlite3
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.domain.distribution import DistributionResult, compute_quarter_distribution
from app.models import billing_run as billing_run_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRun, BillingRunItem


@dataclass
class ControlCheckResult:
    """Outcome of verifying that the LEG is a pure pass-through, energy-wise.

    At a uniform price, every interval's shared energy `S(t)` is split
    identically between its consumption side and its production side (see
    the distribution engine's docstring), so before rounding, the sum of
    every person's *energy* net amount (`consumed_value - produced_value`,
    excluding the admin fees added on top -- see the module docstring)
    across the whole run is exactly zero -- money owed *to* the LEG by
    consumers equals money owed *by* the LEG to producers. Independent
    per-person Rappen rounding can introduce a tiny difference,
    accepted only up to `tolerance_rappen`.

    Attributes:
        total_owed_to_leg_rappen: Sum of all positive energy net amounts
            (money consumers owe the LEG for energy, excluding admin
            fees), in Rappen.
        total_owed_by_leg_rappen: Sum of the absolute value of all
            negative energy net amounts (money the LEG owes producers),
            in Rappen.
        difference_rappen: `total_owed_to_leg_rappen - total_owed_by_leg_rappen`.
        tolerance_rappen: Maximum acceptable absolute difference, one
            Rappen per person (worst-case independent rounding).
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
    This is the *only* place rounding happens for the energy net amount in
    the billing computation (the admin fees are rounded independently,
    see the module docstring).

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
    distribution: DistributionResult,
    price_rp_per_kwh: float,
    verwaltungsaufwand_rp_per_kwh: float,
    papierrechnung_rappen: int,
    papierrechnung_by_person: dict[int, bool],
) -> list[BillingRunItem]:
    """Derive one combined, netted billing item per person.

    A person with locally-sourced consumption and/or locally-delivered
    production during the quarter (on this LEG) gets exactly one item;
    persons with neither (no local sharing at all) get none -- the admin
    fees below only ever apply on top of an existing energy-based item,
    never as a standalone charge for a person with zero local sharing.

    Args:
        distribution: Result of `compute_quarter_distribution` for one LEG.
        price_rp_per_kwh: Internal energy price in Rappen per kWh.
        verwaltungsaufwand_rp_per_kwh: Administrative surcharge in Rappen
            per kWh, charged on `consumed_local_kwh` only.
        papierrechnung_rappen: Flat paper-invoice fee in Rappen, applied
            to persons present (and `True`) in `papierrechnung_by_person`.
        papierrechnung_by_person: Whether each person receives a paper
            invoice (see `Person.papierrechnung`), keyed by person id.
            A person missing from this dict is treated as `False`.

    Returns:
        Unpersisted `BillingRunItem` instances (`id`, `billing_run_id` and
        `created_at` left as placeholders for the caller to fill in).
    """
    items: list[BillingRunItem] = []
    for person_id, totals in sorted(distribution.person_results.items()):
        if totals.consumed_local_kwh <= 0 and totals.produced_local_kwh <= 0:
            continue

        consumed_value_rappen = totals.consumed_local_kwh * price_rp_per_kwh
        produced_value_rappen = totals.produced_local_kwh * price_rp_per_kwh
        energy_net_rappen = round_to_rappen(consumed_value_rappen - produced_value_rappen)

        verwaltungsaufwand = round_to_rappen(
            totals.consumed_local_kwh * verwaltungsaufwand_rp_per_kwh
        )
        papierrechnung = papierrechnung_rappen if papierrechnung_by_person.get(person_id) else 0

        items.append(
            BillingRunItem(
                id=None,
                billing_run_id=0,
                person_id=person_id,
                consumed_kwh=totals.consumed_local_kwh,
                produced_kwh=totals.produced_local_kwh,
                price_rp_per_kwh=price_rp_per_kwh,
                verwaltungsaufwand_rappen=verwaltungsaufwand,
                papierrechnung_rappen=papierrechnung,
                net_amount_rappen=energy_net_rappen + verwaltungsaufwand + papierrechnung,
                pdf_path=None,
                created_at="",
            )
        )
    return items


def verify_sum_balance(items: list[BillingRunItem]) -> ControlCheckResult:
    """Check that money owed to the LEG balances money owed by the LEG, energy-wise.

    Admin fees (`verwaltungsaufwand_rappen`, `papierrechnung_rappen`) are
    deliberately excluded -- they are pure LEG revenue with no matching
    producer-side payout, so including them would make this check flag a
    perfectly healthy run as "unbalanced". See the module docstring.

    Args:
        items: Netted billing run items for one run.

    Returns:
        A `ControlCheckResult` describing the balance and whether it is
        within the accepted rounding tolerance.
    """
    energy_net_by_item = [
        i.net_amount_rappen - i.verwaltungsaufwand_rappen - i.papierrechnung_rappen
        for i in items
    ]
    total_owed_to_leg = sum(n for n in energy_net_by_item if n > 0)
    total_owed_by_leg = sum(-n for n in energy_net_by_item if n < 0)
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
    connection: sqlite3.Connection, leg_id: int, year: int, quarter: int
) -> tuple[BillingRun, list[BillingRunItem], ControlCheckResult, DistributionResult]:
    """Compute and persist a full billing run for one LEG and quarter.

    If a run for the same `(leg_id, year, quarter)` already exists, it is
    deleted first (cascading to its line items) so that re-running billing
    after a late import or a price correction always yields a clean,
    consistent result rather than accumulating duplicates.

    Args:
        connection: Open SQLite connection.
        leg_id: The LEG to bill.
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.

    Returns:
        A tuple of `(billing_run, items, control_check, distribution)`,
        where `items` have their `id` and `billing_run_id` populated as
        persisted.
    """
    existing = billing_run_repo.get_run_by_period(connection, leg_id, year, quarter)
    if existing is not None:
        billing_run_repo.delete_run(connection, existing.id)

    settings = settings_repo.get_settings(connection)
    distribution = compute_quarter_distribution(connection, leg_id, year, quarter)
    papierrechnung_by_person = {p.id: p.papierrechnung for p in person_repo.list_all(connection)}
    items = compute_billing_items(
        distribution,
        settings.price_rp_per_kwh,
        settings.verwaltungsaufwand_rp_per_kwh,
        settings.papierrechnung_rappen,
        papierrechnung_by_person,
    )
    control_check = verify_sum_balance(items)

    run_id = billing_run_repo.create_run(
        connection,
        BillingRun(
            id=None,
            leg_id=leg_id,
            period_year=year,
            period_quarter=quarter,
            created_at="",
            price_rp_per_kwh=settings.price_rp_per_kwh,
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
