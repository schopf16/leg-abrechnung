"""Person: a natural person or company participating in the LEG.

Connected to metering points exclusively through the dated `Assignment` (see
`app.models.assignment`) -- never directly, and never via an address match.
The `billing_*` fields are a pure contact/billing address and
deliberately independent of any site's physical connection address (a
person can be billed somewhere other than where their meter is installed).

A Person can be a company (`company` set), a natural person (`first_name`/
`last_name` set, `company` empty), or a company with a named contact person
(all three set) -- see `Person.display_name` and `Person.address_block_lines`
for how these combine for display.

A couple is **one** Person carrying two names (`second_first_name`/
`second_last_name`), not two records. That follows straight from the vZEV
model (see CLAUDE.md): one customer, one netted amount, one reference
number, one document -- two records would mean two invoices for one
household and a distribution key to argue about. Both partners are contract
parties, so both are addressed (`named_persons`, and
`app.domain.salutation.letter_salutation`) and both email addresses receive
the same message (`contact_emails`). Exactly two, deliberately: a third
name would need a sub-table rather than a third set of columns.
"""

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
    """One named human on a Person record -- the first or the second.

    Exists so callers that address people (the PDF's salutation, the email
    placeholders) can loop over `Person.named_persons` instead of knowing
    which set of columns holds which partner.

    Attributes:
        salutation: One of `SALUTATION_OPTIONS`, or `""` if unknown. An
            empty salutation is a valid state, not a defect -- see
            `app.domain.salutation`.
        first_name: First name, possibly `""`.
        last_name: Last name, possibly `""`.
    """

    salutation: str
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        """`"Vorname Nachname"`, with either part omitted if empty.

        Returns:
            The full name, or `""` if both parts are empty.
        """
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def addressed_name(self) -> str:
        """`"Frau Anna Muster"` -- salutation and name on one line.

        Swiss letter practice puts the salutation on the name line, not on
        a line of its own; with two people in one address block a separate
        salutation line could not be matched to its name at all.

        Returns:
            Salutation and full name joined, either part omitted if empty.
        """
        return " ".join(p for p in (self.salutation, self.full_name) if p)


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
        deactivated_at: The day `active` was last set to `False`, or `None`
            while active. `None` also for everyone deactivated before
            migration 50 introduced the column: that date was never
            recorded, and inventing one would print as though it were a
            fact.
        note: Free-text internal remark. Deliberately never printed on a
            document, put into an email or written to a CSV export -- a
            note like "zahlt immer zu spät" is for the administrator.
        second_salutation: Salutation of the second named person, or `""`.
        second_first_name: First name of the second named person, or `""`
            if this record names only one person.
        second_last_name: Last name of the second named person, or `""`.
        second_contact_email: Email address of the second named person, or
            `""`. Both addresses receive every message (see
            `contact_emails`).
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
    def second_full_name(self) -> str:
        """`"Vorname Nachname"` of the second named person.

        Returns:
            The second person's full name, or `""` if this record names
            only one person.
        """
        return " ".join(p for p in (self.second_first_name, self.second_last_name) if p)

    @property
    def has_second_person(self) -> bool:
        """Whether a second person is named on this record.

        A second salutation or email address alone does not make one:
        without a name there is nobody to address.

        Returns:
            `True` if `second_full_name` is non-empty.
        """
        return bool(self.second_full_name)

    @property
    def named_persons(self) -> list[NamedPerson]:
        """The humans named on this record, first one first.

        Returns:
            One `NamedPerson` per named human -- empty for a company with
            no named contact, one normally, two for a couple.
        """
        people = []
        if self.full_name:
            people.append(NamedPerson(self.salutation, self.first_name, self.last_name))
        if self.has_second_person:
            people.append(NamedPerson(self.second_salutation, self.second_first_name, self.second_last_name))
        return people

    @property
    def contact_emails(self) -> list[str]:
        """Every email address this Person can be reached at, first one first.

        Both partners of a couple are contract parties, so both receive
        every message. `app.emailing.graph_client.send_email` puts them in
        one message's recipient field: one message per contract party,
        which keeps the privacy rule (nobody sees a stranger's address)
        while making a half-sent broadcast impossible.

        Returns:
            The non-empty, whitespace-stripped addresses without
            duplicates -- possibly an empty list.
        """
        addresses: list[str] = []
        for value in (self.contact_email, self.second_contact_email):
            cleaned = value.strip()
            if cleaned and cleaned not in addresses:
                addresses.append(cleaned)
        return addresses

    @property
    def display_name(self) -> str:
        """Single-line display name, for lists, search, dropdowns and exports.

        A couple appears as `"Anna Muster und Beat Beispiel"`, and that
        flows everywhere a person is named -- lists, document filenames,
        CSV, logs. That is intended: the customer is the couple.

        Returns:
            `"Firma (Vorname Nachname)"` if both are set, just the company
            name or just the personal name(s) if only one is, or `""` if
            neither `company` nor a personal name is set.
        """
        names = " und ".join(p for p in (self.full_name, self.second_full_name) if p)
        if self.company and names:
            return f"{self.company} ({names})"
        return self.company or names

    @property
    def address_block_lines(self) -> list[str]:
        """Recipient address block lines (company, then one line per person).

        Standard Swiss business-letter order: company name first, then each
        named person on their own line with the salutation in front of the
        name -- "Frau Anna Muster", not "Frau" above "Anna Muster". The
        salutation used to occupy a line of its own; with two people that
        is unreadable, because nothing says which salutation belongs to
        which name. Street/city are appended by the caller (see
        `app.pdf.layout.draw_recipient_block`).

        Returns:
            Non-empty lines to print, in order.
        """
        lines = []
        if self.company:
            lines.append(self.company)
        lines.extend(person.addressed_name for person in self.named_persons)
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
    row = connection.execute("SELECT * FROM person WHERE id = ?", (person_id,)).fetchone()
    return Person.from_row(row) if row else None


