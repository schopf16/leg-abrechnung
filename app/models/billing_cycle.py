"""Tracks a quarter's billing process through its real-world steps."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

#: The six steps of a billing quarter, in display order, as
#: `(BillingCycle attribute name, German label)` pairs.
STEPS: list[tuple[str, str]] = [
    ("readings_imported_at", "Messdaten eingelesen"),
    ("readings_checked_at", "Messdaten geprüft"),
    ("computed_at", "Abrechnung berechnet und Belege erzeugt"),
    ("emails_sent_at", "Rechnungen per E-Mail versendet"),
    ("paper_invoices_sent_at", "Papierrechnungen gedruckt und verschickt"),
    ("payouts_done_at", "Auszahlungen an Produzenten ausgelöst"),
]

#: Steps the app can confirm from its own data rather than asking. The
#: rest are real-world acts (an envelope posted, a payment ordered) that
#: only the administrator can attest to.
SELF_EVIDENT_STEPS = frozenset(
    {"readings_imported_at", "readings_checked_at", "computed_at", "emails_sent_at"}
)


@dataclass
class BillingCycle:
    """One quarter's progress through the six billing steps."""

    id: Optional[int]
    period_year: int
    period_quarter: int
    readings_imported_at: Optional[date]
    readings_checked_at: Optional[date]
    computed_at: Optional[date]
    emails_sent_at: Optional[date]
    paper_invoices_sent_at: Optional[date]
    payouts_done_at: Optional[date]
    override_reason: str
    override_at: Optional[str]
    created_at: str

    @property
    def label(self) -> str:
        """The quarter this cycle covers, e.g. "Q3 2025"."""
        return f"Q{self.period_quarter} {self.period_year}"

    @property
    def is_complete(self) -> bool:
        """Whether every step has a date, i.e. the quarter is settled."""
        return all(getattr(self, attr) is not None for attr, _ in STEPS)

    @property
    def was_overridden(self) -> bool:
        """Whether the control points were bypassed for this quarter."""
        return bool(self.override_reason.strip())

    @property
    def current_step(self) -> Optional[tuple[str, str]]:
        """The first step that has no date yet."""
        for attr, label in STEPS:
            if getattr(self, attr) is None:
                return attr, label
        return None

    @property
    def current_step_since(self) -> date:
        """The date the current step became active."""
        previous_date: Optional[date] = None
        for attr, _ in STEPS:
            value = getattr(self, attr)
            if value is None:
                break
            previous_date = value
        if previous_date is not None:
            return previous_date
        return datetime.fromisoformat(self.created_at).date()

    def days_open(self, reference: Optional[date] = None) -> Optional[int]:
        """How many days the current step has been open."""
        if self.is_complete:
            return None
        return ((reference or date.today()) - self.current_step_since).days

    def is_overdue(self, threshold_days: int, reference: Optional[date] = None) -> bool:
        """Whether the current step has been open for too long."""
        days = self.days_open(reference)
        return days is not None and days >= threshold_days

    @staticmethod
    def from_row(row: sqlite3.Row) -> "BillingCycle":
        """Build a `BillingCycle` from a `sqlite3.Row`."""

        def _date(value: Optional[str]) -> Optional[date]:
            return date.fromisoformat(value) if value else None

        return BillingCycle(
            id=row["id"],
            period_year=row["period_year"],
            period_quarter=row["period_quarter"],
            readings_imported_at=_date(row["readings_imported_at"]),
            readings_checked_at=_date(row["readings_checked_at"]),
            computed_at=_date(row["computed_at"]),
            emails_sent_at=_date(row["emails_sent_at"]),
            paper_invoices_sent_at=_date(row["paper_invoices_sent_at"]),
            payouts_done_at=_date(row["payouts_done_at"]),
            override_reason=row["override_reason"],
            override_at=row["override_at"],
            created_at=row["created_at"],
        )


