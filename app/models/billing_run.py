"""Persisted billing runs (Abrechnungsläufe) and their line items."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class BillingRun:
    """One quarterly billing run, scoped to exactly one LEG."""

    id: Optional[int]
    leg_id: int
    period_year: int
    period_quarter: int
    created_at: str
    price_rp_per_kwh: float
    status: str
    notes: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "BillingRun":
        """Build a `BillingRun` from a `sqlite3.Row`."""
        return BillingRun(
            id=row["id"],
            leg_id=row["leg_id"],
            period_year=row["period_year"],
            period_quarter=row["period_quarter"],
            created_at=row["created_at"],
            price_rp_per_kwh=row["price_rp_per_kwh"],
            status=row["status"],
            notes=row["notes"],
        )


@dataclass
class BillingRunItem:
    """One person's combined billing document within a billing run."""

    id: Optional[int]
    billing_run_id: int
    person_id: int
    consumed_kwh: float
    produced_kwh: float
    price_rp_per_kwh: float
    admin_fee_consumption_rappen: int
    paper_invoice_rappen: int
    net_amount_rappen: int
    pdf_path: Optional[str]
    created_at: str
    email_sent_at: Optional[str] = None
    due_date: Optional[str] = None
    dunning_level: int = 0
    last_dunning_at: Optional[str] = None
    admin_fee_feed_in_rappen: int = 0
    admin_fee_consumption_rp_per_kwh: float = 0.0
    admin_fee_feed_in_rp_per_kwh: float = 0.0
    dunning_deadline_days: Optional[int] = None

    @property
    def net_amount_chf(self) -> float:
        """Net amount in Swiss francs, derived from `net_amount_rappen`."""
        return self.net_amount_rappen / 100

    @property
    def is_owed_to_leg(self) -> bool:
        """Whether the person owes the LEG money (a real, payable invoice)."""
        return self.net_amount_rappen > 0

    @property
    def is_owed_by_leg(self) -> bool:
        """Whether the LEG owes the person a payout."""
        return self.net_amount_rappen < 0

    @staticmethod
    def from_row(row: sqlite3.Row) -> "BillingRunItem":
        """Build a `BillingRunItem` from a `sqlite3.Row`."""
        return BillingRunItem(
            id=row["id"],
            billing_run_id=row["billing_run_id"],
            person_id=row["person_id"],
            consumed_kwh=row["consumed_kwh"],
            produced_kwh=row["produced_kwh"],
            price_rp_per_kwh=row["price_rp_per_kwh"],
            admin_fee_consumption_rappen=row["admin_fee_consumption_rappen"],
            paper_invoice_rappen=row["paper_invoice_rappen"],
            net_amount_rappen=row["net_amount_rappen"],
            pdf_path=row["pdf_path"],
            created_at=row["created_at"],
            email_sent_at=row["email_sent_at"],
            due_date=row["due_date"],
            dunning_level=row["dunning_level"],
            last_dunning_at=row["last_dunning_at"],
            admin_fee_feed_in_rappen=row["admin_fee_feed_in_rappen"],
            admin_fee_consumption_rp_per_kwh=row["admin_fee_consumption_rp_per_kwh"],
            admin_fee_feed_in_rp_per_kwh=row["admin_fee_feed_in_rp_per_kwh"],
            dunning_deadline_days=row["dunning_deadline_days"],
        )


def list_runs(connection: sqlite3.Connection) -> list[BillingRun]:
    """List all billing runs (across all LEGs), most recent quarter first."""
    rows = connection.execute(
        "SELECT * FROM billing_runs ORDER BY period_year DESC, period_quarter DESC"
    ).fetchall()
    return [BillingRun.from_row(row) for row in rows]


def list_runs_for_leg(connection: sqlite3.Connection, leg_id: int) -> list[BillingRun]:
    """List all billing runs for one LEG, most recent quarter first."""
    rows = connection.execute(
        """
        SELECT * FROM billing_runs WHERE leg_id = ?
        ORDER BY period_year DESC, period_quarter DESC
        """,
        (leg_id,),
    ).fetchall()
    return [BillingRun.from_row(row) for row in rows]


def get_run(connection: sqlite3.Connection, run_id: int) -> Optional[BillingRun]:
    """Fetch a single billing run by id."""
    row = connection.execute("SELECT * FROM billing_runs WHERE id = ?", (run_id,)).fetchone()
    return BillingRun.from_row(row) if row else None


def get_run_by_period(
    connection: sqlite3.Connection, leg_id: int, year: int, quarter: int
) -> Optional[BillingRun]:
    """Fetch a LEG's billing run for a calendar year and quarter."""
    row = connection.execute(
        """
        SELECT * FROM billing_runs
        WHERE leg_id = ? AND period_year = ? AND period_quarter = ?
        """,
        (leg_id, year, quarter),
    ).fetchone()
    return BillingRun.from_row(row) if row else None


