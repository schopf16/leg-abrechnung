"""One search box that reaches every Stammdaten record.

Until now a record could only be found from the list it lives on: looking up
a Messpunktbezeichnung meant going to Messpunkte first, and a street meant
guessing whether it was filed under Standorte or under a person's billing
address. That is knowledge about this app's filing, demanded of somebody who
only wants to find a meter.

What it searches is **what the lists already search**, no more: the per-list
haystacks were built one at a time and each knows its own record. So this is
a domain function over the models, and the five lists keep their own filters
untouched -- two search implementations would drift, and the one in the
header would be the one nobody tested.

Two deliberate limits:

- **Substring, folded, no fuzziness.** `app.domain.address_lookup` is fuzzy
  because it compares a typed address against three million official ones
  and has to tolerate a typo. Here the administrator is looking for
  something they know exists, and a near miss would offer the wrong member.
  Folding goes through `app.sort_keys.fold_for_sort`, so "Buhler" finds
  "Bühler" exactly as the lists' own sorting folds it.
- **No ranking by score.** Results are grouped by kind in the order of the
  data model (Trafokreis → Standort → Messpunkt → LEG → Person →
  Zuordnung), the same order the drawer lists them in, and sorted inside a
  group by the same keys their list uses. A relevance score would put a
  person above a metering point for reasons nobody can see.
"""

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
    """One found record.

    Attributes:
        kind: One of the `KIND_*` constants.
        record_id: Database id.
        title: What the record is called.
        detail: Where it sits -- an address, a LEG name, a Trafokreis.
        route: Where clicking it goes.
    """

    kind: str
    record_id: int
    title: str
    detail: str
    route: str


@dataclass(frozen=True)
class SearchGroup:
    """The hits of one kind.

    Attributes:
        kind: One of the `KIND_*` constants.
        label: German plural, as the drawer spells it.
        hits: Up to `PER_KIND_LIMIT` of them.
        total: How many there were in all, so the group can say "von 23".
    """

    kind: str
    label: str
    hits: list[SearchHit]
    total: int


def _matches(needle: str, *parts: Optional[str]) -> bool:
    """Whether the folded needle occurs in any of the parts.

    Args:
        needle: Already folded search text.
        *parts: Record fields, any of which may be `None`.

    Returns:
        `True` on the first part that contains it.
    """
    return any(needle in fold_for_sort(part or "") for part in parts)


def search(connection: sqlite3.Connection, query: str) -> list[SearchGroup]:
    """Find every Stammdaten record matching one query.

    Args:
        connection: Open SQLite connection.
        query: What the administrator typed.

    Returns:
        Non-empty groups, in `KIND_ORDER`. Empty for a query shorter than
        `MIN_QUERY_LENGTH`.
    """
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
    """Build one hit, with the route its kind leads to.

    Args:
        kind: One of the `KIND_*` constants.
        record_id: Database id.
        title: What the record is called.
        detail: Where it sits.

    Returns:
        The hit.
    """
    return SearchHit(
        kind=kind,
        record_id=record_id,
        title=title,
        detail=detail,
        route=_ROUTES[kind].format(id=record_id),
    )
