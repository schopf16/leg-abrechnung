"""WebRegistration: an inbox row for one registration submitted through
the public form on leg-ittigen.ch (see `app.importers.registration_sync`).

One row per Cloudflare submission (not per reported meter): the form
fields were deliberately chosen to mirror `Person` almost 1:1 (`firma`,
`anrede`, `vorname`, `nachname`, address, contact, `bkw_kundennummer`,
`iban`), but a registration can report zero, one or several meters
(`WebRegistrationMeter`) -- matching "which meter belongs to which
existing or new Messpunkt" is a judgment call for the administrator, not a
mechanical one. Taking a reviewed row into the real records therefore
stays a manual step in the existing `/personen`/`/messpunkte`/
`/zuordnungen` pages, using this row's data as a template.
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
            (not validated against `app.domain.messpunkt_validation` here
            -- the submitter may not know the full formal designation).
        note: Optional free-text purpose label from the submitter (e.g.
            "PV", "Wohnhaus", "Wärmepumpe") -- not a `Messpunkt` field,
            purely a hint for the administrator.
    """

    id: Optional[int]
    web_registration_id: Optional[int]
    meter_number: str
    note: str

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
        firma: Submitted company name, or `""`.
        anrede: Submitted salutation (`""`/`"Herr"`/`"Frau"`/`"Familie"`).
        vorname: Submitted first name.
        nachname: Submitted last name.
        strasse: Submitted street name (without house number).
        hausnummer: Submitted house number.
        plz: Submitted postal code.
        ort: Submitted city.
        email: Submitted email address -- the matching key across repeat
            submissions (see class docstring).
        telefon: Optional submitted phone number.
        bkw_kundennummer: Submitted BKW customer number, free text (unlike
            `Person.bkw_kundennummer`, which is a validated integer --
            the website does not validate this field).
        iban: Optional submitted IBAN, free text (not validated here).
        message: Optional free-text remark from the submitter.
        submitted_at: Submission timestamp as reported by the API.
        imported_at: ISO-8601 timestamp this row was last written here
            (insert or update).
        needs_review: Whether this entry is still awaiting review by the
            administrator. Only `mark_reviewed` clears it; any content
            change (including the reported meters) on a repeat submission
            sets it again, even if it had already been reviewed.
        reviewed_at: ISO-8601 timestamp of the last `mark_reviewed` call,
            or `None` if never reviewed.
        meters: Zählernummern reported with this registration, zero, one
            or several.
    """

    id: Optional[int]
    cloudflare_id: int
    firma: str
    anrede: str
    vorname: str
    nachname: str
    strasse: str
    hausnummer: str
    plz: str
    ort: str
    email: str
    telefon: str
    bkw_kundennummer: str
    iban: str
    message: str
    submitted_at: str
    imported_at: str
    needs_review: bool
    reviewed_at: Optional[str]
    meters: list[WebRegistrationMeter] = field(default_factory=list)

    @property
    def anzeige_name(self) -> str:
        """Single-line display name, mirroring `Person.anzeige_name`.

        Returns:
            `"Firma (Vorname Nachname)"` if both are set, just the
            company name or just the personal name if only one is, or
            `""` if neither is set.
        """
        voller_name = " ".join(p for p in (self.vorname, self.nachname) if p)
        if self.firma and voller_name:
            return f"{self.firma} ({voller_name})"
        return self.firma or voller_name

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
            firma=row["firma"],
            anrede=row["anrede"],
            vorname=row["vorname"],
            nachname=row["nachname"],
            strasse=row["strasse"],
            hausnummer=row["hausnummer"],
            plz=row["plz"],
            ort=row["ort"],
            email=row["email"],
            telefon=row["telefon"],
            bkw_kundennummer=row["bkw_kundennummer"],
            iban=row["iban"],
            message=row["message"],
            submitted_at=row["submitted_at"],
            imported_at=row["imported_at"],
            needs_review=bool(row["needs_review"]),
            reviewed_at=row["reviewed_at"],
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


def list_needs_review(connection: sqlite3.Connection) -> list[WebRegistration]:
    """List only registrations still awaiting review, most recent first.

    Args:
        connection: Open SQLite connection.

    Returns:
        Inbox entries with `needs_review = 1` (with their meters loaded),
        sorted by `submitted_at` descending.
    """
    rows = connection.execute(
        "SELECT * FROM web_registration WHERE needs_review = 1 ORDER BY submitted_at DESC"
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
    which meter row "is the same" across a content change.

    Callers decide `registration.needs_review`/`reviewed_at` before
    calling this (see `app.importers.registration_sync` for the actual
    new-vs-changed-vs-unchanged decision, including skipping this call
    entirely for a genuinely unchanged repeat submission) -- this function
    always writes exactly what it is given.

    Args:
        connection: Open SQLite connection.
        registration: Data to write, including its `meters`. Matched
            against any existing row via `registration.email`, regardless
            of `registration.id`.

    Returns:
        The primary key of the inserted or updated row.
    """
    existing = get_by_email(connection, registration.email)
    now = datetime.now(timezone.utc).isoformat()

    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO web_registration
                (cloudflare_id, firma, anrede, vorname, nachname, strasse, hausnummer,
                 plz, ort, email, telefon, bkw_kundennummer, iban, message,
                 submitted_at, imported_at, needs_review, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                registration.cloudflare_id,
                registration.firma,
                registration.anrede,
                registration.vorname,
                registration.nachname,
                registration.strasse,
                registration.hausnummer,
                registration.plz,
                registration.ort,
                registration.email,
                registration.telefon,
                registration.bkw_kundennummer,
                registration.iban,
                registration.message,
                registration.submitted_at,
                now,
                registration.needs_review,
                registration.reviewed_at,
            ),
        )
        web_registration_id = cursor.lastrowid
    else:
        connection.execute(
            """
            UPDATE web_registration SET
                cloudflare_id = ?, firma = ?, anrede = ?, vorname = ?, nachname = ?,
                strasse = ?, hausnummer = ?, plz = ?, ort = ?, email = ?, telefon = ?,
                bkw_kundennummer = ?, iban = ?, message = ?, submitted_at = ?,
                imported_at = ?, needs_review = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (
                registration.cloudflare_id,
                registration.firma,
                registration.anrede,
                registration.vorname,
                registration.nachname,
                registration.strasse,
                registration.hausnummer,
                registration.plz,
                registration.ort,
                registration.email,
                registration.telefon,
                registration.bkw_kundennummer,
                registration.iban,
                registration.message,
                registration.submitted_at,
                now,
                registration.needs_review,
                registration.reviewed_at,
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
            "INSERT INTO web_registration_meter (web_registration_id, meter_number, note) "
            "VALUES (?, ?, ?)",
            (web_registration_id, meter.meter_number, meter.note),
        )

    connection.commit()
    return web_registration_id


def mark_reviewed(connection: sqlite3.Connection, web_registration_id: int) -> None:
    """Mark a registration as reviewed, the only way `needs_review` is cleared.

    Idempotent: calling this again on an already-reviewed row just
    refreshes `reviewed_at`.

    Args:
        connection: Open SQLite connection.
        web_registration_id: Primary key of the inbox entry.

    Returns:
        None.
    """
    connection.execute(
        "UPDATE web_registration SET needs_review = 0, reviewed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), web_registration_id),
    )
    connection.commit()
