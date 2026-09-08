"""Whether a Trafokreis or LEG has a workable mix of Prosumer and Consumer
participants.

Local sharing needs both sides: a Trafokreis/LEG with only Prosumer
(everyone feeds in, nobody draws from the shared pool) or only Consumer
(nobody feeds in, nothing to share) makes no sense to run as its own LEG,
independent of any BKW discount-rate question. This module answers "does
this Trafokreis/LEG have both sides at all", expressed as a simple
Prosumer:Consumer participant-count ratio, and -- built on top of that --
"could this Trafokreis now split off into its own LEG" once it has both
sides but its participants are still folded into a larger, multi-
Trafokreis LEG (a lower-BKW-discount arrangement, see
`app.domain.leg_composition`).

Terms used here, deliberately simple (an earlier, more legally-precise
model based on the BKW 5%-Produktionsregel/Anschlussleistung -- Art. 19e
StromVV -- turned out to need too much manual, hard-to-obtain data per
Standort to be worth it):

    Prosumer: a person with an active Zuordnung to at least one
        Einspeisung-Messpunkt in scope -- "kann Strom liefern". A person
        who both consumes and feeds in counts here too.
    Consumer: a person with an active Zuordnung to at least one
        Bezug-Messpunkt in scope -- "bezieht Strom". Same overlap applies.

A true prosumer (feeds in AND consumes) is deliberately counted on both
sides -- the question this module answers is whether a supply side and a
demand side both exist at all, not a strict partition of people into two
disjoint camps.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from app.domain.leg_composition import compute_leg_composition
from app.models import leg as leg_repo
from app.models import messpunkt as messpunkt_repo
from app.models import standort as standort_repo
from app.models import trafokreis as trafokreis_repo
from app.models import zuordnung as zuordnung_repo
from app.models.leg import Leg
from app.models.messpunkt import MESSRICHTUNG_BEZUG, MESSRICHTUNG_EINSPEISUNG
from app.models.trafokreis import Trafokreis


def _moment(stichtag: Optional[date]) -> datetime:
    """Turn an optional Stichtag into the midnight `datetime` `Zuordnung.covers` expects.

    Args:
        stichtag: The reference date, or `None` for today.

    Returns:
        Midnight of `stichtag` (or today).
    """
    return datetime.combine(stichtag or date.today(), time())


@dataclass
class ParticipantMix:
    """The Prosumer:Consumer participant balance for a Trafokreis or LEG.

    Attributes:
        prosumer_count: Distinct persons counted as Prosumer (see module
            docstring).
        consumer_count: Distinct persons counted as Consumer.
    """

    prosumer_count: int
    consumer_count: int

    @property
    def ist_einseitig(self) -> bool:
        """Whether one side is completely empty.

        Returns:
            `True` if there are no Prosumer, or no Consumer, at all
            (a Trafokreis/LEG with neither is not "one-sided", it is
            simply empty -- also `True` in that case, since it equally
            cannot function as its own LEG).
        """
        return self.prosumer_count == 0 or self.consumer_count == 0

    @property
    def verhaeltnis(self) -> str:
        """The ratio as a simple `"<Prosumer>:<Consumer>"` string.

        Returns:
            E.g. `"3:5"`.
        """
        return f"{self.prosumer_count}:{self.consumer_count}"

    @property
    def hinweis(self) -> Optional[str]:
        """A German one-liner if exactly one side is empty, else `None`.

        Returns:
            `None` if both sides are present, or if the scope has no
            participants at all yet (nothing to warn about).
        """
        if self.prosumer_count and self.consumer_count:
            return None
        if self.prosumer_count == 0 and self.consumer_count == 0:
            return None
        return (
            "Nur Consumer -- niemand liefert lokal geteilten Strom."
            if self.prosumer_count == 0
            else "Nur Prosumer -- niemand bezieht lokal geteilten Strom."
        )


def compute_participant_mix(
    connection: sqlite3.Connection, standort_ids: list[int], stichtag: Optional[date] = None
) -> ParticipantMix:
    """Compute the Prosumer:Consumer mix for an arbitrary set of Standorte.

    The one core computation, used both for a hypothetical "this
    Trafokreis as its own LEG" check (`compute_participant_mix_for_trafokreis`)
    and the real "this LEG, however it is actually composed" check
    (`compute_participant_mix_for_leg`).

    Args:
        connection: Open SQLite connection.
        standort_ids: Standorte to include.
        stichtag: Reference date for which Zuordnungen count as active,
            `None` for today.

    Returns:
        The computed `ParticipantMix`.
    """
    moment = _moment(stichtag)
    standort_ids_set = set(standort_ids)

    prosumer_ids: set[int] = set()
    consumer_ids: set[int] = set()
    for messpunkt in messpunkt_repo.list_all(connection):
        if messpunkt.standort_id not in standort_ids_set:
            continue
        for zuordnung in zuordnung_repo.list_for_messpunkt(connection, messpunkt.id):
            if not zuordnung.covers(moment):
                continue
            if messpunkt.messrichtung == MESSRICHTUNG_EINSPEISUNG:
                prosumer_ids.add(zuordnung.person_id)
            elif messpunkt.messrichtung == MESSRICHTUNG_BEZUG:
                consumer_ids.add(zuordnung.person_id)

    return ParticipantMix(prosumer_count=len(prosumer_ids), consumer_count=len(consumer_ids))


def compute_participant_mix_for_trafokreis(
    connection: sqlite3.Connection, trafokreis_id: int, stichtag: Optional[date] = None
) -> ParticipantMix:
    """Hypothetical mix if this Trafokreis's Standorte formed their own LEG.

    Args:
        connection: Open SQLite connection.
        trafokreis_id: Primary key of the Trafokreis.
        stichtag: Reference date, `None` for today.

    Returns:
        The `ParticipantMix` for every Standort assigned to this Trafokreis.
    """
    standort_ids = [s.id for s in standort_repo.list_all(connection) if s.trafokreis_id == trafokreis_id]
    return compute_participant_mix(connection, standort_ids, stichtag)


def compute_participant_mix_for_leg(
    connection: sqlite3.Connection, leg_id: int, stichtag: Optional[date] = None
) -> ParticipantMix:
    """The real mix for a LEG as it is actually composed today.

    Args:
        connection: Open SQLite connection.
        leg_id: Primary key of the LEG.
        stichtag: Reference date, `None` for today.

    Returns:
        The `ParticipantMix` for every Standort with at least one
        Messpunkt assigned to this LEG.
    """
    standort_ids = sorted({
        mp.standort_id for mp in messpunkt_repo.list_all(connection) if mp.leg_id == leg_id
    })
    return compute_participant_mix(connection, standort_ids, stichtag)


def leg_should_split(
    connection: sqlite3.Connection, leg_id: int, stichtag: Optional[date] = None
) -> bool:
    """Whether a mixed LEG's Trafokreise would each work fine standalone.

    If every Trafokreis a LEG spans would, on its own, already have both a
    Prosumer and a Consumer (see `compute_participant_mix_for_trafokreis`),
    splitting the LEG into one dedicated LEG per Trafokreis strands nobody
    -- and earns every one of them the better single-Trafokreis BKW
    discount instead of today's shared, lower one (the app never computes
    or displays the actual rate, see `app.domain.leg_composition`).

    Args:
        connection: Open SQLite connection.
        leg_id: Primary key of the LEG.
        stichtag: Reference date, `None` for today.

    Returns:
        `True` only if the LEG spans more than one Trafokreis (see
        `app.domain.leg_composition.compute_leg_composition`) AND *every*
        one of those Trafokreise is independently non-one-sided --
        deliberately requiring all of them, not just one: if even a single
        Trafokreis would be one-sided alone, splitting would strand its
        participants, so the LEG stays better off shared for now.
    """
    composition = compute_leg_composition(connection, leg_id)
    if not composition.is_mixed:
        return False
    return all(
        not compute_participant_mix_for_trafokreis(connection, trafokreis.id, stichtag).ist_einseitig
        for trafokreis in composition.trafokreise
    )


@dataclass
class UpgradeCandidate:
    """A Trafokreis that could now form its own (better-discounted) LEG.

    Attributes:
        trafokreis: The Trafokreis with a newly-workable Prosumer/Consumer mix.
        mixed_legs: The LEGs currently used by this Trafokreis's
            participants that span more than one Trafokreis -- these are
            the ones a dedicated LEG would let them leave.
        person_count: Distinct persons (via an active Zuordnung) at this
            Trafokreis whose Messpunkt currently belongs to one of
            `mixed_legs`.
        mix: The hypothetical solo-Trafokreis `ParticipantMix` that shows
            this is now viable.
    """

    trafokreis: Trafokreis
    mixed_legs: list[Leg]
    person_count: int
    mix: ParticipantMix


def find_upgrade_candidates(
    connection: sqlite3.Connection, stichtag: Optional[date] = None
) -> list[UpgradeCandidate]:
    """Find Trafokreise that now have both sides but are still split across
    a multi-Trafokreis LEG.

    Args:
        connection: Open SQLite connection.
        stichtag: Reference date, `None` for today.

    Returns:
        One `UpgradeCandidate` per Trafokreis with a newly-workable
        Prosumer/Consumer mix whose participants are (at least partly)
        still in a mixed LEG. A Trafokreis already fully moved into a
        dedicated LEG of its own produces no candidate -- the
        recommendation is already acted on.
    """
    moment = _moment(stichtag)
    standorte = standort_repo.list_all(connection)
    messpunkte = messpunkt_repo.list_all(connection)
    legs_by_id = {leg.id: leg for leg in leg_repo.list_all(connection)}

    candidates: list[UpgradeCandidate] = []
    for trafokreis in trafokreis_repo.list_all(connection):
        standort_ids = {s.id for s in standorte if s.trafokreis_id == trafokreis.id}
        if not standort_ids:
            continue

        mix = compute_participant_mix(connection, list(standort_ids), stichtag)
        if mix.ist_einseitig:
            continue

        leg_ids_here = {
            mp.leg_id for mp in messpunkte if mp.standort_id in standort_ids and mp.leg_id is not None
        }
        mixed_legs = sorted(
            (
                legs_by_id[leg_id] for leg_id in leg_ids_here
                if leg_id in legs_by_id and compute_leg_composition(connection, leg_id).is_mixed
            ),
            key=lambda leg: leg.name,
        )
        if not mixed_legs:
            continue

        mixed_leg_ids = {leg.id for leg in mixed_legs}
        person_ids: set[int] = set()
        for mp in messpunkte:
            if mp.standort_id not in standort_ids or mp.leg_id not in mixed_leg_ids:
                continue
            for zuordnung in zuordnung_repo.list_for_messpunkt(connection, mp.id):
                if zuordnung.covers(moment):
                    person_ids.add(zuordnung.person_id)

        candidates.append(
            UpgradeCandidate(
                trafokreis=trafokreis, mixed_legs=mixed_legs,
                person_count=len(person_ids), mix=mix,
            )
        )

    return candidates