def get_by_customer_number(connection: sqlite3.Connection, customer_number: int) -> Optional[Person]:
    """Fetch a single Person by their customer number.

    Args:
        connection: Open SQLite connection.
        customer_number: Customer number to look up.

    Returns:
        The matching `Person`, or `None` if no such customer number exists.
    """
    row = connection.execute("SELECT * FROM person WHERE customer_number = ?", (customer_number,)).fetchone()
    return Person.from_row(row) if row else None


def get_by_email(connection: sqlite3.Connection, email: str) -> Optional[Person]:
    """Fetch a single Person by their contact email address.

    `contact_email` has no uniqueness constraint (unlike `customer_number`),
    so this returns the first match if several persons happen to share
    an address -- used only for an informational "does this already
    exist?" lookup (see `app.gui.pages.web_registrations`), never as an
    identity key.

    Args:
        connection: Open SQLite connection.
        email: Email address to look up.

    Returns:
        A matching `Person`, or `None` if no Person has this email.
    """
    row = connection.execute("SELECT * FROM person WHERE contact_email = ? LIMIT 1", (email,)).fetchone()
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
    """Activate or deactivate a Person, stamping `deactivated_at` along with it.

    Deactivating records today's date; reactivating clears it again, so the
    field never describes someone who is active. It is the date behind
    "Inaktiv seit ...", not a history -- a person deactivated, reactivated
    and deactivated again keeps only the latest date.

    Args:
        connection: Open SQLite connection.
        person_id: Primary key of the person.
        active: New active state.

    Returns:
        None.
    """
    connection.execute(
        "UPDATE person SET active = ?, deactivated_at = ? WHERE id = ?",
        (active, None if active else date.today().isoformat(), person_id),
    )
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


def confirm_billing_address(connection: sqlite3.Connection, person_id: int, value: str) -> None:
    """Record that the billing street/house number/postal code is intended.

    A PO box, a "c/o" line or a foreign address is perfectly legitimate and
    the register cannot know it, so one dismissal has to hold. Separate from
    `update` for the same reason as `app.models.site.confirm_address`.

    Args:
        connection: Open SQLite connection.
        person_id: The person.
        value: The exact text being confirmed. Empty clears it.

    Returns:
        None.
    """
    connection.execute("UPDATE person SET billing_address_confirmed = ? WHERE id = ?", (value, person_id))
    connection.commit()


def confirm_billing_city(connection: sqlite3.Connection, person_id: int, value: str) -> None:
    """Record that the billing locality is intended.

    Args:
        connection: Open SQLite connection.
        person_id: The person.
        value: The confirmed locality.

    Returns:
        None.
    """
    connection.execute("UPDATE person SET billing_city_confirmed = ? WHERE id = ?", (value, person_id))
    connection.commit()
