"""Which Textbaustein is due for one tracker, and what was already sent.

The rule lives here rather than on the pages, because Aufnahmen and Austritte
share it. Two triggers and no more vocabulary: a baustein is due *once a step
has its date* (mails about something the administrator did) or *while a step
has none* (the reminder, with a deadline in days on the baustein).
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.models import message_template as template_repo
from app.models import person_message_log as log_repo
from app.models.message_template import (
    OCCASION_OFFBOARDING,
    OCCASION_ONBOARDING,
    TRIGGER_STEP_DONE,
    TRIGGER_STEP_PENDING,
    MessageTemplate,
)
from app.models.person_message_log import CHANNEL_MANUAL, PersonMessageLog
from app.models.person_offboarding import STEPS as OFFBOARDING_STEPS
from app.models.person_onboarding import STEPS as ONBOARDING_STEPS

#: The steps each occasion's trackers offer, so the labels shown beside a
#: due baustein come from the one place that defines them.
STEPS_BY_OCCASION = {
    OCCASION_ONBOARDING: ONBOARDING_STEPS,
    OCCASION_OFFBOARDING: OFFBOARDING_STEPS,
}


@dataclass(frozen=True)
class DueMessage:
    """One baustein that may be sent for one tracker right now."""

    template: MessageTemplate
    step_label: str
    days_waiting: Optional[int]
    log: Optional[PersonMessageLog] = None

    @property
    def was_sent(self) -> bool:
        """Whether this baustein is settled -- really sent, or marked done."""
        return self.log is not None

    @property
    def sent_on(self) -> str:
        """The day it was settled, or `""`."""
        return self.log.sent_on if self.log else ""

    @property
    def by_hand(self) -> bool:
        """Whether it was marked done rather than sent, so the card can say so."""
        return self.log.by_hand if self.log else False


def text_for(connection: sqlite3.Connection, occasion: str) -> tuple[str, str]:
    """The subject and body stored for one occasion."""
    templates = template_repo.list_for_occasion(connection, occasion)
    if not templates:
        return "", ""
    return templates[0].subject, templates[0].body


def last_activity(tracker, steps: list[tuple[str, str]]) -> date:
    """The most recent day something happened on this tracker.

    The deadline of a `step_pending` baustein counts from here. Deliberately
    not from the day the earlier mail was sent: a contract handed over on
    paper leaves no log entry, and the reminder would then never become due.
    """
    dates = [value for attribute, _ in steps if (value := getattr(tracker, attribute, None)) is not None]
    return max(dates) if dates else date.fromisoformat(tracker.created_at[:10])


def due_templates(
    connection: sqlite3.Connection,
    tracker,
    occasion: str,
    *,
    today: Optional[date] = None,
) -> list[DueMessage]:
    """Which bausteine may be sent for this one tracker, in the stored order."""
    return _due_for(
        tracker,
        occasion,
        template_repo.list_for_occasion(connection, occasion),
        log_repo.latest_by_template(connection, tracker.person_id),
        today or date.today(),
    )


def due_by_person(
    connection: sqlite3.Connection,
    trackers: list,
    occasion: str,
    *,
    today: Optional[date] = None,
) -> dict[int, list[DueMessage]]:
    """The same answer for a whole worklist, with two queries instead of two per card."""
    templates = template_repo.list_for_occasion(connection, occasion)
    everything = log_repo.latest_everywhere(connection)
    day = today or date.today()
    result: dict[int, list[DueMessage]] = {}
    for tracker in trackers:
        latest = {
            template_id: entry
            for (person_id, template_id), entry in everything.items()
            if person_id == tracker.person_id
        }
        result[tracker.person_id] = _due_for(tracker, occasion, templates, latest, day)
    return result


def mark_done(
    connection: sqlite3.Connection,
    person_id: int,
    template: MessageTemplate,
    occasion: str,
) -> int:
    """Settle one baustein without sending anything.

    For the letter that was handed over on paper before this feature existed.
    The row carries no text and no recipients, because there was no message
    -- `channel` is what the card reads to print "von Hand" rather than a
    bare date that would claim a mail went out.
    """
    return log_repo.record(
        connection,
        person_id=person_id,
        template_id=template.id,
        occasion=occasion,
        step=template.step,
        subject="",
        body="",
        recipient_emails=[],
        attachment_filenames=[],
        channel=CHANNEL_MANUAL,
    )


def _due_for(
    tracker,
    occasion: str,
    templates: list[MessageTemplate],
    latest: dict[int, PersonMessageLog],
    day: date,
) -> list[DueMessage]:
    """Apply the two triggers to one tracker against already-loaded templates."""
    steps = STEPS_BY_OCCASION.get(occasion, [])
    labels = dict(steps)
    reference = last_activity(tracker, steps)

    due: list[DueMessage] = []
    for template in templates:
        if template.step not in labels:
            # The step was renamed, or the occasion changed after the step
            # was picked. Nothing to hang off, so nothing is offered.
            continue
        reached = getattr(tracker, template.step, None)
        waiting: Optional[int] = None
        if template.trigger_kind == TRIGGER_STEP_DONE:
            if reached is None:
                continue
        elif template.trigger_kind == TRIGGER_STEP_PENDING:
            if reached is not None:
                continue
            waiting = (day - reference).days
            if waiting < (template.deadline_days or 0):
                continue
        else:
            continue
        due.append(
            DueMessage(
                template=template,
                step_label=labels[template.step],
                days_waiting=waiting,
                log=latest.get(template.id),
            )
        )
    return due
