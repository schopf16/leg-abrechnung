"""The values page 1 of the Beitrittserklärung asks for.

The official form (`LEG-Ittigen-Beitrittserklaerung-und-Gesellschaftsvertrag`,
seven pages) asks for thirteen things on its first page, and this database
already held twelve of them -- the thirteenth, the Wallbox power, is on the
metering point since migration 52 for exactly this reason. Every one of
these was being copied onto paper by hand; 77 of 91 participants have signed
such a sheet.

Reading, not deciding: this gathers what is stored and leaves a blank where
nothing is stored. Three blanks are deliberate and permanent -- Ort, Datum
and the signature come from the person, with a pen.

Two shapes of the real data decide the awkward parts:

- **Several metering points per direction.** 86 of 87 participants hold one
  consumption and/or one feed-in meter, and exactly one holds two of each.
  The form has one line per direction, so several designations are listed in
  that one line rather than silently reduced to the first -- a form that
  drops a meter is worse than a form that is crowded.
- **A couple is one participant with two names** (see CLAUDE.md on the vZEV
  model), and the form has one "Vorname, Name" line. Both names go into it,
  because both are contract parties and both sign.

The Trafokreis comes through the site, not the metering point: the form asks
for one per participant, and in this data every meter of a participant sits
in the same Trafokreis. Where that is ever untrue the distinct names are
listed, for the same reason the designations are.
"""

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
    """Write an optional measurement without inventing a zero.

    Args:
        value: The figure, or `None` when nothing is recorded.

    Returns:
        The number German-style without trailing zeros, or `""`. Blank
        rather than "0", because "no battery" and "a battery of zero kWh"
        are different statements and only the second would be a claim.
    """
    if value is None:
        return ""
    return f"{value:g}".replace(".", ",")


@dataclass
class ContractFields:
    """What goes on page 1, each already a finished line of text.

    Attributes:
        company: Firma, or `""` for a private participant.
        names: "Vorname, Name" -- both names for a couple.
        address: Street and house number.
        locality: Postal code and place.
        email: Contact address, both for a couple.
        phone: Contact number.
        substation_area: Trafokreis designation, without the "TRA" the form
            already prints.
        consumption_designations: Messpunktnummern Bezug.
        feed_in_designations: Messpunktnummern Einspeisung.
        iban: For a feed-in refund.
        pv_capacity: Solaranlage in kWp.
        battery_capacity: Batteriespeicher in kWh.
        wallbox_capacity: Wallbox maximum power in kW.
    """

    company: str = ""
    names: str = ""
    address: str = ""
    locality: str = ""
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
        """Whether the optional feed-in block says anything at all.

        Returns:
            `True` if the participant feeds in or any of its figures is
            recorded.
        """
        return bool(
            self.feed_in_designations
            or self.iban
            or self.pv_capacity
            or self.battery_capacity
            or self.wallbox_capacity
        )


def _substation_area_names(connection: sqlite3.Connection, points: list[MeteringPoint]) -> list[str]:
    """The Trafokreis designations behind a participant's metering points.

    Args:
        connection: Open SQLite connection.
        points: The participant's metering points.

    Returns:
        The distinct designations, in the order the points were given.
    """
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
    """Collect everything page 1 asks about one participant.

    Args:
        connection: Open SQLite connection.
        person: The participant the form is for.

    Returns:
        The fields, each blank where nothing is stored.
    """
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

    return ContractFields(
        company=person.company,
        # Both names of a couple: both are contract parties and both sign.
        # Without the salutations -- the form's line is "Vorname, Name".
        names=" und ".join(named.full_name for named in person.named_persons),
        address=person.billing_street_with_number,
        locality=f"{person.billing_postal_code} {person.billing_city}".strip(),
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
