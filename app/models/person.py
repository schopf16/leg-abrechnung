"""Person: a natural person or company participating in the LEG.

Connected to Messpunkte exclusively through the dated `Zuordnung` (see
`app.models.zuordnung`) -- never directly, and never via an address match.
The `rechnungsadresse_*` fields are a pure contact/billing address and
deliberately independent of any Standort's physical connection address (a
person can be billed somewhere other than where their meter is installed).
"""

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

#: Selectable values for `Person.anrede` (salutation), used on billing
#: documents. Empty string means "no salutation known".
ANREDE_OPTIONS = ["Herr", "Frau", "Firma"]

#: Digit range for `generate_kundennummer` -- always exactly 8 digits.
_KUNDENNUMMER_MIN = 10_000_000
_KUNDENNUMMER_MAX = 99_999_999


@dataclass
class Person:
    """A participant in the local energy community.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        anrede: Salutation for billing documents -- one of
            `ANREDE_OPTIONS`, or `""` if unknown.
        name: Full name or company name.
        kontakt_email: Contact email address.
        kontakt_telefon: Optional contact phone number.
        rechnungsadresse_strasse: Billing address street and house number.
        rechnungsadresse_plz: Billing address postal code.
        rechnungsadresse_ort: Billing address city.
        rechnungsadresse_land: Billing address ISO-3166 alpha-2 country code.
        iban: Bank IBAN used for credit note payouts.
        kundennummer: An 8-digit customer number, auto-assigned at
            creation (see `generate_kundennummer`) and never editable
            afterwards. Deliberately random rather than sequential so it
            cannot be used to infer customer count or registration order.
        papierrechnung: Whether this person receives a paper invoice by
            post (incurs the flat `LegSettings.papierrechnung_rappen` fee)
            rather than an electronic one.
        created_at: ISO-8601 creation timestamp.
    """

    id: Optional[int]
    anrede: str
    name: str
    kontakt_email: str
    kontakt_telefon: str
    rechnungsadresse_strasse: str
    rechnungsadresse_plz: str
    rechnungsadresse_ort: str
    rechnungsadresse_land: str
    iban: str
    kundennummer: Optional[int]
    papierrechnung: bool
    created_at: str

    @property
    def kundennummer_formatiert(self) -> str:
        """The Kundennummer grouped for display, e.g. `"80 083 138"`.

        Returns:
            The 8-digit number as `"XX XXX XXX"`, or `""` if not yet
            assigned (should not normally happen for a persisted Person).
        """
        if self.kundennummer is None:
            return ""
        digits = f"{self.kundennummer:08d}"
        return f"{digits[:2]} {digits[2:5]} {digits[5:]}"

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Person":
        """Build a `Person` from a `sqlite3.Row`.

        Args:
            row: Row selected from the `person` table.

        Returns:
            The corresponding `Person` dataclass instance.
        """
        return Person(
            id=row["id"],
            anrede=row["anrede"],
            name=row["name"],
            kontakt_email=row["kontakt_email"],
            kontakt_telefon=row["kontakt_telefon"],
            rechnungsadresse_strasse=row["rechnungsadresse_strasse"],
            rechnungsadresse_plz=row["rechnungsadresse_plz"],
            rechnungsadresse_ort=row["rechnungsadresse_ort"],
            rechnungsadresse_land=row["rechnungsadresse_land"],
            iban=row["iban"],
            kundennummer=row["kundennummer"],
            papierrechnung=bool(row["papierrechnung"]),
            created_at=row["created_at"],
        )


def list_all(connection: sqlite3.Connection) -> list[Person]:
    """List all Personen, ordered by name.

    Args:
        connection: Open SQLite connection.

    Returns:
        All persons, alphabetically sorted by name.
    """
    rows = connection.execute("SELECT * FROM person ORDER BY name").fetchall()
    return [Person.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, person_id: int) -> Optional[Person]:
    """Fetch a single Person by id.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.

    Returns:
        The matching `Person`, or `None` if no such id exists.
    """
    row = connection.execute(
        "SELECT * FROM person WHERE id = ?", (person_id,)
    ).fetchone()
    return Person.from_row(row) if row else None


