"""Person: a natural person or company participating in the LEG.

Connected to metering points exclusively through the dated `Assignment` (see
`app.models.assignment`) -- never directly, and never via an address match.
The `billing_*` fields are a pure contact/billing address and
deliberately independent of any site's physical connection address (a
person can be billed somewhere other than where their meter is installed).

A Person can be a company (`company` set), a natural person (`first_name`/
`last_name` set, `company` empty), or a company with a named contact person
(all three set) -- see `Person.display_name` and `Person.adressblock_zeilen`
for how these combine for display.
"""

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

#: Selectable values for `Person.salutation` (salutation of the natural person
#: -- the standalone individual, or the named contact at a company), used
#: on billing documents. Empty string means "no salutation known".
SALUTATION_OPTIONS = ["Herr", "Frau", "Familie"]

#: Digit range for `generate_customer_number` -- always exactly 6 digits.
_CUSTOMER_NUMBER_MIN = 100_000
_CUSTOMER_NUMBER_MAX = 999_999


@dataclass
class Person:
    """A participant in the local energy community.

    Attributes:
        id: Primary key, `None` for a not-yet-persisted instance.
        salutation: Salutation for the natural person (`first_name`/`last_name`)
            -- one of `SALUTATION_OPTIONS`, or `""` if unknown/not applicable
            (e.g. a company with no named contact person).
        company: Company name, or `""` if this Person is a natural person
            with no company.
        first_name: First name, or `""` if this Person is a company with no
            named contact person.
        last_name: Last name, or `""` if this Person is a company with no
            named contact person.
        contact_email: Contact email address.
        contact_phone: Optional contact phone number.
        billing_street: Billing address street name (without
            house number -- banks require the house number as its own
            field, see `billing_house_number`).
        billing_house_number: Billing address house number.
        billing_postal_code: Billing address postal code.
        billing_city: Billing address city.
        billing_country: Billing address ISO-3166 alpha-2 country code.
        iban: Bank IBAN used for credit note payouts.
        customer_number: A 6-digit customer number, auto-assigned at
            creation (see `generate_customer_number`) and never editable
            afterwards. Deliberately random rather than sequential so it
            cannot be used to infer customer count or registration order.
        bkw_customer_number: The customer number BKW itself assigns to this
            person, entered manually (unlike `customer_number`, which this
            app generates itself), or `None` if not known yet.
        paper_invoice: Whether this person receives a paper invoice by
            post (incurs the flat `LegSettings.paper_invoice_rappen` fee)
            rather than an electronic one.
        active: Whether this person is active. Set to `False` instead of
            deleting when billing history exists (see `delete`) -- an
            inactive person is kept for accounting/statistics but hidden
            from selection for new assignments.
        created_at: ISO-8601 creation timestamp.
    """

    id: Optional[int]
    salutation: str
    company: str
    first_name: str
    last_name: str
    contact_email: str
    contact_phone: str
    billing_street: str
    billing_house_number: str
    billing_postal_code: str
    billing_city: str
    billing_country: str
    iban: str
    customer_number: Optional[int]
    bkw_customer_number: Optional[int]
    paper_invoice: bool
    active: bool
    created_at: str

    @property
    def full_name(self) -> str:
        """`"Vorname Nachname"`, with either part omitted if empty.

        Returns:
            The natural person's full name, or `""` if both are empty.
        """
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def billing_street_with_number(self) -> str:
        """`"Strasse Hausnummer"`, with either part omitted if empty.

        Returns:
            The billing address's street line, or `""` if both are empty.
        """
        return " ".join(p for p in (self.billing_street, self.billing_house_number) if p)

    @property
    def display_name(self) -> str:
        """Single-line display name, for lists, search, dropdowns and exports.

        Returns:
            `"Firma (Vorname Nachname)"` if both are set, just the company
            name or just the personal name if only one is, or `""` if
            neither `company` nor a personal name is set.
        """
        if self.company and self.full_name:
            return f"{self.company} ({self.full_name})"
        return self.company or self.full_name

    @property
    def adressblock_zeilen(self) -> list[str]:
        """Recipient address block lines (company, salutation, personal name).

        Standard Swiss business-letter order: company name first, then the
        named contact's salutation and name (if any). Street/city are
        appended by the caller (see `app.pdf.layout.draw_recipient_block`).

        Returns:
            Non-empty lines to print, in order.
        """
        lines = []
        if self.company:
            lines.append(self.company)
        if self.full_name:
            if self.salutation:
                lines.append(self.salutation)
            lines.append(self.full_name)
        return lines

    @property
    def formatted_customer_number(self) -> str:
        """The customer number grouped for display, e.g. `"083 138"`.

        Returns:
            The 6-digit number as `"XXX XXX"`, or `""` if not yet
            assigned (should not normally happen for a persisted Person).
        """
        if self.customer_number is None:
            return ""
        digits = f"{self.customer_number:06d}"
        return f"{digits[:3]} {digits[3:]}"

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
            salutation=row["salutation"],
            company=row["company"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            contact_email=row["contact_email"],
            contact_phone=row["contact_phone"],
            billing_street=row["billing_street"],
            billing_house_number=row["billing_house_number"],
            billing_postal_code=row["billing_postal_code"],
            billing_city=row["billing_city"],
            billing_country=row["billing_country"],
            iban=row["iban"],
            customer_number=row["customer_number"],
            bkw_customer_number=row["bkw_customer_number"],
            paper_invoice=bool(row["paper_invoice"]),
            active=bool(row["active"]),
            created_at=row["created_at"],
        )


