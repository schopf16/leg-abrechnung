"""Whether a substation area or LEG has a workable mix of Producer and Consumer
participants.

Local sharing needs both sides: a substation area/LEG with only Producer
(everyone feeds in, nobody draws from the shared pool) or only Consumer
(nobody feeds in, nothing to share) makes no sense to run as its own LEG,
independent of any BKW discount-rate question. This module answers "does
this substation area/LEG have both sides at all", expressed as a simple
Producer:Consumer participant-count ratio.

**What this module deliberately no longer does** is recommend moving people
out of a pooled LEG into a dedicated one. It used to: both sides present
plus a minimum headcount produced a "could now split off" suggestion. That
test was wrong, because presence is not viability. A substation area with
seven feed-in meters and one consumption meter passed it, and acting on the
advice would have left the producers with almost nobody to share with --
the opposite of what they joined for. A real administrator had deliberately
parked a 34 kWp producer in the pooled LEG for exactly that reason and was
told to undo it.

A ratio threshold would have been the obvious repair. It was not built,
because the decision is not the app's to make: it turns on economics, on
what the participants are willing to do, and on what BKW confirms per
location -- none of which is in this database. The app now states the one
fact it does hold, per metering point, on the LEG detail page: whether that
substation area already has a LEG of its own or would need one founded (see
`app.gui.pages.legs`). The judgement stays with the administrator, who
sorts that list by substation area and decides.

Terms used here, deliberately simple (an earlier, more legally-precise
model based on the BKW 5%-Produktionsregel/Anschlussleistung -- Art. 19e
StromVV -- turned out to need too much manual, hard-to-obtain data per
site to be worth it):

    Producer: a person with a current-or-upcoming Assignment (see
        `app.models.assignment.Assignment.is_current_or_upcoming` -- counts
        an assignment pre-entered ahead of its start date too, not just
        ones already running today; real customer data made this the
        permanent behaviour, not a toggle: an administrator who
        pre-enters a whole future quarter's move-ins in advance had every
        substation area/LEG here show 0:0 under a strict "started today" rule,
        until that date actually arrived) to at least one
        feed-in-MeteringPoint in scope -- "kann Strom liefern". A person
        who both consumes and feeds in counts here too.
    Consumer: a person with a current-or-upcoming Assignment to at least
        one consumption-MeteringPoint in scope -- "bezieht Strom". Same overlap
        applies.

A true prosumer (feeds in AND consumes) is deliberately counted on both
sides -- the question this module answers is whether a supply side and a
demand side both exist at all, not a strict partition of people into two
disjoint camps.

This is deliberately different from billing/distribution
(`app.domain.distribution`) and the historical reading-completeness check
(`app.domain.quality_checks.check_reading_completeness`), which both keep
using the strict `Assignment.covers` unaffected by anything here --
attributing energy to someone before their Assignment's exact start date
would be a real correctness bug there, unlike for this module's
"does/will this arrangement work" question.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models import assignment as assignment_repo
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN


def _moment(reference_date: Optional[date]) -> datetime:
    """Turn an optional reference date into the midnight `datetime` `Assignment.covers` expects.

    Args:
        reference_date: The reference date, or `None` for today.

    Returns:
        Midnight of `reference_date` (or today).
    """
    return datetime.combine(reference_date or date.today(), time())


@dataclass
class ParticipantMix:
    """The Producer:Consumer participant balance for a substation area or LEG.

    Two different things are counted here, and confusing them is what
    made the overview disagree with itself: the **metering points** are
    what a reader adds up against the "Messpunkte" column, while the
    **persons** answer "how many people is this". The overviews show the
    metering points.

    Attributes:
        producer_count: Distinct persons on the feed-in side (see module
            docstring). A person with two feed-in meters counts once.
        consumer_count: Distinct persons on the consumption side.
        producer_metering_points: Feed-in metering points in scope.
        consumer_metering_points: Consumption metering points in scope.
        unassigned_metering_points: Of those, how many have no current or
            upcoming assignment. They are counted in the two numbers
            above -- they exist and they have a direction -- but they are
            reported separately, because a metering point nobody is
            assigned to produces energy with no recipient at billing
            time (see `app.domain.billing_checks`).
    """

    producer_count: int
    consumer_count: int
    producer_metering_points: int = 0
    consumer_metering_points: int = 0
    unassigned_metering_points: int = 0

    @property
    def is_one_sided(self) -> bool:
        """Whether one side is completely empty.

        Judged on the metering points, not the persons: whether energy
        can be shared locally depends on there being meters of both
        directions, regardless of how many people hold them.

        Returns:
            `True` if there are no feed-in, or no consumption, metering
            points at all (a scope with neither is not "one-sided", it is
            simply empty -- also `True` in that case, since it equally
            cannot function as its own LEG).
        """
        return self.producer_metering_points == 0 or self.consumer_metering_points == 0

    @property
    def ratio(self) -> str:
        """The ratio as a simple `"<Producer>:<Consumer>"` string.

        Metering points, so the two numbers add up to the metering point
        count shown beside them.

        Returns:
            E.g. `"9:26"`.
        """
        return f"{self.producer_metering_points}:{self.consumer_metering_points}"

    @property
    def total_persons(self) -> int:
        """The simple sum of `producer_count` and `consumer_count`.

        A true prosumer is counted on both sides (see the module
        docstring), so this is not a deduplicated headcount. Note this is
        **not** what `ratio` shows: that counts metering points, so the
        overview adds up against the "Messpunkte" column beside it. This is
        the people -- seven meters are not seven members.

        Returns:
            `producer_count + consumer_count`.
        """
        return self.producer_count + self.consumer_count

    @property
    def hint(self) -> Optional[str]:
        """A German one-liner if exactly one side is empty, else `None`.

        Returns:
            `None` if both sides are present, or if the scope has no
            participants at all yet (nothing to warn about).
        """
        if self.producer_metering_points and self.consumer_metering_points:
            return None
        if self.producer_metering_points == 0 and self.consumer_metering_points == 0:
            return None
        return (
            "Nur Konsumenten -- niemand liefert lokal geteilten Strom."
            if self.producer_metering_points == 0
            else "Nur Produzenten -- niemand bezieht lokal geteilten Strom."
        )


def compute_participant_mix(
    connection: sqlite3.Connection, site_ids: list[int], reference_date: Optional[date] = None
) -> ParticipantMix:
    """Compute the Producer:Consumer mix for an arbitrary set of sites.

    The one core computation, used both for a hypothetical "this
    substation area as its own LEG" check (`compute_participant_mix_for_substation_area`)
    and the real "this LEG, however it is actually composed" check
    (`compute_participant_mix_for_leg`).

    Args:
        connection: Open SQLite connection.
        site_ids: sites to include.
        reference_date: Reference date for which assignments count as relevant,
            `None` for today.

    Returns:
        The computed `ParticipantMix`.
    """
    site_ids_set = set(site_ids)
    metering_points = [mp for mp in metering_point_repo.list_all(connection) if mp.site_id in site_ids_set]
    return _mix_of(connection, metering_points, reference_date)


def _mix_of(
    connection: sqlite3.Connection, metering_points: list, reference_date: Optional[date] = None
) -> ParticipantMix:
    """Compute the mix over an explicit set of metering points.

    The single place both counts are derived, so they can never be taken
    over different sets: the metering points are counted by their own
    `direction`, the persons from the assignments those metering points
    carry.

    A metering point with no current or upcoming assignment still counts
    towards its direction -- it is part of the LEG and it has one -- but
    contributes no person, and is tallied in
    `unassigned_metering_points`. Dropping it from the direction counts
    is what used to make the two numbers fall short of the metering point
    count beside them, with nothing saying why.

    Args:
        connection: Open SQLite connection.
        metering_points: The metering points in scope.
        reference_date: Reference date for which assignments count as
            relevant, `None` for today.

    Returns:
        The computed `ParticipantMix`.
    """
    moment = _moment(reference_date)

    producer_ids: set[int] = set()
    consumer_ids: set[int] = set()
    producer_metering_points = 0
    consumer_metering_points = 0
    unassigned = 0

    for metering_point in metering_points:
        is_feed_in = metering_point.direction == DIRECTION_FEED_IN
        if is_feed_in:
            producer_metering_points += 1
        elif metering_point.direction == DIRECTION_CONSUMPTION:
            consumer_metering_points += 1
        else:
            continue

        person_ids = {
            assignment.person_id
            for assignment in assignment_repo.list_for_metering_point(connection, metering_point.id)
            if assignment.is_current_or_upcoming(moment)
        }
        if not person_ids:
            unassigned += 1
            continue
        (producer_ids if is_feed_in else consumer_ids).update(person_ids)

    return ParticipantMix(
        producer_count=len(producer_ids),
        consumer_count=len(consumer_ids),
        producer_metering_points=producer_metering_points,
        consumer_metering_points=consumer_metering_points,
        unassigned_metering_points=unassigned,
    )


def compute_participant_mix_for_substation_area(
    connection: sqlite3.Connection, substation_area_id: int, reference_date: Optional[date] = None
) -> ParticipantMix:
    """Hypothetical mix if this substation area's sites formed their own LEG.

    Args:
        connection: Open SQLite connection.
        substation_area_id: Primary key of the substation area.
        reference_date: Reference date, `None` for today.

    Returns:
        The `ParticipantMix` for every site assigned to this substation area.
    """
    site_ids = [s.id for s in site_repo.list_all(connection) if s.substation_area_id == substation_area_id]
    return compute_participant_mix(connection, site_ids, reference_date)


def compute_participant_mix_for_leg(
    connection: sqlite3.Connection, leg_id: int, reference_date: Optional[date] = None
) -> ParticipantMix:
    """The real mix for a LEG as it is actually composed today.

    Args:
        connection: Open SQLite connection.
        leg_id: Primary key of the LEG.
        reference_date: Reference date, `None` for today.

    Returns:
        The `ParticipantMix` over the metering points that belong to this
        LEG.

    Scoped by `leg_id`, not by the sites those metering points sit at:
    LEG membership is a property of the MeteringPoint (see
    `app.models.leg`), and two metering points at one address can belong
    to different LEGs. Going via the sites pulled a neighbour's meter
    into this LEG's figures and made them disagree with the metering
    point count shown beside them.
    """
    metering_points = [mp for mp in metering_point_repo.list_all(connection) if mp.leg_id == leg_id]
    return _mix_of(connection, metering_points, reference_date)
