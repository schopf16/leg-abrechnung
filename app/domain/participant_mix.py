"""Whether a substation area or LEG has a workable mix of Producer and Consumer participants."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from app.models import metering_point as metering_point_repo
from app.models import site as site_repo
from app.models import assignment as assignment_repo
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN


def _moment(reference_date: Optional[date]) -> datetime:
    """Turn an optional reference date into the midnight `datetime` `Assignment.covers` expects."""
    return datetime.combine(reference_date or date.today(), time())


@dataclass
class ParticipantMix:
    """The Producer:Consumer participant balance for a substation area or LEG."""

    producer_count: int
    consumer_count: int
    producer_metering_points: int = 0
    consumer_metering_points: int = 0
    unassigned_metering_points: int = 0

    @property
    def is_one_sided(self) -> bool:
        """Whether one side is completely empty."""
        return self.producer_metering_points == 0 or self.consumer_metering_points == 0

    @property
    def ratio(self) -> str:
        """The ratio as a simple `"<Producer>:<Consumer>"` string."""
        return f"{self.producer_metering_points}:{self.consumer_metering_points}"

    @property
    def total_persons(self) -> int:
        """The simple sum of `producer_count` and `consumer_count`."""
        return self.producer_count + self.consumer_count

    @property
    def hint(self) -> Optional[str]:
        """A German one-liner if exactly one side is empty, else `None`."""
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
    """Compute the Producer:Consumer mix for an arbitrary set of sites."""
    site_ids_set = set(site_ids)
    metering_points = [mp for mp in metering_point_repo.list_all(connection) if mp.site_id in site_ids_set]
    return _mix_of(connection, metering_points, reference_date)


def _mix_of(
    connection: sqlite3.Connection, metering_points: list, reference_date: Optional[date] = None
) -> ParticipantMix:
    """Compute the mix over an explicit set of metering points."""
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
    """Hypothetical mix if this substation area's sites formed their own LEG."""
    site_ids = [s.id for s in site_repo.list_all(connection) if s.substation_area_id == substation_area_id]
    return compute_participant_mix(connection, site_ids, reference_date)


def compute_participant_mix_for_leg(
    connection: sqlite3.Connection, leg_id: int, reference_date: Optional[date] = None
) -> ParticipantMix:
    """The real mix for a LEG as it is actually composed today."""
    metering_points = [mp for mp in metering_point_repo.list_all(connection) if mp.leg_id == leg_id]
    return _mix_of(connection, metering_points, reference_date)


@dataclass
class ParticipantRoles:
    """How many **connections** are on each side, counted once each."""

    prosumers: int
    consumers: int
    feed_in_only: list[tuple[int, int]]

    @property
    def total(self) -> int:
        """Every connection taking part, each counted once."""
        return self.prosumers + self.consumers


def compute_participant_roles(
    connection: sqlite3.Connection, reference_date: Optional[date] = None
) -> ParticipantRoles:
    """Count the deployment's connections by side, without double counting."""
    moment = _moment(reference_date)
    feed_in: set[tuple[int, int]] = set()
    consumption: set[tuple[int, int]] = set()

    for metering_point in metering_point_repo.list_all(connection):
        if metering_point.direction == DIRECTION_FEED_IN:
            side = feed_in
        elif metering_point.direction == DIRECTION_CONSUMPTION:
            side = consumption
        else:
            continue
        for assignment in assignment_repo.list_for_metering_point(connection, metering_point.id):
            if assignment.is_current_or_upcoming(moment):
                side.add((assignment.person_id, metering_point.site_id))

    return ParticipantRoles(
        prosumers=len(feed_in),
        consumers=len(consumption - feed_in),
        feed_in_only=sorted(feed_in - consumption),
    )
