"""Person: a natural person or company participating in the LEG."""

import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

#: Selectable values for `Person.salutation` (salutation of the natural person
#: -- the standalone individual, or the named contact at a company), used
#: on billing documents. Empty string means "no salutation known".
SALUTATION_OPTIONS = ["Herr", "Frau", "Familie"]


@dataclass(frozen=True)
class NamedPerson:
    """One named human on a Person record -- the first or the second."""

    salutation: str
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        """`"Vorname Nachname"`, with either part omitted if empty."""
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def addressed_name(self) -> str:
        """`"Frau Anna Muster"` -- salutation and name on one line."""
        return " ".join(p for p in (self.salutation, self.full_name) if p)


#: Digit range for `generate_customer_number` -- always exactly 6 digits.
_CUSTOMER_NUMBER_MIN = 100_000
_CUSTOMER_NUMBER_MAX = 999_999


@dataclass
class Person:
    """A participant in the local energy community."""

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
    deactivated_at: Optional[date] = None
    note: str = ""
    second_salutation: str = ""
    second_first_name: str = ""
    second_last_name: str = ""
    second_contact_email: str = ""
    billing_address_confirmed: str = ""
    billing_city_confirmed: str = ""

    @property
    def full_name(self) -> str:
        """`"Vorname Nachname"`, with either part omitted if empty."""
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def billing_street_with_number(self) -> str:
        """`"Strasse Hausnummer"`, with either part omitted if empty."""
        return " ".join(p for p in (self.billing_street, self.billing_house_number) if p)

    @property
    def second_full_name(self) -> str:
        """`"Vorname Nachname"` of the second named person."""
        return " ".join(p for p in (self.second_first_name, self.second_last_name) if p)

    @property
    def has_second_person(self) -> bool:
        """Whether a second person is named on this record."""
        return bool(self.second_full_name)

    @property
    def named_persons(self) -> list[NamedPerson]:
        """The humans named on this record, first one first."""
        people = []
        if self.full_name:
            people.append(NamedPerson(self.salutation, self.first_name, self.last_name))
        if self.has_second_person:
            people.append(NamedPerson(self.second_salutation, self.second_first_name, self.second_last_name))
        return people

    @property
    def contact_emails(self) -> list[str]:
        """Every email address this Person can be reached at, first one first."""
        addresses: list[str] = []
        for value in (self.contact_email, self.second_contact_email):
            cleaned = value.strip()
            if cleaned and cleaned not in addresses:
                addresses.append(cleaned)
        return addresses

    @property
    def display_name(self) -> str:
        """Single-line display name, for lists, search, dropdowns and exports."""
        names = " und ".join(p for p in (self.full_name, self.second_full_name) if p)
        if self.company and names:
            return f"{self.company} ({names})"
        return self.company or names

    @property
    def address_block_lines(self) -> list[str]:
        """Recipient address block lines (company, then one line per person)."""
        lines = []
        if self.company:
            lines.append(self.company)
        lines.extend(person.addressed_name for person in self.named_persons)
        return lines

    @property
    def formatted_customer_number(self) -> str:
        """The customer number grouped for display, e.g. `"083 138"`."""
        if self.customer_number is None:
            return ""
        digits = f"{self.customer_number:06d}"
        return f"{digits[:3]} {digits[3:]}"

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Person":
        """Build a `Person` from a `sqlite3.Row`."""
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
            billing_address_confirmed=row["billing_address_confirmed"],
            billing_city_confirmed=row["billing_city_confirmed"],
            iban=row["iban"],
            customer_number=row["customer_number"],
            bkw_customer_number=row["bkw_customer_number"],
            paper_invoice=bool(row["paper_invoice"]),
            active=bool(row["active"]),
            created_at=row["created_at"],
            deactivated_at=(date.fromisoformat(row["deactivated_at"]) if row["deactivated_at"] else None),
            note=row["note"],
            second_salutation=row["second_salutation"],
            second_first_name=row["second_first_name"],
            second_last_name=row["second_last_name"],
            second_contact_email=row["second_contact_email"],
        )