def get_by_kundennummer(connection: sqlite3.Connection, kundennummer: int) -> Optional[Person]:
    """Fetch a single Person by their Kundennummer.

    Args:
        connection: Open SQLite connection.
        kundennummer: Customer number to look up.

    Returns:
        The matching `Person`, or `None` if no such Kundennummer exists.
    """
    row = connection.execute(
        "SELECT * FROM person WHERE kundennummer = ?", (kundennummer,)
    ).fetchone()
    return Person.from_row(row) if row else None


def generate_kundennummer(connection: sqlite3.Connection) -> int:
    """Generate a random, unique 8-digit Kundennummer.

    Deliberately random rather than sequential (retried on the
    astronomically unlikely event of a collision), so a Kundennummer alone
    can never be used to infer how many customers exist or in what order
    they registered.

    Args:
        connection: Open SQLite connection.

    Returns:
        A new, unique 8-digit customer number, not yet persisted.
    """
    while True:
        candidate = random.randint(_KUNDENNUMMER_MIN, _KUNDENNUMMER_MAX)
        if get_by_kundennummer(connection, candidate) is None:
            return candidate


def create(connection: sqlite3.Connection, person: Person) -> int:
    """Insert a new Person.

    Args:
        connection: Open SQLite connection.
        person: Data to insert; `id`, `created_at` and `kundennummer` are
            ignored -- `kundennummer` is always freshly auto-assigned (see
            `generate_kundennummer`), regardless of what `person` carries.

    Returns:
        The primary key of the newly created person.
    """
    kundennummer = generate_kundennummer(connection)
    cursor = connection.execute(
        """
        INSERT INTO person
            (anrede, name, kontakt_email, kontakt_telefon, rechnungsadresse_strasse,
             rechnungsadresse_plz, rechnungsadresse_ort, rechnungsadresse_land,
             iban, kundennummer, papierrechnung, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person.anrede,
            person.name,
            person.kontakt_email,
            person.kontakt_telefon,
            person.rechnungsadresse_strasse,
            person.rechnungsadresse_plz,
            person.rechnungsadresse_ort,
            person.rechnungsadresse_land,
            person.iban,
            kundennummer,
            person.papierrechnung,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, person: Person) -> None:
    """Update an existing Person's data.

    The Kundennummer is never changed by an update -- it is fixed for the
    lifetime of the Person once assigned at creation.

    Args:
        connection: Open SQLite connection.
        person: Person with `id` set to an existing record.

    Returns:
        None.

    Raises:
        ValueError: If `person.id` is `None`.
    """
    if person.id is None:
        raise ValueError("Cannot update a Person without an id.")
    connection.execute(
        """
        UPDATE person SET
            anrede = ?, name = ?, kontakt_email = ?, kontakt_telefon = ?,
            rechnungsadresse_strasse = ?, rechnungsadresse_plz = ?,
            rechnungsadresse_ort = ?, rechnungsadresse_land = ?, iban = ?,
            papierrechnung = ?
        WHERE id = ?
        """,
        (
            person.anrede,
            person.name,
            person.kontakt_email,
            person.kontakt_telefon,
            person.rechnungsadresse_strasse,
            person.rechnungsadresse_plz,
            person.rechnungsadresse_ort,
            person.rechnungsadresse_land,
            person.iban,
            person.papierrechnung,
            person.id,
        ),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, person_id: int) -> None:
    """Delete a Person.

    Messpunkte remain untouched; any of the person's Zuordnungen are
    removed via `ON DELETE CASCADE`.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person to delete.

    Returns:
        None.
    """
    connection.execute("DELETE FROM person WHERE id = ?", (person_id,))
    connection.commit()
