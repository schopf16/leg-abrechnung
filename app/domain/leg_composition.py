"""Determines which Trafokreise a LEG's Messpunkte are spread across.

A LEG's members can each be attached to a different physical Trafokreis
(see `app.models.leg` / `app.models.trafokreis`) whenever their owners
deliberately share one LEG despite being on different transformer
circuits. The grid operator only grants the full same-Trafokreis discount
(project brief) within one Trafokreis; sharing across Trafokreise attracts
a lower rate. This module answers "does this LEG mix Trafokreise" so the
GUI can surface that as a heads-up for the administrator -- the app itself
never computes or bills the actual BKW discount rate.
"""

import sqlite3
from dataclasses import dataclass, field

from app.models import messpunkt as messpunkt_repo
from app.models import standort as standort_repo
from app.models import trafokreis as trafokreis_repo
from app.models.trafokreis import Trafokreis


@dataclass
class LegComposition:
    """Which Trafokreise a LEG's Messpunkte are spread across.

    Attributes:
        leg_id: The LEG this composition describes.
        trafokreise: Distinct Trafokreise at least one of the LEG's
            Messpunkte is attached to (via its Standort), sorted by name.
            A Messpunkt whose Standort has no Trafokreis assigned is not
            represented here.
    """

    leg_id: int
    trafokreise: list[Trafokreis] = field(default_factory=list)

    @property
    def is_mixed(self) -> bool:
        """Whether this LEG spans more than one Trafokreis.

        Returns:
            `True` if the LEG's Messpunkte are attached to two or more
            distinct Trafokreise.
        """
        return len(self.trafokreise) > 1


def compute_leg_composition(connection: sqlite3.Connection, leg_id: int) -> LegComposition:
    """Determine which Trafokreise a LEG's Messpunkte are spread across.

    Args:
        connection: Open SQLite connection.
        leg_id: Primary key of the LEG to inspect.

    Returns:
        A `LegComposition` for that LEG.
    """
    standorte_by_id = {s.id: s for s in standort_repo.list_all(connection)}
    trafokreise_by_id = {t.id: t for t in trafokreis_repo.list_all(connection)}

    trafokreis_ids: set[int] = set()
    for messpunkt in messpunkt_repo.list_all(connection):
        if messpunkt.leg_id != leg_id:
            continue
        standort = standorte_by_id.get(messpunkt.standort_id)
        if standort is None or standort.trafokreis_id is None:
            continue
        trafokreis_ids.add(standort.trafokreis_id)

    trafokreise = sorted(
        (trafokreise_by_id[tid] for tid in trafokreis_ids if tid in trafokreise_by_id),
        key=lambda t: t.name,
    )
    return LegComposition(leg_id=leg_id, trafokreise=trafokreise)
