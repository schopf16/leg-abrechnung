"""Determines which substation areas a LEG's metering points are spread across."""

import sqlite3
from dataclasses import dataclass, field

from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models.substation_area import SubstationArea


@dataclass
class LegComposition:
    """Which substation areas a LEG's metering points are spread across."""

    leg_id: int
    substation_areas: list[SubstationArea] = field(default_factory=list)

    @property
    def is_mixed(self) -> bool:
        """Whether this LEG spans more than one substation area."""
        return len(self.substation_areas) > 1


def compute_leg_composition(connection: sqlite3.Connection, leg_id: int) -> LegComposition:
    """Determine which substation areas a LEG's metering points are spread across."""
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