def delete_run(connection: sqlite3.Connection, run_id: int, *, commit: bool = True) -> None:
    """Delete a billing run and all its line items (cascade)."""
    connection.execute("DELETE FROM billing_runs WHERE id = ?", (run_id,))
    if commit:
        connection.commit()


def create_run(connection: sqlite3.Connection, run: BillingRun, *, commit: bool = True) -> int:
    """Insert a new billing run."""
    cursor = connection.execute(
        """
        INSERT INTO billing_runs
            (leg_id, period_year, period_quarter, created_at, price_rp_per_kwh, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.leg_id,
            run.period_year,
            run.period_quarter,
            datetime.now(timezone.utc).isoformat(),
            run.price_rp_per_kwh,
            run.status,
            run.notes,
        ),
    )
    if commit:
        connection.commit()
    return cursor.lastrowid


def add_items(
    connection: sqlite3.Connection, items: list[BillingRunItem], *, commit: bool = True
) -> list[int]:
    """Insert billing run line items."""
    ids = []
    for item in items:
        cursor = connection.execute(
            """
            INSERT INTO billing_run_items
                (billing_run_id, person_id, consumed_kwh, produced_kwh,
                 price_rp_per_kwh, admin_fee_consumption_rappen,
                 admin_fee_feed_in_rappen,
                 admin_fee_consumption_rp_per_kwh, admin_fee_feed_in_rp_per_kwh,
                 paper_invoice_rappen, net_amount_rappen, pdf_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.billing_run_id,
                item.person_id,
                item.consumed_kwh,
                item.produced_kwh,
                item.price_rp_per_kwh,
                item.admin_fee_consumption_rappen,
                item.admin_fee_feed_in_rappen,
                item.admin_fee_consumption_rp_per_kwh,
                item.admin_fee_feed_in_rp_per_kwh,
                item.paper_invoice_rappen,
                item.net_amount_rappen,
                item.pdf_path,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        ids.append(cursor.lastrowid)
    if commit:
        connection.commit()
    return ids


def list_items(connection: sqlite3.Connection, billing_run_id: int) -> list[BillingRunItem]:
    """List all line items of a billing run."""
    rows = connection.execute(
        """
        SELECT * FROM billing_run_items
        WHERE billing_run_id = ?
        ORDER BY person_id
        """,
        (billing_run_id,),
    ).fetchall()
    return [BillingRunItem.from_row(row) for row in rows]


def list_items_for_person(connection: sqlite3.Connection, person_id: int) -> list[BillingRunItem]:
    """List every billing run item ever created for one person, across all billing runs (any LEG, any..."""
    rows = connection.execute(
        "SELECT * FROM billing_run_items WHERE person_id = ? ORDER BY created_at, id",
        (person_id,),
    ).fetchall()
    return [BillingRunItem.from_row(row) for row in rows]


def get_item(connection: sqlite3.Connection, item_id: int) -> Optional[BillingRunItem]:
    """Fetch a single billing run line item by id."""
    row = connection.execute("SELECT * FROM billing_run_items WHERE id = ?", (item_id,)).fetchone()
    return BillingRunItem.from_row(row) if row else None


def set_item_pdf_path(connection: sqlite3.Connection, item_id: int, pdf_path: str) -> None:
    """Record the filesystem path of a generated PDF for a line item."""
    connection.execute(
        "UPDATE billing_run_items SET pdf_path = ? WHERE id = ?",
        (pdf_path, item_id),
    )
    connection.commit()


def set_item_due_date(connection: sqlite3.Connection, item_id: int, due_date: str) -> None:
    """Persist the due date actually printed on a line item's PDF."""
    connection.execute(
        "UPDATE billing_run_items SET due_date = ? WHERE id = ?",
        (due_date, item_id),
    )
    connection.commit()


def set_item_dunning_level(
    connection: sqlite3.Connection,
    item_id: int,
    dunning_level: int,
    last_dunning_at: str,
    *,
    dunning_deadline_days: Optional[int] = None,
) -> None:
    """Record that a dunning notice was sent for a line item, advancing its stage."""
    if dunning_deadline_days is not None:
        connection.execute(
            """
            UPDATE billing_run_items
            SET dunning_level = ?, last_dunning_at = ?, dunning_deadline_days = ?
            WHERE id = ?
            """,
            (dunning_level, last_dunning_at, dunning_deadline_days, item_id),
        )
    else:
        connection.execute(
            "UPDATE billing_run_items SET dunning_level = ?, last_dunning_at = ? WHERE id = ?",
            (dunning_level, last_dunning_at, item_id),
        )
    connection.commit()


def set_item_email_sent_at(connection: sqlite3.Connection, item_id: int, sent_at: str) -> None:
    """Record when the invoice email for a line item was last sent."""
    connection.execute(
        "UPDATE billing_run_items SET email_sent_at = ? WHERE id = ?",
        (sent_at, item_id),
    )
    connection.commit()