def list_all(connection: sqlite3.Connection) -> list[Person]:
    """List all persons, ordered by last name (or company if no last name), then first name."""
    rows = connection.execute(
        """
        SELECT * FROM person
        ORDER BY lower(CASE WHEN last_name <> '' THEN last_name ELSE company END), lower(first_name)
        """
    ).fetchall()
    return [Person.from_row(row) for row in rows]


def get(connection: sqlite3.Connection, person_id: int) -> Optional[Person]:
    """Fetch a single Person by id."""
    row = connection.execute("SELECT * FROM person WHERE id = ?", (person_id,)).fetchone()
    return Person.from_row(row) if row else None


def get_by_customer_number(connection: sqlite3.Connection, customer_number: int) -> Optional[Person]:
    """Fetch a single Person by their customer number."""
    row = connection.execute("SELECT * FROM person WHERE customer_number = ?", (customer_number,)).fetchone()
    return Person.from_row(row) if row else None


def get_by_email(connection: sqlite3.Connection, email: str) -> Optional[Person]:
    """Fetch a single Person by their contact email address."""
    row = connection.execute("SELECT * FROM person WHERE contact_email = ? LIMIT 1", (email,)).fetchone()
    return Person.from_row(row) if row else None


def generate_customer_number(connection: sqlite3.Connection) -> int:
    """Generate a random, unique 6-digit customer number."""
    while True:
        candidate = random.randint(_CUSTOMER_NUMBER_MIN, _CUSTOMER_NUMBER_MAX)
        if get_by_customer_number(connection, candidate) is None:
            return candidate


def create(connection: sqlite3.Connection, person: Person) -> int:
    """Insert a new Person."""
    customer_number = generate_customer_number(connection)
    cursor = connection.execute(
        """
        INSERT INTO person
            (salutation, company, first_name, last_name, contact_email, contact_phone,
             billing_street, billing_house_number, billing_postal_code,
             billing_city, billing_country, iban, customer_number, bkw_customer_number,
             paper_invoice, active, created_at, note,
             second_salutation, second_first_name, second_last_name, second_contact_email)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            person.note,
            person.second_salutation,
            person.second_first_name,
            person.second_last_name,
            person.second_contact_email,
        ),
    )
    connection.commit()
    return cursor.lastrowid


def update(connection: sqlite3.Connection, person: Person) -> None:
    """Update an existing Person's data."""
    if person.id is None:
        raise ValueError("Cannot update a Person without an id.")
    connection.execute(
        """
        UPDATE person SET
            salutation = ?, company = ?, first_name = ?, last_name = ?, contact_email = ?,
            contact_phone = ?, billing_street = ?, billing_house_number = ?,
            billing_postal_code = ?, billing_city = ?, billing_country = ?, iban = ?,
            bkw_customer_number = ?, paper_invoice = ?, note = ?,
            second_salutation = ?, second_first_name = ?, second_last_name = ?,
            second_contact_email = ?
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
            person.note,
            person.second_salutation,
            person.second_first_name,
            person.second_last_name,
            person.second_contact_email,
            person.id,
        ),
    )
    connection.commit()


def set_active(connection: sqlite3.Connection, person_id: int, active: bool) -> None:
    """Activate or deactivate a Person, stamping `deactivated_at` along with it."""
    connection.execute(
        "UPDATE person SET active = ?, deactivated_at = ? WHERE id = ?",
        (active, None if active else date.today().isoformat(), person_id),
    )
    connection.commit()


def delete(connection: sqlite3.Connection, person_id: int) -> bool:
    """Delete a Person, or deactivate them if billing history blocks deletion."""
    try:
        connection.execute("DELETE FROM person WHERE id = ?", (person_id,))
        connection.commit()
        return True
    except sqlite3.IntegrityError:
        connection.rollback()
        set_active(connection, person_id, False)
        return False


def confirm_billing_address(connection: sqlite3.Connection, person_id: int, value: str) -> None:
    """Record that the billing street/house number/postal code is intended."""
    connection.execute("UPDATE person SET billing_address_confirmed = ? WHERE id = ?", (value, person_id))
    connection.commit()


def confirm_billing_city(connection: sqlite3.Connection, person_id: int, value: str) -> None:
    """Record that the billing locality is intended."""
    connection.execute("UPDATE person SET billing_city_confirmed = ? WHERE id = ?", (value, person_id))
    connection.commit()
