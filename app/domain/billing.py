"""Turns a quarter's distribution result into one combined billing item per
person, and a sum-balance control check (project brief, section 5,
points 6-9).

Every person gets exactly one document per LEG they participate in: their
locally-sourced consumption ("consumption") and locally-delivered production
("Vergütung") are computed independently and only *netted together at the
very end* -- `consumed_value - produced_value` -- with rounding to the
nearest Rappen happening exactly once, on that energy net amount.
Intermediate figures (kWh, subtotal values) stay at full precision and are
only *formatted* for display, never independently rounded and re-added,
so rounding error can never compound across a document.

On top of that energy net, three admin fees are added -- each its own
distinct, independently rounded (or exact, for the flat fee) billed line,
not subject to the "round only once" rule above since they are not
derived from repeated addition of the same rounded figure:

- `admin_fee_consumption_rappen`: `consumed_local_kwh *
  admin_fee_consumption_rp_per_kwh`, rounded to the nearest Rappen.
- `admin_fee_feed_in_rappen`: `produced_local_kwh *
  admin_fee_feed_in_rp_per_kwh`, rounded to the nearest
  Rappen. Independent of the consumption rate above -- either can be zero while
  the other is not.
- `paper_invoice_rappen`: a flat fee, copied verbatim from
  `LegSettings.paper_invoice_rappen` if the person has
  `Person.paper_invoice` set, else 0.

Both `admin_fee_*_rp_per_kwh` rates actually used are frozen
directly onto each `BillingRunItem` (mirroring how `price_rp_per_kwh` is
already frozen there) -- once a run is created, changing the rate in
`LegSettings` never alters what an already-billed item appears to have
charged; it only takes effect for runs created afterwards.

These fees are pure LEG revenue with no producer-side counterpart, so
`verify_sum_balance` deliberately excludes them and checks only the energy
portion -- see its docstring.
"""

