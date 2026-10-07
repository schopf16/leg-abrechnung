"""The values page 1 of the Beitrittserklärung asks for."""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from app.models import assignment as assignment_repo
from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN, MeteringPoint
from app.models.person import Person


def _number(value: Optional[float]) -> str:
    """Write an optional measurement without inventing a zero."""
    if value is None:
        return ""
    return f"{value:g}".replace(".", ",")


@dataclass
class ContractFields:
    """What goes on page 1, each already a finished line of text."""

    company: str = ""
    salutation: str = ""
    first_name: str = ""
    last_name: str = ""
    names: str = ""
    address: str = ""
    locality: str = ""
    postal_code: str = ""
    city: str = ""
    email: str = ""
    phone: str = ""
    substation_area: str = ""
    consumption_designations: list[str] = field(default_factory=list)
    feed_in_designations: list[str] = field(default_factory=list)
    iban: str = ""
    pv_capacity: str = ""
    battery_capacity: str = ""
    wallbox_capacity: str = ""

    @property
    def has_feed_in(self) -> bool:
        """Whether the optional feed-in block says anything at all."""
        return bool(
            self.feed_in_designations
            or self.iban
            or self.pv_capacity
            or self.battery_capacity
            or self.wallbox_capacity
        )


def _substation_area_names(connection: sqlite3.Connection, points: list[MeteringPoint]) -> list[str]:
    """The Trafokreis designations behind a participant's metering points."""
    sites = {site.id: site for site in site_repo.list_all(connection)}
    areas = {area.id: area for area in substation_area_repo.list_all(connection)}
    found: list[str] = []
    for point in points:
        site = sites.get(point.site_id)
        area = areas.get(site.substation_area_id) if site else None
        if area is None:
            continue
        # `bkw_designation` is BKW's own, "TRA9365", and it is filled for all
        # 34 areas in the live data. `name` is the administrator's label for
        # the same thing, "Trafokreis-TRA9365", so it would print the word
        # twice on a form whose line already reads "Trafokreis TRA ___".
        designation = (area.bkw_designation or area.name).strip()
        if designation[:3].upper() == "TRA":
            designation = designation[3:]
        if designation and designation not in found:
            found.append(designation)
    return found


def gather(connection: sqlite3.Connection, person: Person) -> ContractFields:
    """Collect everything page 1 asks about one participant."""
    points_by_id = {point.id: point for point in metering_point_repo.list_all(connection)}
    points = [
        point
        for assignment in assignment_repo.list_for_person(connection, person.id)
        if (point := points_by_id.get(assignment.metering_point_id)) is not None
    ]
    # Distinct and in a stable order: a participant can hold the same meter
    # across two consecutive assignments (a move within the same flat), and
    # the form must not name it twice.
    seen: set[int] = set()
    unique_points = [p for p in points if not (p.id in seen or seen.add(p.id))]

    consumption = [p for p in unique_points if p.direction == DIRECTION_CONSUMPTION]
    feed_in = [p for p in unique_points if p.direction == DIRECTION_FEED_IN]

    # The capacities belong to the feed-in side. Taken from the feed-in
    # meters only, and joined where a participant has several -- the one
    # participant in the live data who does would otherwise lose a figure.
    pv = [_number(p.pv_capacity_kwp) for p in feed_in if p.pv_capacity_kwp is not None]
    battery = [_number(p.battery_capacity_kwh) for p in feed_in if p.battery_capacity_kwh is not None]
    wallbox = [_number(p.wallbox_capacity_kw) for p in feed_in if p.wallbox_capacity_kw is not None]

    named = person.named_persons
    shared_last_name = len({one.last_name for one in named}) == 1
    if len(named) == 2 and shared_last_name:
        first_name, last_name = f"{named[0].first_name} und {named[1].first_name}", named[0].last_name
    elif len(named) == 2:
        # Different surnames: the first person whole in the Vorname field, so
        # the two fields read "Anna Muster und Beat" + "Beispiel".
        first_name, last_name = f"{named[0].full_name} und {named[1].first_name}", named[1].last_name
    elif named:
        first_name, last_name = named[0].first_name, named[0].last_name
    else:
        first_name, last_name = "", ""
    if len(named) == 2:
        # The form's choice offers "Familie" but nothing for two surnames.
        salutation = "Familie" if shared_last_name else ""
    else:
        salutation = person.salutation

    return ContractFields(
        company=person.company,
        salutation=salutation,
        first_name=first_name,
        last_name=last_name,
        # Both names of a couple: both are contract parties and both sign.
        # Without the salutations -- the form's line is "Vorname, Name".
        names=" und ".join(named.full_name for named in person.named_persons),
        address=person.billing_street_with_number,
        locality=f"{person.billing_postal_code} {person.billing_city}".strip(),
        postal_code=person.billing_postal_code,
        city=person.billing_city,
        email=", ".join(person.contact_emails),
        phone=person.contact_phone,
        substation_area=", ".join(_substation_area_names(connection, unique_points)),
        consumption_designations=[p.designation for p in consumption],
        feed_in_designations=[p.designation for p in feed_in],
        iban=person.iban,
        pv_capacity=", ".join(pv),
        battery_capacity=", ".join(battery),
        wallbox_capacity=", ".join(wallbox),
    )
