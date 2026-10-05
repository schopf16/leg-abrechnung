"""WebRegistration: an inbox row for one registration submitted through the public form on leg-
ittigen.ch (see `app.importers.registration_sync`)."""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class WebRegistrationMeter:
    """One reported Zählernummer within a `WebRegistration`."""

    id: Optional[int]
    web_registration_id: Optional[int]
    meter_number: str
    note: str
    metering_point_taken_over: bool = False

    @staticmethod
    def from_row(row: sqlite3.Row) -> "WebRegistrationMeter":
        """Build a `WebRegistrationMeter` from a `sqlite3.Row`."""
        return WebRegistrationMeter(
            id=row["id"],
            web_registration_id=row["web_registration_id"],
            meter_number=row["meter_number"],
            note=row["note"],
            metering_point_taken_over=bool(row["metering_point_taken_over"]),
        )


@dataclass
class WebRegistration:
    """One registration submitted through the leg-ittigen.ch public form."""

    id: Optional[int]
    cloudflare_id: int
    company: str
    salutation: str
    first_name: str
    last_name: str
    street: str
    house_number: str
    postal_code: str
    city: str
    email: str
    phone: str
    bkw_customer_number: str
    iban: str
    message: str
    submitted_at: str
    imported_at: str
    person_taken_over: bool = False
    site_taken_over: bool = False
    meters: list[WebRegistrationMeter] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Single-line display name, mirroring `Person.display_name`."""
        full_name = " ".join(p for p in (self.first_name, self.last_name) if p)
        if self.company and full_name:
            return f"{self.company} ({full_name})"
        return self.company or full_name

    @property
    def is_fully_processed(self) -> bool:
        """Whether there is nothing left to take over from this registration."""
        return (
            self.person_taken_over
            and self.site_taken_over
            and all(m.metering_point_taken_over for m in self.meters)
        )

    @staticmethod
    def from_row(row: sqlite3.Row, meters: list[WebRegistrationMeter]) -> "WebRegistration":
        """Build a `WebRegistration` from a `sqlite3.Row` and its meters."""
        return WebRegistration(
            id=row["id"],
            cloudflare_id=row["cloudflare_id"],
            company=row["company"],
            salutation=row["salutation"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            street=row["street"],
            house_number=row["house_number"],
            postal_code=row["postal_code"],
            city=row["city"],
            email=row["email"],
            phone=row["phone"],
            bkw_customer_number=row["bkw_customer_number"],
            iban=row["iban"],
            message=row["message"],
            submitted_at=row["submitted_at"],
            imported_at=row["imported_at"],
            person_taken_over=bool(row["person_taken_over"]),
            site_taken_over=bool(row["site_taken_over"]),
            meters=meters,
        )


def _load_meters(connection: sqlite3.Connection, web_registration_id: int) -> list[WebRegistrationMeter]:
    """Load all meters reported with one registration."""
    rows = connection.execute(
        "SELECT * FROM web_registration_meter WHERE web_registration_id = ? ORDER BY id",
        (web_registration_id,),
    ).fetchall()
    return [WebRegistrationMeter.from_row(row) for row in rows]


def list_all(connection: sqlite3.Connection) -> list[WebRegistration]:
    """List all registrations, most recently submitted first."""
    rows = connection.execute("SELECT * FROM web_registration ORDER BY submitted_at DESC").fetchall()
    return [WebRegistration.from_row(row, _load_meters(connection, row["id"])) for row in rows]


def get(connection: sqlite3.Connection, web_registration_id: int) -> Optional[WebRegistration]:
    """Fetch a single registration by id."""
    row = connection.execute("SELECT * FROM web_registration WHERE id = ?", (web_registration_id,)).fetchone()
    return WebRegistration.from_row(row, _load_meters(connection, row["id"])) if row else None


def get_by_email(connection: sqlite3.Connection, email: str) -> Optional[WebRegistration]:
    """Fetch a single registration by its email address."""
    row = connection.execute("SELECT * FROM web_registration WHERE email = ?", (email,)).fetchone()
    return WebRegistration.from_row(row, _load_meters(connection, row["id"])) if row else None


def upsert_from_submission(connection: sqlite3.Connection, registration: WebRegistration) -> int:
    """Insert a new registration, or update the existing one for the same email in place, replacing its..."""
    existing = get_by_email(connection, registration.email)
    previously_taken_over_by_meter = (
        {m.meter_number: m.metering_point_taken_over for m in existing.meters} if existing else {}
    )
    now = datetime.now(timezone.utc).isoformat()

    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO web_registration
                (cloudflare_id, company, salutation, first_name, last_name, street, house_number,
                 postal_code, city, email, phone, bkw_customer_number, iban, message,
                 submitted_at, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                registration.cloudflare_id,
                registration.company,
                registration.salutation,
                registration.first_name,
                registration.last_name,
                registration.street,
                registration.house_number,
                registration.postal_code,
                registration.city,
                registration.email,
                registration.phone,
                registration.bkw_customer_number,
                registration.iban,
                registration.message,
                registration.submitted_at,
                now,
            ),
        )
        web_registration_id = cursor.lastrowid
    else:
        connection.execute(
            """
            UPDATE web_registration SET
                cloudflare_id = ?, company = ?, salutation = ?, first_name = ?, last_name = ?,
                street = ?, house_number = ?, postal_code = ?, city = ?, email = ?, phone = ?,
                bkw_customer_number = ?, iban = ?, message = ?, submitted_at = ?,
                imported_at = ?
            WHERE id = ?
            """,
            (
                registration.cloudflare_id,
                registration.company,
                registration.salutation,
                registration.first_name,
                registration.last_name,
                registration.street,
                registration.house_number,
                registration.postal_code,
                registration.city,
                registration.email,
                registration.phone,
                registration.bkw_customer_number,
                registration.iban,
                registration.message,
                registration.submitted_at,
                now,
                existing.id,
            ),
        )
        web_registration_id = existing.id
        connection.execute(
            "DELETE FROM web_registration_meter WHERE web_registration_id = ?",
            (web_registration_id,),
        )

    for meter in registration.meters:
        connection.execute(
            "INSERT INTO web_registration_meter (web_registration_id, meter_number, note, metering_point_taken_over) "
            "VALUES (?, ?, ?, ?)",
            (
                web_registration_id,
                meter.meter_number,
                meter.note,
                previously_taken_over_by_meter.get(meter.meter_number, False),
            ),
        )

    connection.commit()
    return web_registration_id


def mark_person_taken_over(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Record that this registration's Person needs no further action."""
    connection.execute(
        "UPDATE web_registration SET person_taken_over = 1 WHERE id = ?",
        (web_registration_id,),
    )
    connection.commit()


