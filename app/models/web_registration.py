"""WebRegistration: an inbox row for one registration submitted through
the public form on leg-ittigen.ch (see `app.importers.registration_sync`).

One row per Cloudflare submission (not per reported meter): the form
fields were deliberately chosen to mirror `Person` almost 1:1 (`company`,
`salutation`, `first_name`, `last_name`, address, contact, `bkw_customer_number`,
`iban`), but a registration can report zero, one or several meters
(`WebRegistrationMeter`). Person, site and each meter's MeteringPoint are
each taken over as their own explicit step (see `app.gui.pages.
web_registrations`) -- matching a reported meter (and its site)
against a *new* record is a judgment call for the administrator, not a
mechanical one. Assignment (linking a taken-over Person to a taken-over
MeteringPoint) stays a manual step in `/assignments`.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class WebRegistrationMeter:
    """One reported Zählernummer within a `WebRegistration`.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        web_registration_id: Foreign key to the owning `WebRegistration`,
            `None` until persisted.
        meter_number: The reported Zählernummer, free text as submitted
            (not validated against `app.domain.metering_point_validation` here
            -- the submitter may not know the full formal designation).
        note: Optional free-text purpose label from the submitter (e.g.
            "PV", "Wohnhaus", "Wärmepumpe") -- not a `MeteringPoint` field,
            purely a hint for the administrator.
        metering_point_created: Whether a `MeteringPoint` was actually created for
            this reported meter via "MeteringPoint übernehmen" (see
            `app.gui.pages.web_registrations`). Only
            `mark_metering_point_created` sets it; `upsert_from_submission`
            carries it forward by `meter_number` across a repeat
            submission, since that call otherwise replaces all of a
            registration's meter rows wholesale.
    """

    id: Optional[int]
    web_registration_id: Optional[int]
    meter_number: str
    note: str
    metering_point_created: bool = False

    @staticmethod
    def from_row(row: sqlite3.Row) -> "WebRegistrationMeter":
        """Build a `WebRegistrationMeter` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `web_registration_meter` table.

        Returns:
            The corresponding `WebRegistrationMeter` dataclass instance.
        """
        return WebRegistrationMeter(
            id=row["id"],
            web_registration_id=row["web_registration_id"],
            meter_number=row["meter_number"],
            note=row["note"],
            metering_point_created=bool(row["metering_point_created"]),
        )


@dataclass
class WebRegistration:
    """One registration submitted through the leg-ittigen.ch public form.

    Matched across repeat submissions by `email` (the only identity field
    every registration is guaranteed to carry -- `meters` can be empty).
    See `app.importers.registration_sync` for the accepted limitation this
    implies if two different people share an email address.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        cloudflare_id: The id of the raw submission this row currently
            reflects, in the leg-ittigen.ch API -- unique, since at any
            time each row is a snapshot of exactly one submission.
        company: Submitted company name, or `""`.
        salutation: Submitted salutation (`""`/`"Herr"`/`"Frau"`/`"Familie"`).
        first_name: Submitted first name.
        last_name: Submitted last name.
        street: Submitted street name (without house number).
        house_number: Submitted house number.
        postal_code: Submitted postal code.
        city: Submitted city.
        email: Submitted email address -- the matching key across repeat
            submissions (see class docstring).
        phone: Optional submitted phone number.
        bkw_customer_number: Submitted BKW customer number, free text (unlike
            `Person.bkw_customer_number`, which is a validated integer --
            the website does not validate this field).
        iban: Optional submitted IBAN, free text (not validated here).
        message: Optional free-text remark from the submitter.
        submitted_at: Submission timestamp as reported by the API.
        imported_at: ISO-8601 timestamp this row was last written here
            (insert or update).
        person_created: Whether a `Person` was actually created from this
            registration via "Person übernehmen" (see `app.gui.pages.
            web_registrations`). Only `mark_person_created` sets it;
            used to decide whether deleting this registration (see
            `delete`) needs the strong irrevocable-data-loss warning.
        site_created: Whether a `site` was actually created from
            this registration's reported address via "site
            übernehmen". Only `mark_site_created` sets it.
        meters: Zählernummern reported with this registration, zero, one
            or several -- each with its own `metering_point_created` flag.
    """

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
    person_created: bool = False
    site_created: bool = False
    meters: list[WebRegistrationMeter] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Single-line display name, mirroring `Person.display_name`.

        Returns:
            `"Firma (Vorname Nachname)"` if both are set, just the
            company name or just the personal name if only one is, or
            `""` if neither is set.
        """
        full_name = " ".join(p for p in (self.first_name, self.last_name) if p)
        if self.company and full_name:
            return f"{self.company} ({full_name})"
        return self.company or full_name

    @property
    def is_fully_processed(self) -> bool:
        """Whether there is nothing left to take over from this registration.

        `True` once Person, site and every reported MeteringPoint have
        all been created via their respective "... übernehmen" action --
        the only remaining action at that point is deleting the entry.

        Returns:
            `True` if fully processed, `False` if anything is still open.
        """
        return (
            self.person_created
            and self.site_created
            and all(m.metering_point_created for m in self.meters)
        )

    @staticmethod
    def from_row(row: sqlite3.Row, meters: list[WebRegistrationMeter]) -> "WebRegistration":
        """Build a `WebRegistration` from a `sqlite3.Row` and its meters.

        Args:
            row: Row selected from the `web_registration` table.
            meters: This registration's `WebRegistrationMeter` rows,
                already loaded separately (see `_load_meters`).

        Returns:
            The corresponding `WebRegistration` dataclass instance.
        """
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
            person_created=bool(row["person_created"]),
            site_created=bool(row["site_created"]),
            meters=meters,
        )


