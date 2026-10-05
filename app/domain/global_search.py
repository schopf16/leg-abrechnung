"""One search box that reaches every Stammdaten record."""

import sqlite3
from dataclasses import dataclass
from typing import Optional

from app.models import assignment as assignment_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.sort_keys import fold_for_sort, person_name_key, text_key

#: Kinds, in the order of the data model. The labels are what the drawer
#: calls those lists, so a result group and the page it leads to are named
#: the same thing.
KIND_SUBSTATION_AREA = "substation_area"
KIND_SITE = "site"
KIND_METERING_POINT = "metering_point"
KIND_LEG = "leg"
KIND_PERSON = "person"

KIND_LABELS = {
    KIND_SUBSTATION_AREA: "Trafokreise",
    KIND_SITE: "Standorte",
    KIND_METERING_POINT: "Messpunkte",
    KIND_LEG: "LEGs",
    KIND_PERSON: "Personen",
}

#: The order groups are shown in.
KIND_ORDER = [
    KIND_SUBSTATION_AREA,
    KIND_SITE,
    KIND_METERING_POINT,
    KIND_LEG,
    KIND_PERSON,
]

#: Routes a hit leads to, by kind. A Trafokreis has no detail page, so it
#: leads to its list -- the one place its records can be opened.
_ROUTES = {
    KIND_SUBSTATION_AREA: "/substation-areas",
    KIND_SITE: "/sites/{id}",
    KIND_METERING_POINT: "/metering-points/{id}",
    KIND_LEG: "/legs/{id}",
    KIND_PERSON: "/persons/{id}",
}

#: How many hits one group shows. Beyond this the group says how many more
#: there are and the list is where they get worked through -- a dropdown with
#: ninety entries is a list, and a worse one than the real page.
PER_KIND_LIMIT = 6

#: Shortest query that is searched at all. One character matches most of the
#: database and says nothing.
MIN_QUERY_LENGTH = 2


@dataclass(frozen=True)
class SearchHit:
    """One found record."""

    kind: str
    record_id: int
    title: str
    detail: str
    route: str


@dataclass(frozen=True)
class SearchGroup:
    """The hits of one kind."""

    kind: str
    label: str
    hits: list[SearchHit]
    total: int


def _matches(needle: str, *parts: Optional[str]) -> bool:
    """Whether the folded needle occurs in any of the parts."""
    return any(needle in fold_for_sort(part or "") for part in parts)


def person_matches(person, query: str) -> bool:
    """Whether every word of the query occurs somewhere in a person's names.

    Word-wise, which is the whole point: "Michael Test" is two words and no
    single field holds both, so a plain substring search over the fields
    finds nothing -- while the administrator reasonably types the name the
    way they say it. Order does not matter either.

    An empty query matches everybody, so a caller needs no special case.
    Used by the Aufnahmen and Austritte worklists, which search a person's
    name and nothing else (their `hint` says so); the Personen list keeps
    its own, wider haystack.
    """
    parts = (
        person.company,
        person.first_name,
        person.last_name,
        person.second_first_name,
        person.second_last_name,
        person.formatted_customer_number,
    )
    return all(_matches(fold_for_sort(word), *parts) for word in (query or "").split())


def search(connection: sqlite3.Connection, query: str) -> list[SearchGroup]:
    """Find every Stammdaten record matching one query."""
    needle = fold_for_sort((query or "").strip())
    if len(needle) < MIN_QUERY_LENGTH:
        return []

    areas = {area.id: area for area in substation_area_repo.list_all(connection)}
    sites = {site.id: site for site in site_repo.list_all(connection)}
    legs = {leg.id: leg for leg in leg_repo.list_all(connection)}
    persons = {person.id: person for person in person_repo.list_all(connection)}
    metering_points = metering_point_repo.list_all(connection)

    found: dict[str, list[tuple]] = {kind: [] for kind in KIND_ORDER}

    for area in areas.values():
        if _matches(needle, area.name, area.bkw_designation, area.note):
            found[KIND_SUBSTATION_AREA].append(
                (text_key(area.name), _hit(KIND_SUBSTATION_AREA, area.id, area.name, ""))
            )

    for site in sites.values():
        area = areas.get(site.substation_area_id)
        if _matches(
            needle,
            site.street,
            site.house_number,
            site.postal_code,
            site.municipality,
            site.address_detail,
            area.name if area else "",
        ):
            found[KIND_SITE].append(
                (
                    text_key(site.street, site.house_number),
                    _hit(
                        KIND_SITE,
                        site.id,
                        site.full_address,
                        area.name if area else "",
                    ),
                )
            )

    # Who is on a metering point now: the same question the Messpunkte list
    # answers, and the reason a person's name finds their meter.
    persons_by_metering_point: dict[int, list[str]] = {}
    for assignment in assignment_repo.list_all(connection):
        person = persons.get(assignment.person_id)
        if person is not None:
            persons_by_metering_point.setdefault(assignment.metering_point_id, []).append(person.display_name)

    for metering_point in metering_points:
        site = sites.get(metering_point.site_id)
        leg = legs.get(metering_point.leg_id)
        names = persons_by_metering_point.get(metering_point.id, [])
        if _matches(
            needle,
            metering_point.designation,
            metering_point.label,
            site.full_address if site else "",
            leg.name if leg else "",
            *names,
        ):
            found[KIND_METERING_POINT].append(
                (
                    text_key(metering_point.designation),
                    _hit(
                        KIND_METERING_POINT,
                        metering_point.id,
                        metering_point.designation,
                        ", ".join(
                            part
                            for part in (
                                metering_point.label,
                                site.full_address if site else "",
                            )
                            if part
                        ),
                    ),
                )
            )

    for leg in legs.values():
        if _matches(needle, leg.name, leg.note):
            found[KIND_LEG].append((text_key(leg.name), _hit(KIND_LEG, leg.id, leg.name, "")))

    for person in persons.values():
        if _matches(
            needle,
            person.company,
            person.first_name,
            person.last_name,
            person.second_first_name,
            person.second_last_name,
            person.contact_email,
            person.second_contact_email,
            person.contact_phone,
            person.billing_street,
            person.billing_house_number,
            person.billing_postal_code,
            person.billing_city,
            person.formatted_customer_number,
            person.note,
        ):
            found[KIND_PERSON].append(
                (
                    person_name_key(person),
                    _hit(
                        KIND_PERSON,
                        person.id,
                        person.display_name,
                        person.formatted_customer_number,
                    ),
                )
            )

    groups = []
    for kind in KIND_ORDER:
        entries = sorted(found[kind], key=lambda pair: pair[0])
        if not entries:
            continue
        groups.append(
            SearchGroup(
                kind=kind,
                label=KIND_LABELS[kind],
                hits=[hit for _, hit in entries[:PER_KIND_LIMIT]],
                total=len(entries),
            )
        )
    return groups


def _hit(kind: str, record_id: int, title: str, detail: str) -> SearchHit:
    """Build one hit, with the route its kind leads to."""
    return SearchHit(
        kind=kind,
        record_id=record_id,
        title=title,
        detail=detail,
        route=_ROUTES[kind].format(id=record_id),
    )