def get(connection: sqlite3.Connection, cycle_id: int) -> Optional[BillingCycle]:
    """Fetch a single billing cycle by id."""
    row = connection.execute("SELECT * FROM billing_cycle WHERE id = ?", (cycle_id,)).fetchone()
    return BillingCycle.from_row(row) if row else None


def get_by_period(connection: sqlite3.Connection, year: int, quarter: int) -> Optional[BillingCycle]:
    """Fetch the cycle for one quarter, if one was started."""
    row = connection.execute(
        "SELECT * FROM billing_cycle WHERE period_year = ? AND period_quarter = ?",
        (year, quarter),
    ).fetchone()
    return BillingCycle.from_row(row) if row else None


def list_all(connection: sqlite3.Connection) -> list[BillingCycle]:
    """List every billing cycle, newest quarter first."""
    rows = connection.execute(
        "SELECT * FROM billing_cycle ORDER BY period_year DESC, period_quarter DESC"
    ).fetchall()
    return [BillingCycle.from_row(row) for row in rows]


def list_in_progress(connection: sqlite3.Connection) -> list[BillingCycle]:
    """List cycles that are not yet complete, newest quarter first."""
    return [c for c in list_all(connection) if not c.is_complete]


def start_for_period(connection: sqlite3.Connection, year: int, quarter: int) -> BillingCycle:
    """Start a billing cycle for a quarter, or return its existing one."""
    existing = get_by_period(connection, year, quarter)
    if existing is not None:
        return existing

    cursor = connection.execute(
        "INSERT INTO billing_cycle (period_year, period_quarter, created_at) VALUES (?, ?, ?)",
        (year, quarter, datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()
    return get(connection, cursor.lastrowid)


def update(connection: sqlite3.Connection, cycle: BillingCycle) -> None:
    """Update a cycle's step dates and override note."""
    if cycle.id is None:
        raise ValueError("Cannot update a BillingCycle without an id.")
    connection.execute(
        """
        UPDATE billing_cycle SET
            readings_imported_at = ?, readings_checked_at = ?, computed_at = ?,
            emails_sent_at = ?, paper_invoices_sent_at = ?, payouts_done_at = ?,
            override_reason = ?, override_at = ?
        WHERE id = ?
        """,
        (
            cycle.readings_imported_at.isoformat() if cycle.readings_imported_at else None,
            cycle.readings_checked_at.isoformat() if cycle.readings_checked_at else None,
            cycle.computed_at.isoformat() if cycle.computed_at else None,
            cycle.emails_sent_at.isoformat() if cycle.emails_sent_at else None,
            cycle.paper_invoices_sent_at.isoformat() if cycle.paper_invoices_sent_at else None,
            cycle.payouts_done_at.isoformat() if cycle.payouts_done_at else None,
            cycle.override_reason.strip(),
            cycle.override_at,
            cycle.id,
        ),
    )
    connection.commit()


def mark_step(
    connection: sqlite3.Connection, cycle: BillingCycle, attribute: str, when: Optional[date] = None
) -> BillingCycle:
    """Record that one step happened, without disturbing the others."""
    if attribute not in {attr for attr, _ in STEPS}:
        raise ValueError(f"Unknown billing cycle step: {attribute!r}")
    setattr(cycle, attribute, when or date.today())
    update(connection, cycle)
    return get(connection, cycle.id)


def record_override(connection: sqlite3.Connection, cycle: BillingCycle, reason: str) -> BillingCycle:
    """Record why the control points were bypassed for this quarter."""
    if not reason.strip():
        raise ValueError("Eine Umgehung der Kontrollpunkte braucht eine Begründung.")
    cycle.override_reason = reason.strip()
    cycle.override_at = datetime.now(timezone.utc).isoformat()
    update(connection, cycle)
    return get(connection, cycle.id)


def delete(connection: sqlite3.Connection, cycle_id: int) -> None:
    """Discard a billing cycle -- never touches its billing runs."""
    connection.execute("DELETE FROM billing_cycle WHERE id = ?", (cycle_id,))
    connection.commit()