def mark_site_taken_over(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Record that this registration's site needs no further action."""
    connection.execute(
        "UPDATE web_registration SET site_taken_over = 1 WHERE id = ?",
        (web_registration_id,),
    )
    connection.commit()


def mark_metering_point_taken_over(connection: sqlite3.Connection, web_registration_meter_id: int) -> None:
    """Record that one reported meter needs no further action."""
    connection.execute(
        "UPDATE web_registration_meter SET metering_point_taken_over = 1 WHERE id = ?",
        (web_registration_meter_id,),
    )
    connection.commit()


def unmark_person_taken_over(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Reopen this registration's Person item."""
    connection.execute(
        "UPDATE web_registration SET person_taken_over = 0 WHERE id = ?",
        (web_registration_id,),
    )
    connection.commit()


def unmark_site_taken_over(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Reopen this registration's site item."""
    connection.execute(
        "UPDATE web_registration SET site_taken_over = 0 WHERE id = ?",
        (web_registration_id,),
    )
    connection.commit()


def unmark_metering_point_taken_over(connection: sqlite3.Connection, web_registration_meter_id: int) -> None:
    """Reopen one reported meter."""
    connection.execute(
        "UPDATE web_registration_meter SET metering_point_taken_over = 0 WHERE id = ?",
        (web_registration_meter_id,),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Delete a registration and its reported meters (cascade)."""
    connection.execute("DELETE FROM web_registration WHERE id = ?", (web_registration_id,))
    connection.commit()