def list_all(connection: sqlite3.Connection) -> list[Person]:
    """List all persons, ordered by last name (or company if no last name), then first name.

    Args:
        connection: Open SQLite connection.

    Returns:
        All persons, alphabetically sorted.
    """
    rows = connection.execute(
        """
        SELECT * FROM person
        ORDER BY lower(CASE WHEN last_name <> '' THEN last_name ELSE company END), lower(first_name)
        """
    ).fetchall()
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


def get_by_customer_number(connection: sqlite3.Connection, customer_number: int) -> Optional[Person]:
    """Fetch a single Person by their customer number.

    Args:
        connection: Open SQLite connection.
        customer_number: Customer number to look up.

    Returns:
        The matching `Person`, or `None` if no such customer number exists.
    """
    row = connection.execute(
        "SELECT * FROM person WHERE customer_number = ?", (customer_number,)
    ).fetchone()
    return Person.from_row(row) if row else None


def get_by_email(connection: sqlite3.Connection, email: str) -> Optional[Person]:
    """Fetch a single Person by their contact email address.

    `contact_email` has no uniqueness constraint (unlike `customer_number`),
    so this returns the first match if several persons happen to share
    an address -- used only for an informational "does this already
    exist?" lookup (see `app.gui.pages.web_registrierungen`), never as an
    identity key.

    Args:
        connection: Open SQLite connection.
        email: Email address to look up.

    Returns:
        A matching `Person`, or `None` if no Person has this email.
    """
    row = connection.execute(
        "SELECT * FROM person WHERE contact_email = ? LIMIT 1", (email,)
    ).fetchone()
    return Person.from_row(row) if row else None


def generate_customer_number(connection: sqlite3.Connection) -> int:
    """Generate a random, unique 6-digit customer number.

    Deliberately random rather than sequential (retried on the unlikely
    event of a collision), so a customer number alone can never be used to
    infer how many customers exist or in what order they registered.

    Args:
        connection: Open SQLite connection.

    Returns:
        A new, unique 6-digit customer number, not yet persisted.
    """
    while True:
        candidate = random.randint(_CUSTOMER_NUMBER_MIN, _CUSTOMER_NUMBER_MAX)
        if get_by_customer_number(connection, candidate) is None:
            return candidate


def create(connection: sqlite3.Connection, person: Person) -> int:
    """Insert a new Person.

    Args:
        connection: Open SQLite connection.
        person: Data to insert; `id`, `created_at` and `customer_number` are
            ignored -- `customer_number` is always freshly auto-assigned (see
            `generate_customer_number`), regardless of what `person` carries.

    Returns:
        The primary key of the newly created person.
    """
    customer_number = generate_customer_number(connection)
    cursor = connection.execute(
        """
        INSERT INTO person
            (salutation, company, first_name, last_name, contact_email, contact_phone,
             billing_street, billing_house_number, billing_postal_code,
             billing_city, billing_country, iban, customer_number, bkw_customer_number,
             paper_invoice, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person.salutation,
            person.company,
            person.first_name,
            person.last_name,
            person.contact_email,
            person.contact_phone,
            person.billing_street,
            person.billing_house_number,
            person.billing_postal_code,
            person.billing_city,
            person.billing_country,
            person.iban,
            customer_number,
            person.bkw_customer_number,
            person.paper_invoice,
            True,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, person: Person) -> None:
    """Update an existing Person's data.

    The customer number is never changed by an update -- it is fixed for the
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
            salutation = ?, company = ?, first_name = ?, last_name = ?, contact_email = ?,
            contact_phone = ?, billing_street = ?, billing_house_number = ?,
            billing_postal_code = ?, billing_city = ?, billing_country = ?, iban = ?,
            bkw_customer_number = ?, paper_invoice = ?
        WHERE id = ?
        """,
        (
            person.salutation,
            person.company,
            person.first_name,
            person.last_name,
            person.contact_email,
            person.contact_phone,
            person.billing_street,
            person.billing_house_number,
            person.billing_postal_code,
            person.billing_city,
            person.billing_country,
            person.iban,
            person.bkw_customer_number,
            person.paper_invoice,
            person.id,
        ),
    )
    connection.commit()


def set_active(connection: sqlite3.Connection, person_id: int, active: bool) -> None:
    """Activate or deactivate a Person, without touching any other field.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.
        active: New active state.

    Returns:
        None.
    """
    connection.execute("UPDATE person SET active = ? WHERE id = ?", (active, person_id))
    connection.commit()


def delete(connection: sqlite3.Connection, person_id: int) -> bool:
    """Delete a Person, or deactivate them if billing history blocks deletion.

    `billing_run_items.person_id` is `ON DELETE RESTRICT` deliberately --
    once a person has been billed, that record is part of the accounting
    trail and must never silently lose its person reference. Rather than
    surface that as a dead end, a person who cannot be deleted is instead
    deactivated (`active = 0`): their customer number and history stay intact
    for accounting/statistics, but they no longer appear as a selectable
    option for new assignments. A genuinely new person (even one with the
    "same" name) always gets a fresh, independent customer number -- see
    `create`.

    metering points remain untouched when a person is actually deleted; any of
    their assignments are removed via `ON DELETE CASCADE`.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person to delete.

    Returns:
        `True` if the person was permanently deleted, `False` if they
        were deactivated instead because billing history exists.
    """
    try:
        connection.execute("DELETE FROM person WHERE id = ?", (person_id,))
        connection.commit()
        return True
    except sqlite3.IntegrityError:
        connection.rollback()
        set_active(connection, person_id, False)
        return False
