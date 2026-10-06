"""Turns a quarter's distribution result into one combined billing item per person, and a sum-balance
control check (project brief, section 5, points 6-9)."""

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
    """Outcome of verifying that the LEG is a pure pass-through, energy-wise."""

    total_owed_to_leg_rappen: int
    total_owed_by_leg_rappen: int
    difference_rappen: int
    tolerance_rappen: int
    balanced: bool


def round_to_rappen(amount_rappen: float) -> int:
    """Round a fractional Rappen amount to the nearest whole Rappen."""
    return int(Decimal(str(amount_rappen)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_billing_items(
    distribution: DistributionResult,
    price_rp_per_kwh: float,
    admin_fee_consumption_rp_per_kwh: float,
    admin_fee_feed_in_rp_per_kwh: float,
    paper_invoice_rappen: int,
    paper_invoice_by_person: dict[int, bool],
) -> list[BillingRunItem]:
    """Derive one combined, netted billing item per person."""
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
    """Check that money owed to the LEG balances money owed by the LEG, energy-wise."""
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
    """Compute and persist a full billing run for one LEG and quarter."""
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
    """What happened when one LEG was billed as part of a run over all of them."""

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
    """Bill every LEG for one quarter, in one pass."""
    from app.pdf.export_service import export_billing_run_documents

    outcomes: list[LegRunOutcome] = []
    for leg in leg_repo.list_all(connection):
        # A failed replacement must not leave the old run deleted (or a new
        # run half-written). Per-LEG savepoints let the rest of the batch
        # continue while keeping each individual replacement atomic.
        savepoint = f"billing_leg_{leg.id}"
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            run, items, control_check, _ = create_or_replace_billing_run(connection, leg.id, year, quarter)
        except LegNotAssignedError:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        except Exception as exc:  # noqa: BLE001 -- one LEG must not stop the rest
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            outcomes.append(LegRunOutcome(leg=leg, error=str(exc)))
            continue
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")

        outcome = LegRunOutcome(leg=leg, run=run, items=items, control_check=control_check)
        if export:
            try:
                outcome.export = export_billing_run_documents(connection, run)
            except Exception as exc:  # noqa: BLE001 -- the run itself is already saved
                outcome.error = f"Abrechnung erstellt, Export fehlgeschlagen: {exc}"
        outcomes.append(outcome)

    return outcomes
