"""Determines which substation areas a LEG's metering points are spread across.

A LEG's members can each be attached to a different physical substation area
(see `app.models.leg` / `app.models.substation_area`) whenever their owners
deliberately share one LEG despite being on different transformer
circuits. The grid operator only grants the full same-substation-area discount
(project brief) within one substation area; sharing across substation areas attracts
a lower rate. This module answers "does this LEG mix substation areas" so the
GUI can surface that as a heads-up for the administrator -- the app itself
never computes or bills the actual BKW discount rate.
"""

import sqlite3
from dataclasses import dataclass, field

from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.substation_area import SubstationArea


@dataclass
class LegComposition:
    """Which substation areas a LEG's metering points are spread across.

    Attributes:
        leg_id: The LEG this composition describes.
        substation areas: Distinct substation areas at least one of the LEG's
            metering points is attached to (via its site), sorted by name.
            A MeteringPoint whose site has no substation area assigned is not
            represented here.
    """

    leg_id: int
    substation_areas: list[SubstationArea] = field(default_factory=list)

    @property
    def is_mixed(self) -> bool:
        """Whether this LEG spans more than one substation area.

        Returns:
            `True` if the LEG's metering points are attached to two or more
            distinct substation areas.
        """
        return len(self.substation_areas) > 1


def compute_leg_composition(connection: sqlite3.Connection, leg_id: int) -> LegComposition:
    """Determine which substation areas a LEG's metering points are spread across.

    Args:
        connection: Open SQLite connection.
        leg_id: Primary key of the LEG to inspect.

    Returns:
        A `LegComposition` for that LEG.
    """
    sites_by_id = {s.id: s for s in site_repo.list_all(connection)}
    substation_areas_by_id = {t.id: t for t in substation_area_repo.list_all(connection)}

    substation_area_ids: set[int] = set()
    for metering_point in metering_point_repo.list_all(connection):
        if metering_point.leg_id != leg_id:
            continue
        site = sites_by_id.get(metering_point.site_id)
        if site is None or site.substation_area_id is None:
            continue
        substation_area_ids.add(site.substation_area_id)

    substation_areas = sorted(
        (substation_areas_by_id[tid] for tid in substation_area_ids if tid in substation_areas_by_id),
        key=lambda t: t.name,
    )
    return LegComposition(leg_id=leg_id, substation_areas=substation_areas)
