"""Whether a substation area or LEG has a workable mix of Prosumer and Consumer
participants.

Local sharing needs both sides: a substation area/LEG with only Prosumer
(everyone feeds in, nobody draws from the shared pool) or only Consumer
(nobody feeds in, nothing to share) makes no sense to run as its own LEG,
independent of any BKW discount-rate question. This module answers "does
this substation area/LEG have both sides at all", expressed as a simple
Prosumer:Consumer participant-count ratio, and -- built on top of that --
"could this substation area now split off into its own LEG" once it has both
sides but its participants are still folded into a larger, multi-
substation area LEG (a lower-BKW-discount arrangement, see
`app.domain.leg_composition`). That second question also requires the
substation area to have at least `LegSettings.leg_gruendung_min_personen`
people overall (`ParticipantMix.gesamt_personen`, default 7) -- both
sides being present is necessary but not sufficient: a substation area with
just one Prosumer and one Consumer is rarely worth founding a dedicated
LEG over, so `leg_should_split`/`find_upgrade_candidates` take this as an
explicit `min_personen` parameter rather than hardcoding it.

Terms used here, deliberately simple (an earlier, more legally-precise
model based on the BKW 5%-Produktionsregel/Anschlussleistung -- Art. 19e
StromVV -- turned out to need too much manual, hard-to-obtain data per
site to be worth it):

    Prosumer: a person with a current-or-upcoming Zuordnung (see
        `app.models.zuordnung.Zuordnung.is_current_or_upcoming` -- counts
        an assignment pre-entered ahead of its start date too, not just
        ones already running today; real customer data made this the
        permanent behaviour, not a toggle: an administrator who
        pre-enters a whole future quarter's move-ins in advance had every
        substation area/LEG here show 0:0 under a strict "started today" rule,
        until that date actually arrived) to at least one
        Einspeisung-MeteringPoint in scope -- "kann Strom liefern". A person
        who both consumes and feeds in counts here too.
    Consumer: a person with a current-or-upcoming Zuordnung to at least
        one Bezug-MeteringPoint in scope -- "bezieht Strom". Same overlap
        applies.

A true prosumer (feeds in AND consumes) is deliberately counted on both
sides -- the question this module answers is whether a supply side and a
demand side both exist at all, not a strict partition of people into two
disjoint camps.

This is deliberately different from billing/distribution
(`app.domain.distribution`) and the historical reading-completeness check
(`app.domain.quality_checks.check_reading_completeness`), which both keep
using the strict `Zuordnung.covers` unaffected by anything here --
attributing energy to someone before their Zuordnung's exact start date
would be a real correctness bug there, unlike for this module's
"does/will this arrangement work" question.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from app.domain.leg_composition import compute_leg_composition
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models import substation_area as substation_area_repo
from app.models import zuordnung as zuordnung_repo
from app.models.leg import Leg
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN
from app.models.substation_area import SubstationArea


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
    """The Prosumer:Consumer participant balance for a substation area or LEG.

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
            (a substation area/LEG with neither is not "one-sided", it is
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
    def gesamt_personen(self) -> int:
        """The simple sum of `prosumer_count` and `consumer_count`.

        A true prosumer is counted on both sides (see the module
        docstring), so this is not a deduplicated headcount -- it is
        exactly the two numbers shown together in `verhaeltnis` added up,
        matching how an administrator reads that badge. Used to gate the
        LEG-upgrade suggestion on `LegSettings.leg_gruendung_min_personen`
        (see `leg_should_split`/`find_upgrade_candidates`): a substation area
        with both sides present but too few people overall is not worth
        splitting off into its own LEG.

        Returns:
            `prosumer_count + consumer_count`.
        """
        return self.prosumer_count + self.consumer_count

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
    connection: sqlite3.Connection, site_ids: list[int], stichtag: Optional[date] = None
) -> ParticipantMix:
    """Compute the Prosumer:Consumer mix for an arbitrary set of sites.

    The one core computation, used both for a hypothetical "this
    substation area as its own LEG" check (`compute_participant_mix_for_substation_area`)
    and the real "this LEG, however it is actually composed" check
    (`compute_participant_mix_for_leg`).

    Args:
        connection: Open SQLite connection.
        site_ids: sites to include.
        stichtag: Reference date for which Zuordnungen count as relevant,
            `None` for today.

    Returns:
        The computed `ParticipantMix`.
    """
    moment = _moment(stichtag)
    site_ids_set = set(site_ids)

    prosumer_ids: set[int] = set()
    consumer_ids: set[int] = set()
    for metering_point in metering_point_repo.list_all(connection):
        if metering_point.site_id not in site_ids_set:
            continue
        for zuordnung in zuordnung_repo.list_for_metering_point(connection, metering_point.id):
            if not zuordnung.is_current_or_upcoming(moment):
                continue
            if metering_point.direction == DIRECTION_FEED_IN:
                prosumer_ids.add(zuordnung.person_id)
            elif metering_point.direction == DIRECTION_CONSUMPTION:
                consumer_ids.add(zuordnung.person_id)

    return ParticipantMix(prosumer_count=len(prosumer_ids), consumer_count=len(consumer_ids))


def compute_participant_mix_for_substation_area(
    connection: sqlite3.Connection, substation_area_id: int, stichtag: Optional[date] = None
) -> ParticipantMix:
    """Hypothetical mix if this substation area's sites formed their own LEG.

    Args:
        connection: Open SQLite connection.
        substation_area_id: Primary key of the substation area.
        stichtag: Reference date, `None` for today.

    Returns:
        The `ParticipantMix` for every site assigned to this substation area.
    """
    site_ids = [s.id for s in site_repo.list_all(connection) if s.substation_area_id == substation_area_id]
    return compute_participant_mix(connection, site_ids, stichtag)


