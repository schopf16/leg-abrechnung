"""Matching a Web-Registrierung against records the app already holds."""

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
    """Normalise an email address for comparison."""
    return (email or "").strip().lower()


def site_key(street: str, house_number: str, postal_code: str) -> tuple[str, str, str]:
    """Normalise an address into the key both sides of the match use."""
    return (street.strip().lower(), house_number.strip().lower(), postal_code.strip().lower())


def designation_key(designation: Optional[str]) -> str:
    """Normalise a Messpunktbezeichnung for comparison."""
    return (designation or "").strip().upper()


@dataclass
class RegistrationMatch:
    """The existing records one registration matches, if any."""

    person: Optional[Person]
    site: Optional[Site]
    metering_points: dict[int, Optional[MeteringPoint]]


@dataclass(frozen=True)
class TakeOverChoice:
    """Which actions one take-over item offers right now."""

    done: bool
    existing: Optional[str]
    offer_create: bool
    offer_link: bool
    offer_hand_mark: bool


def decide_take_over(*, done: bool, existing: Optional[str], match_is_identity: bool) -> TakeOverChoice:
    """Work out which actions a take-over item offers."""
    if done:
        return TakeOverChoice(True, existing, False, False, False)
    if existing is not None:
        return TakeOverChoice(False, existing, not match_is_identity, True, False)
    return TakeOverChoice(False, None, True, False, True)


def load_matches(connection, registrations: list[WebRegistration]) -> dict[int, RegistrationMatch]:
    """Match every registration against what the app already holds."""
    persons_by_email: dict[str, Person] = {}
    for person in person_repo.list_all(connection):
        # Every address the person holds, not just the first: a couple has
        # two (see `app.models.person`), and a registration arriving from
        # the partner's address has to find the record that already exists
        # rather than open a second one for the same household.
        for address in person.contact_emails:
            key = email_key(address)
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
