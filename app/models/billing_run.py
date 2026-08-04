"""Persisted billing runs (Abrechnungsläufe) and their line items."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class BillingRun:
    """One quarterly billing run.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        period_year: Calendar year of the billing quarter.
        period_quarter: Quarter number, 1 to 4.
        created_at: ISO-8601 creation timestamp.
        price_rp_per_kwh: Internal price used for this run, snapshotted at
            creation time so later price changes never alter past runs.
        status: "erstellt" or "abgeschlossen".
        notes: Free-text notes.
    """

    id: Optional[int]
    period_year: int
    period_quarter: int
    created_at: str
    price_rp_per_kwh: float
    status: str
    notes: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "BillingRun":
        """Build a `BillingRun` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `billing_runs` table.

        Returns:
            The corresponding `BillingRun` dataclass instance.
        """
        return BillingRun(
            id=row["id"],
            period_year=row["period_year"],
            period_quarter=row["period_quarter"],
            created_at=row["created_at"],
            price_rp_per_kwh=row["price_rp_per_kwh"],
            status=row["status"],
            notes=row["notes"],
        )


@dataclass
class BillingRunItem:
    """One participant's combined billing document within a billing run.

    Every participant gets exactly one item, and one resulting PDF,
    regardless of whether they only consume, only produce, or both:
    consumption ("Bezug") and production ("Vergütung") are netted into a
    single amount. A positive `net_amount_rappen` means the participant
    owes the LEG (an invoice); negative means the LEG owes the participant
    (a credit, paid out via the payment list).

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        billing_run_id: Foreign key to the parent `BillingRun`.
        participant_id: Foreign key to the billed participant.
        consumed_kwh: Total locally-sourced consumption for the quarter
            (3 decimal precision).
        produced_kwh: Total locally-delivered production for the quarter
            (3 decimal precision).
        price_rp_per_kwh: Price applied, copied from the parent run.
        net_amount_rappen: `consumed_kwh * price - produced_kwh * price`,
            rounded to the nearest Rappen -- the *only* rounding step in
            the whole billing computation (see `app.domain.billing`).
        pdf_path: Filesystem path of the generated PDF, once created.
        created_at: ISO-8601 creation timestamp.
    """

    id: Optional[int]
    billing_run_id: int
    participant_id: int
    consumed_kwh: float
    produced_kwh: float
    price_rp_per_kwh: float
    net_amount_rappen: int
    pdf_path: Optional[str]
    created_at: str

    @property
    def net_amount_chf(self) -> float:
        """Net amount in Swiss francs, derived from `net_amount_rappen`.

        Returns:
            The amount as a float in CHF (Rappen / 100). Positive means
            owed to the LEG, negative means owed by the LEG.
        """
        return self.net_amount_rappen / 100

    @property
    def is_owed_to_leg(self) -> bool:
        """Whether the participant owes the LEG money (a real, payable invoice).

        Returns:
            `True` if `net_amount_rappen` is strictly positive.
        """
        return self.net_amount_rappen > 0

    @property
    def is_owed_by_leg(self) -> bool:
        """Whether the LEG owes the participant a payout.

        Returns:
            `True` if `net_amount_rappen` is strictly negative.
        """
        return self.net_amount_rappen < 0

    @staticmethod
    def from_row(row: sqlite3.Row) -> "BillingRunItem":
        """Build a `BillingRunItem` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `billing_run_items` table.

        Returns:
            The corresponding `BillingRunItem` dataclass instance.
        """
        return BillingRunItem(
            id=row["id"],
            billing_run_id=row["billing_run_id"],
            participant_id=row["participant_id"],
            consumed_kwh=row["consumed_kwh"],
            produced_kwh=row["produced_kwh"],
            price_rp_per_kwh=row["price_rp_per_kwh"],
            net_amount_rappen=row["net_amount_rappen"],
            pdf_path=row["pdf_path"],
            created_at=row["created_at"],
        )


def list_runs(connection: sqlite3.Connection) -> list[BillingRun]:
    """List all billing runs, most recent quarter first.

    Args:
        connection: Open SQLite connection.

    Returns:
        All billing runs ordered by year and quarter, descending.
    """
    rows = connection.execute(
        "SELECT * FROM billing_runs ORDER BY period_year DESC, period_quarter DESC"
    ).fetchall()
    return [BillingRun.from_row(row) for row in rows]


def get_run(connection: sqlite3.Connection, run_id: int) -> Optional[BillingRun]:
    """Fetch a single billing run by id.

    Args:
        connection: Open SQLite connection.
        run_id: Primary key of the billing run.

    Returns:
        The matching `BillingRun`, or `None` if no such id exists.
    """
    row = connection.execute(
        "SELECT * FROM billing_runs WHERE id = ?", (run_id,)
    ).fetchone()
    return BillingRun.from_row(row) if row else None


def get_run_by_period(
    connection: sqlite3.Connection, year: int, quarter: int
) -> Optional[BillingRun]:
    """Fetch a billing run by calendar year and quarter.

    Args:
        connection: Open SQLite connection.
        year: Calendar year.
        quarter: Quarter number, 1 to 4.

    Returns:
        The matching `BillingRun`, or `None` if none exists yet.
    """
    row = connection.execute(
        "SELECT * FROM billing_runs WHERE period_year = ? AND period_quarter = ?",
        (year, quarter),
    ).fetchone()
    return BillingRun.from_row(row) if row else None


def delete_run(connection: sqlite3.Connection, run_id: int) -> None:
    """Delete a billing run and all its line items (cascade).

    Used to regenerate a run from scratch, keeping billing idempotent per
    quarter.

    Args:
        connection: Open SQLite connection.
        run_id: Primary key of the billing run to delete.

    Returns:
        None.
    """
    connection.execute("DELETE FROM billing_runs WHERE id = ?", (run_id,))
    connection.commit()


def create_run(connection: sqlite3.Connection, run: BillingRun) -> int:
    """Insert a new billing run.

    Args:
        connection: Open SQLite connection.
        run: Billing run data to insert; `id` and `created_at` are ignored
            and generated by this function.

    Returns:
        The primary key of the newly created billing run.
    """
    cursor = connection.execute(
        """
        INSERT INTO billing_runs
            (period_year, period_quarter, created_at, price_rp_per_kwh, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run.period_year,
            run.period_quarter,
            datetime.now(timezone.utc).isoformat(),
            run.price_rp_per_kwh,
            run.status,
            run.notes,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def add_items(
    connection: sqlite3.Connection, items: list[BillingRunItem]
) -> list[int]:
    """Insert billing run line items.

    Args:
        connection: Open SQLite connection.
        items: Line items to insert; `id` and `created_at` are ignored and
            generated by this function.

    Returns:
        The primary keys of the newly created items, in input order.
    """
    ids = []
    for item in items:
        cursor = connection.execute(
            """
            INSERT INTO billing_run_items
                (billing_run_id, participant_id, consumed_kwh, produced_kwh,
                 price_rp_per_kwh, net_amount_rappen, pdf_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.billing_run_id,
                item.participant_id,
                item.consumed_kwh,
                item.produced_kwh,
                item.price_rp_per_kwh,
                item.net_amount_rappen,
                item.pdf_path,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        ids.append(cursor.lastrowid)
    connection.commit()
    return ids


def list_items(
    connection: sqlite3.Connection, billing_run_id: int
) -> list[BillingRunItem]:
    """List all line items of a billing run.

    Args:
        connection: Open SQLite connection.
        billing_run_id: Primary key of the parent billing run.

    Returns:
        All line items for the run, ordered by participant id.
    """
    rows = connection.execute(
        """
        SELECT * FROM billing_run_items
        WHERE billing_run_id = ?
        ORDER BY participant_id
        """,
        (billing_run_id,),
    ).fetchall()
    return [BillingRunItem.from_row(row) for row in rows]


def set_item_pdf_path(
    connection: sqlite3.Connection, item_id: int, pdf_path: str
) -> None:
    """Record the filesystem path of a generated PDF for a line item.

    Args:
        connection: Open SQLite connection.
        item_id: Primary key of the billing run item.
        pdf_path: Path of the generated PDF file.

    Returns:
        None.
    """
    connection.execute(
        "UPDATE billing_run_items SET pdf_path = ? WHERE id = ?",
        (pdf_path, item_id),
    )
    connection.commit()