def compute_participant_mix_for_leg(
    connection: sqlite3.Connection, leg_id: int, stichtag: Optional[date] = None
) -> ParticipantMix:
    """The real mix for a LEG as it is actually composed today.

    Args:
        connection: Open SQLite connection.
        leg_id: Primary key of the LEG.
        stichtag: Reference date, `None` for today.

    Returns:
        The `ParticipantMix` for every site with at least one
        MeteringPoint assigned to this LEG.
    """
    site_ids = sorted({
        mp.site_id for mp in metering_point_repo.list_all(connection) if mp.leg_id == leg_id
    })
    return compute_participant_mix(connection, site_ids, stichtag)


def leg_should_split(
    connection: sqlite3.Connection, leg_id: int, stichtag: Optional[date] = None,
    *, min_personen: int = 0,
) -> bool:
    """Whether a mixed LEG's substation areas would each work fine standalone.

    If every substation area a LEG spans would, on its own, already have both a
    Prosumer and a Consumer (see `compute_participant_mix_for_substation_area`)
    and enough people overall, splitting the LEG into one dedicated LEG
    per substation area strands nobody -- and earns every one of them the
    better single-substation area BKW discount instead of today's shared, lower
    one (the app never computes or displays the actual rate, see
    `app.domain.leg_composition`).

    Args:
        connection: Open SQLite connection.
        leg_id: Primary key of the LEG.
        stichtag: Reference date, `None` for today.
        min_personen: Minimum `ParticipantMix.gesamt_personen` each
            substation area must reach on its own for the split to be
            suggested -- pass `LegSettings.leg_gruendung_min_personen`
            (default 0, i.e. no minimum, for callers that only care about
            the plain both-sides-present question).

    Returns:
        `True` only if the LEG spans more than one substation area (see
        `app.domain.leg_composition.compute_leg_composition`) AND *every*
        one of those substation areas is independently non-one-sided and has
        at least `min_personen` people -- deliberately requiring all of
        them, not just one: if even a single substation area would be
        one-sided or too small alone, splitting would strand its
        participants, so the LEG stays better off shared for now.
    """
    composition = compute_leg_composition(connection, leg_id)
    if not composition.is_mixed:
        return False
    for substation_area in composition.substation_areas:
        mix = compute_participant_mix_for_substation_area(connection, substation_area.id, stichtag)
        if mix.ist_einseitig or mix.gesamt_personen < min_personen:
            return False
    return True


@dataclass
class UpgradeCandidate:
    """A substation area that could now form its own (better-discounted) LEG.

    Attributes:
        substation area: The substation area with a newly-workable Prosumer/Consumer mix.
        mixed_legs: The LEGs currently used by this substation area's
            participants that span more than one substation area -- these are
            the ones a dedicated LEG would let them leave.
        person_count: Distinct persons (via a current-or-upcoming
            Zuordnung) at this substation area whose MeteringPoint currently
            belongs to one of `mixed_legs`.
        mix: The hypothetical solo-substation area `ParticipantMix` that shows
            this is now viable.
    """

    substation_area: SubstationArea
    mixed_legs: list[Leg]
    person_count: int
    mix: ParticipantMix


def find_upgrade_candidates(
    connection: sqlite3.Connection, stichtag: Optional[date] = None, *, min_personen: int = 0
) -> list[UpgradeCandidate]:
    """Find substation areas that now have both sides but are still split across
    a multi-substation-area LEG.

    Args:
        connection: Open SQLite connection.
        stichtag: Reference date, `None` for today.
        min_personen: Minimum `ParticipantMix.gesamt_personen` a substation area
            must reach to be suggested -- pass `LegSettings.
            leg_gruendung_min_personen` (default 0, i.e. no minimum). A
            substation area with only, say, one Prosumer and one Consumer is
            technically non-one-sided but rarely worth founding a
            dedicated LEG over; this keeps the suggestion from firing
            until there is a real number of people behind it.

    Returns:
        One `UpgradeCandidate` per substation area with a newly-workable
        Prosumer/Consumer mix (both sides present, `gesamt_personen >=
        min_personen`) whose participants are (at least partly) still in
        a mixed LEG. A substation area already fully moved into a dedicated
        LEG of its own produces no candidate -- the recommendation is
        already acted on.
    """
    moment = _moment(stichtag)
    sites = site_repo.list_all(connection)
    metering_points = metering_point_repo.list_all(connection)
    legs_by_id = {leg.id: leg for leg in leg_repo.list_all(connection)}

    candidates: list[UpgradeCandidate] = []
    for substation_area in substation_area_repo.list_all(connection):
        site_ids = {s.id for s in sites if s.substation_area_id == substation_area.id}
        if not site_ids:
            continue

        mix = compute_participant_mix(connection, list(site_ids), stichtag)
        if mix.ist_einseitig or mix.gesamt_personen < min_personen:
            continue

        leg_ids_here = {
            mp.leg_id for mp in metering_points if mp.site_id in site_ids and mp.leg_id is not None
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
        for mp in metering_points:
            if mp.site_id not in site_ids or mp.leg_id not in mixed_leg_ids:
                continue
            for zuordnung in zuordnung_repo.list_for_metering_point(connection, mp.id):
                if zuordnung.is_current_or_upcoming(moment):
                    person_ids.add(zuordnung.person_id)

        candidates.append(
            UpgradeCandidate(
                substation_area=substation_area, mixed_legs=mixed_legs,
                person_count=len(person_ids), mix=mix,
            )
        )

    return candidates