def _load_meters(connection: sqlite3.Connection, web_registration_id: int) -> list[WebRegistrationMeter]:
    """Load all meters reported with one registration.

    Args:
        connection: Open SQLite connection.
        web_registration_id: Primary key of the owning registration.

    Returns:
        That registration's `WebRegistrationMeter` rows, in insertion order.
    """
    rows = connection.execute(
        "SELECT * FROM web_registration_meter WHERE web_registration_id = ? ORDER BY id",
        (web_registration_id,),
    ).fetchall()
    return [WebRegistrationMeter.from_row(row) for row in rows]


def list_all(connection: sqlite3.Connection) -> list[WebRegistration]:
    """List all registrations, most recently submitted first.

    Args:
        connection: Open SQLite connection.

    Returns:
        All inbox entries (with their meters loaded), sorted by
        `submitted_at` descending.
    """
    rows = connection.execute(
        "SELECT * FROM web_registration ORDER BY submitted_at DESC"
    ).fetchall()
    return [WebRegistration.from_row(row, _load_meters(connection, row["id"])) for row in rows]


def get(connection: sqlite3.Connection, web_registration_id: int) -> Optional[WebRegistration]:
    """Fetch a single registration by id.

    Args:
        connection: Open SQLite connection.
        web_registration_id: Primary key of the inbox entry.

    Returns:
        The matching `WebRegistration` (with meters loaded), or `None` if
        no such id exists.
    """
    row = connection.execute(
        "SELECT * FROM web_registration WHERE id = ?", (web_registration_id,)
    ).fetchone()
    return WebRegistration.from_row(row, _load_meters(connection, row["id"])) if row else None


def get_by_email(connection: sqlite3.Connection, email: str) -> Optional[WebRegistration]:
    """Fetch a single registration by its email address.

    The email is the matching key used across repeat submissions -- see
    the `WebRegistration` class docstring for the accepted limitation
    this implies.

    Args:
        connection: Open SQLite connection.
        email: Email address as submitted through the web form.

    Returns:
        The matching `WebRegistration` (with meters loaded), or `None` if
        unknown.
    """
    row = connection.execute(
        "SELECT * FROM web_registration WHERE email = ?", (email,)
    ).fetchone()
    return WebRegistration.from_row(row, _load_meters(connection, row["id"])) if row else None


def upsert_from_submission(connection: sqlite3.Connection, registration: WebRegistration) -> int:
    """Insert a new registration, or update the existing one for the same
    email in place, replacing its meters wholesale.

    Always replaces the full set of `web_registration_meter` rows (delete
    then reinsert) rather than diffing them individually -- the number of
    meters per registration is small, and this avoids having to decide
    which meter row "is the same" across a content change -- except for
    `metering_point_created`, which is explicitly carried forward by
    `meter_number` (see the loop below): unlike a brand new meter row,
    that flag records real administrator work that a same-content resync
    must not silently discard.

    Args:
        connection: Open SQLite connection.
        registration: Data to write, including its `meters`. Matched
            against any existing row via `registration.email`, regardless
            of `registration.id`.

    Returns:
        The primary key of the inserted or updated row.
    """
    existing = get_by_email(connection, registration.email)
    previously_created_by_meter = (
        {m.meter_number: m.metering_point_created for m in existing.meters} if existing else {}
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
            "INSERT INTO web_registration_meter (web_registration_id, meter_number, note, metering_point_created) "
            "VALUES (?, ?, ?, ?)",
            (
                web_registration_id,
                meter.meter_number,
                meter.note,
                previously_created_by_meter.get(meter.meter_number, False),
            ),
        )

    connection.commit()
    return web_registration_id


def mark_person_created(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Record that a `Person` was actually created from this registration.

    Idempotent. The only way `person_created` is set.

    Args:
        connection: Open SQLite connection.
        web_registration_id: Primary key of the inbox entry.

    Returns:
        None.
    """
    connection.execute(
        "UPDATE web_registration SET person_created = 1 WHERE id = ?",
        (web_registration_id,),
    )
    connection.commit()


def mark_site_created(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Record that a `site` was actually created from this registration.

    Idempotent. The only way `site_created` is set.

    Args:
        connection: Open SQLite connection.
        web_registration_id: Primary key of the inbox entry.

    Returns:
        None.
    """
    connection.execute(
        "UPDATE web_registration SET site_created = 1 WHERE id = ?",
        (web_registration_id,),
    )
    connection.commit()


def mark_metering_point_created(connection: sqlite3.Connection, web_registration_meter_id: int) -> None:
    """Record that a `MeteringPoint` was actually created for one reported meter.

    Idempotent. The only way a meter's `metering_point_created` is set.

    Args:
        connection: Open SQLite connection.
        web_registration_meter_id: Primary key of the `web_registration_meter` row.

    Returns:
        None.
    """
    connection.execute(
        "UPDATE web_registration_meter SET metering_point_created = 1 WHERE id = ?",
        (web_registration_meter_id,),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Delete a registration and its reported meters (cascade).

    Purely local -- callers that also want the corresponding submission
    removed from the remote leg-ittigen.ch Worker database must call
    `app.importers.cloudflare_client.delete_submissions` themselves (see
    `app.gui.pages.web_registrations.on_delete`, which does both).

    Args:
        connection: Open SQLite connection.
        web_registration_id: Primary key of the inbox entry to delete.

    Returns:
        None.
    """
    connection.execute("DELETE FROM web_registration WHERE id = ?", (web_registration_id,))
    connection.commit()
