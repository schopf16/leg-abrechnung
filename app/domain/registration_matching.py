"""Matching a Web-Registrierung against records the app already holds.

Lives here rather than in `app.gui.pages.web_registrations` because the
result decides what gets *written*: whether the page offers to create a
record, to link an existing one, or neither. That is a business rule, and
`app/gui/pages/` does not hold those (see CLAUDE.md).

Matching is exact, after normalising each field the way the app itself
stores it. Deliberately no fuzzy matching: linking a registration to the
wrong record is worse than not finding it at all, and the case fuzziness
would serve -- a typo in a submitted address -- is handled by the
administrator marking the item by hand instead.

The three keys are not equally strong, and the difference matters:

- A **site** address and a **Messpunktbezeichnung** identify their record.
  One address is one site (an apartment block is a single site with
  several metering points, see `app.models.site`), and `designation` is
  `UNIQUE` in the schema. A match here *is* the record, so offering to
  create a second one would only produce a duplicate -- or, for a
  Messpunkt, a constraint error.
- An **email address** does not. `app.models.person.get_by_email` says so
  outright: `contact_email` has no uniqueness constraint and is never used
  as an identity key. A couple sharing one household address genuinely
  resolves to the wrong person, so such a match may only ever be a
  suggestion, and creating the other person must stay possible.
"""

from dataclasses import dataclass
from typing import Optional

from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models.metering_point import MeteringPoint
from app.models.person import Person
from app.models.site import Site
from app.models.web_registration import WebRegistration


def email_key(email: Optional[str]) -> str:
    """Normalise an email address for comparison.

    Args:
        email: Raw address from either side, or `None`.

    Returns:
        Trimmed and lowercased. Case matters to nobody in practice, and
        letting it decide would silently create a second Person for a
        capitalised spelling -- `app.gui.person_form` has no
        duplicate-email check to catch that.
    """
    return (email or "").strip().lower()


def site_key(street: str, house_number: str, postal_code: str) -> tuple[str, str, str]:
    """Normalise an address into the key both sides of the match use.

    Args:
        street: Street name.
        house_number: House number.
        postal_code: Postal code.

    Returns:
        The three parts, trimmed and lowercased.
    """
    return (street.strip().lower(), house_number.strip().lower(), postal_code.strip().lower())


def designation_key(designation: Optional[str]) -> str:
    """Normalise a Messpunktbezeichnung for comparison.

    Args:
        designation: Raw value from either side, or `None`.

    Returns:
        Trimmed and uppercased -- the form the app stores, see
        `app.domain.metering_point_validation.
        assemble_metering_point_designation`. The submitter types free
        text, so a lowercase submission of an existing Messpunkt must
        still find it; without this the page would offer to create it and
        the create form would hit the `UNIQUE` constraint instead.
    """
    return (designation or "").strip().upper()


@dataclass
class RegistrationMatch:
    """The existing records one registration matches, if any.

    Holds the records themselves rather than booleans, so the
    confirmation dialog can name what it is about to link -- confirming
    that blind would defeat the purpose.

    Attributes:
        person: Existing Person with this registration's email, or `None`.
        site: Existing site at this registration's address, or `None`.
        metering_points: `{meter.id: MeteringPoint or None}` per reported meter.
    """

    person: Optional[Person]
    site: Optional[Site]
    metering_points: dict[int, Optional[MeteringPoint]]


@dataclass(frozen=True)
class TakeOverChoice:
    """Which actions one take-over item offers right now.

    Attributes:
        done: Nothing left to do; show the badge and a way to reopen.
        existing: Display text of the matched record, or `None`.
        offer_create: Show the prefilled create dialog action.
        offer_link: Show the "link the existing record" action.
        offer_hand_mark: Show the mark-by-hand escape hatch.
    """

    done: bool
    existing: Optional[str]
    offer_create: bool
    offer_link: bool
    offer_hand_mark: bool


def decide_take_over(*, done: bool, existing: Optional[str], match_is_identity: bool) -> TakeOverChoice:
    """Work out which actions a take-over item offers.

    Three states, and the middle one is the whole point of this module.
    Nothing left to do: just the badge. A match found: offer to link it --
    and suppress "create" only when the match *identifies* the record,
    because creating a second site at one address, or a second Messpunkt
    under one designation, is exactly the mistake being prevented. A match
    that merely suggests (an email) keeps "create" available, so a wrong
    suggestion never traps the administrator. Nothing found: create, plus
    the hand-marking escape hatch for the typo case.

    Args:
        done: Whether the item is already taken over.
        existing: Display text of the matched record, or `None`.
        match_is_identity: Whether a match proves it is the same record
            (address, Messpunktbezeichnung) rather than merely suggesting
            it (email) -- see the module docstring.

    Returns:
        The `TakeOverChoice` for this item.
    """
    if done:
        return TakeOverChoice(True, existing, False, False, False)
    if existing is not None:
        return TakeOverChoice(False, existing, not match_is_identity, True, False)
    return TakeOverChoice(False, None, True, False, True)


def load_matches(connection, registrations: list[WebRegistration]) -> dict[int, RegistrationMatch]:
    """Match every registration against what the app already holds.

    Reads the three tables once for the whole list rather than once per
    registration -- the inbox renders every card in one pass.

    Args:
        connection: Open SQLite connection.
        registrations: The registrations currently being shown.

    Returns:
        `{registration.id: RegistrationMatch}`.
    """
    persons_by_email: dict[str, Person] = {}
    for person in person_repo.list_all(connection):
        key = email_key(person.contact_email)
        # An empty address is not an identity, and several persons may
        # legally share one -- keep the first rather than letting
        # `list_all` order silently decide which one a dialog names.
        if key and key not in persons_by_email:
            persons_by_email[key] = person

    sites_by_address = {
        site_key(s.street, s.house_number, s.postal_code): s for s in site_repo.list_all(connection)
    }
    metering_points_by_designation = {
        designation_key(mp.designation): mp for mp in metering_point_repo.list_all(connection)
    }

    return {
        reg.id: RegistrationMatch(
            person=persons_by_email.get(email_key(reg.email)) if email_key(reg.email) else None,
            site=sites_by_address.get(site_key(reg.street, reg.house_number, reg.postal_code)),
            metering_points={
                m.id: metering_points_by_designation.get(designation_key(m.meter_number)) for m in reg.meters
            },
        )
        for reg in registrations
    }