import sqlite3
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.domain.distribution import (
    DistributionResult,
    LegNotAssignedError,
    compute_quarter_distribution,
)
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRun, BillingRunItem
from app.models.leg import Leg


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
            Rappen per person whose item carries energy (worst-case
            independent rounding). Items reading zero are excluded: they
            round nothing, so counting them would only loosen the check.
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
    return int(Decimal(str(amount_rappen)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_billing_items(
    distribution: DistributionResult,
    price_rp_per_kwh: float,
    admin_fee_consumption_rp_per_kwh: float,
    admin_fee_feed_in_rp_per_kwh: float,
    paper_invoice_rappen: int,
    paper_invoice_by_person: dict[int, bool],
) -> list[BillingRunItem]:
    """Derive one combined, netted billing item per person.

    Every person the distribution covers gets exactly one item --
    including one whose meters shared nothing at all this quarter, whose
    document then reads 0.00 CHF. Who is covered is decided by the
    assignments, not by the energy (see
    `app.domain.distribution._seed_participants`): a participant has to
    be able to see that their quarter was settled, and a bill's line-up
    must not change from quarter to quarter.

    The flat paper-invoice fee is the one figure withheld from such a
    zero document: charging 2 francs for a statement reading 0.00 is not
    defensible, and a quarter with no local sharing at all would
    otherwise become an invoice run for the fee alone. The per-kWh admin
    fees need no such rule -- they are zero on zero energy by themselves.

    Args:
        distribution: Result of `compute_quarter_distribution` for one LEG.
        price_rp_per_kwh: Internal energy price in Rappen per kWh.
        admin_fee_consumption_rp_per_kwh: Administrative surcharge in
            Rappen per kWh, charged on `consumed_local_kwh`.
        admin_fee_feed_in_rp_per_kwh: Administrative
            surcharge in Rappen per kWh, charged on `produced_local_kwh`
            -- independent of the consumption rate above.
        paper_invoice_rappen: Flat paper-invoice fee in Rappen, applied
            to persons present (and `True`) in `paper_invoice_by_person`.
        paper_invoice_by_person: Whether each person receives a paper
            invoice (see `Person.paper_invoice`), keyed by person id.
            A person missing from this dict is treated as `False`.

    Returns:
        Unpersisted `BillingRunItem` instances (`id`, `billing_run_id` and
        `created_at` left as placeholders for the caller to fill in). Both
        `admin_fee_*_rp_per_kwh` rates actually used are frozen
        onto each item (see module docstring).
    """
    items: list[BillingRunItem] = []
    for person_id, totals in sorted(distribution.person_results.items()):
        shared_anything = totals.consumed_local_kwh > 0 or totals.produced_local_kwh > 0

        consumed_value_rappen = totals.consumed_local_kwh * price_rp_per_kwh
        produced_value_rappen = totals.produced_local_kwh * price_rp_per_kwh
        energy_net_rappen = round_to_rappen(consumed_value_rappen - produced_value_rappen)

        admin_fee_consumption = round_to_rappen(totals.consumed_local_kwh * admin_fee_consumption_rp_per_kwh)
        admin_fee_feed_in = round_to_rappen(totals.produced_local_kwh * admin_fee_feed_in_rp_per_kwh)
        # No flat fee on a document that charges nothing: billing someone
        # 2 francs for a statement reading 0.00 is not defensible, and a
        # quarter with no local sharing at all would otherwise turn into
        # an invoice run for the paper fee alone.
        paper_invoice = (
            paper_invoice_rappen if shared_anything and paper_invoice_by_person.get(person_id) else 0
        )

        items.append(
            BillingRunItem(
                id=None,
                billing_run_id=0,
                person_id=person_id,
                consumed_kwh=totals.consumed_local_kwh,
                produced_kwh=totals.produced_local_kwh,
                price_rp_per_kwh=price_rp_per_kwh,
                admin_fee_consumption_rappen=admin_fee_consumption,
                admin_fee_feed_in_rappen=admin_fee_feed_in,
                admin_fee_consumption_rp_per_kwh=admin_fee_consumption_rp_per_kwh,
                admin_fee_feed_in_rp_per_kwh=admin_fee_feed_in_rp_per_kwh,
                paper_invoice_rappen=paper_invoice,
                net_amount_rappen=(
                    energy_net_rappen + admin_fee_consumption + admin_fee_feed_in + paper_invoice
                ),
                pdf_path=None,
                created_at="",
            )
        )
    return items


def verify_sum_balance(items: list[BillingRunItem]) -> ControlCheckResult:
    """Check that money owed to the LEG balances money owed by the LEG, energy-wise.

    Admin fees (`admin_fee_consumption_rappen`,
    `admin_fee_feed_in_rappen`, `paper_invoice_rappen`) are
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
        i.net_amount_rappen
        - i.admin_fee_consumption_rappen
        - i.admin_fee_feed_in_rappen
        - i.paper_invoice_rappen
        for i in items
    ]
    total_owed_to_leg = sum(n for n in energy_net_by_item if n > 0)
    total_owed_by_leg = sum(-n for n in energy_net_by_item if n < 0)
    difference = total_owed_to_leg - total_owed_by_leg
    # Counted over the items that actually carry energy, not all of them:
    # every participant now gets an item, and one reading 0.000 kWh
    # introduces no rounding error, so letting it widen the tolerance
    # would quietly weaken the check as the community grows.
    rounded_items = sum(1 for i in items if i.consumed_kwh > 0 or i.produced_kwh > 0)
    tolerance = max(1, rounded_items)
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
    paper_invoice_by_person = {p.id: p.paper_invoice for p in person_repo.list_all(connection)}
    items = compute_billing_items(
        distribution,
        settings.price_rp_per_kwh,
        settings.admin_fee_consumption_rp_per_kwh,
        settings.admin_fee_feed_in_rp_per_kwh,
        settings.paper_invoice_rappen,
        paper_invoice_by_person,
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
            status="created",
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


@dataclass
class LegRunOutcome:
    """What happened when one LEG was billed as part of a run over all of them.

    Attributes:
        leg: The LEG this outcome belongs to.
        run: The persisted run, or `None` if it could not be created.
        items: The run's line items, empty on failure.
        control_check: The sum balance, or `None` on failure.
        export: The document export result, or `None` if not exported
            (either because billing failed, or because the caller asked
            for computation only).
        error: A German message explaining the failure, or `None`.
    """

    leg: Leg
    run: Optional[BillingRun] = None
    items: list[BillingRunItem] = field(default_factory=list)
    control_check: Optional[ControlCheckResult] = None
    export: Optional[object] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        """Whether this LEG was billed without error."""
        return self.error is None and self.run is not None

    @property
    def total_invoiced_rappen(self) -> int:
        """Sum of everything this LEG's members owe, in Rappen."""
        return sum(i.net_amount_rappen for i in self.items if i.is_owed_to_leg)

    @property
    def total_credited_rappen(self) -> int:
        """Sum of everything this LEG owes its members, in Rappen."""
        return sum(-i.net_amount_rappen for i in self.items if i.is_owed_by_leg)


def create_billing_runs_for_all_legs(
    connection: sqlite3.Connection, year: int, quarter: int, *, export: bool = True
) -> list[LegRunOutcome]:
    """Bill every LEG for one quarter, in one pass.

    Billing one LEG at a time is how a LEG gets forgotten -- there is
    nothing in the per-LEG flow that says which ones are still
    outstanding, and the omission only shows up when a member asks why
    they got no invoice. This covers all of them and reports per LEG.

    A LEG that fails does not stop the others: its message lands in its
    own `LegRunOutcome`, and the remaining LEGs are still billed. The one
    exception is `app.domain.distribution.LegNotAssignedError`, which is
    deliberately deployment-wide -- a metering point with readings but no
    LEG would otherwise be silently missing from *every* run, so it
    aborts the whole pass and is raised to the caller.

    Args:
        connection: Open SQLite connection.
        year: Calendar year of the billing quarter.
        quarter: Quarter number, 1 to 4.
        export: Whether to also write each LEG's documents. Kept as a
            parameter so tests can bill without touching the filesystem.

    Returns:
        One `LegRunOutcome` per LEG, in the repo's own order.

    Raises:
        LegNotAssignedError: If any metering point with readings in this
            quarter has no LEG assigned.
    """
    from app.pdf.export_service import export_billing_run_documents

    outcomes: list[LegRunOutcome] = []
    for leg in leg_repo.list_all(connection):
        try:
            run, items, control_check, _ = create_or_replace_billing_run(connection, leg.id, year, quarter)
        except LegNotAssignedError:
            raise
        except Exception as exc:  # noqa: BLE001 -- one LEG must not stop the rest
            outcomes.append(LegRunOutcome(leg=leg, error=str(exc)))
            continue

        outcome = LegRunOutcome(leg=leg, run=run, items=items, control_check=control_check)
        if export:
            try:
                outcome.export = export_billing_run_documents(connection, run)
            except Exception as exc:  # noqa: BLE001 -- the run itself is already saved
                outcome.error = f"Abrechnung erstellt, Export fehlgeschlagen: {exc}"
        outcomes.append(outcome)

    return outcomes
