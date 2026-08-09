"""Synchronizes the leg-ittigen.ch registration inbox into `web_registration`.

Orchestrates `app.importers.cloudflare_client` -> `app.models.
web_registration`: fetches every submission newer than the stored cursor
(looping while the API's 500-entry page limit is hit, until an empty
response confirms there is nothing left), applies each one to the local
inbox, and advances the cursor so the same Cloudflare entry is never
re-fetched.

Matching is done by email address -- the only identity field every
registration is guaranteed to carry (a registration can report zero, one
or several meters, so the meter set cannot serve as the key). Accepted,
deliberate limitation: if two different people happen to submit with the
same email address (e.g. a couple), a second submission overwrites the
first's row instead of creating its own -- at the expected scale of a
single-municipality LEG, and since the administrator reviews every entry
manually before acting on it anyway, no extra heuristic is built for this.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from app.importers.cloudflare_client import RegistrationSubmission, fetch_new_registrations
from app.models import settings as settings_repo
from app.models import web_registration as web_registration_repo
from app.models.web_registration import WebRegistration, WebRegistrationMeter

#: WebRegistration fields compared to decide whether a repeat submission
#: actually changed anything (meters are compared separately as a set).
_COMPARED_FIELDS = (
    "firma",
    "anrede",
    "vorname",
    "nachname",
    "strasse",
    "hausnummer",
    "plz",
    "ort",
    "telefon",
    "bkw_kundennummer",
    "iban",
    "message",
)


@dataclass
class RegistrationSyncResult:
    """Outcome of one `sync_registrations` run, for display in the GUI.

    Attributes:
        neu: Number of newly created inbox rows.
        aktualisiert: Number of existing rows updated because a repeat
            submission for the same email changed its content (any
            compared field, or its reported meters).
        unveraendert: Number of repeat submissions with identical content
            to what is already stored (no-ops).
        warnings: Human-readable (German) messages about skipped entries
            (currently: submissions with no email address).
    """

    neu: int = 0
    aktualisiert: int = 0
    unveraendert: int = 0
    warnings: list[str] = field(default_factory=list)


def sync_registrations(connection: sqlite3.Connection, token: str) -> RegistrationSyncResult:
    """Fetch and apply every new/changed registration since the last sync.

    Args:
        connection: Open SQLite connection.
        token: Bearer token for the leg-ittigen.ch API (see
            `app.config.get_leg_api_token`).

    Returns:
        A `RegistrationSyncResult` summarizing what happened.

    Raises:
        app.importers.cloudflare_client.CloudflareAuthError: If the token
            is missing or invalid.
        app.importers.cloudflare_client.CloudflareApiError: For any other
            failure talking to the API.
    """
    result = RegistrationSyncResult()
    settings = settings_repo.get_settings(connection)
    cursor = settings.web_registration_cursor

    while True:
        batch = fetch_new_registrations(cursor, token)
        if not batch:
            break

        for submission in batch:
            _apply_submission(connection, submission, result)
            cursor = max(cursor, submission.cloudflare_id)

        # Advance the cursor after every batch (including no-op/skipped
        # entries) so a failure partway through a later batch never causes
        # already-processed submissions to be re-fetched.
        settings.web_registration_cursor = cursor
        settings_repo.update_settings(connection, settings)

    return result


def _apply_submission(
    connection: sqlite3.Connection,
    submission: RegistrationSubmission,
    result: RegistrationSyncResult,
) -> None:
    """Insert, update or ignore one submission, updating `result` in place.

    Args:
        connection: Open SQLite connection.
        submission: One fetched registration submission.
        result: Running tally to update in place.

    Returns:
        None.
    """
    if not submission.email:
        who = f"{submission.vorname} {submission.nachname}".strip() or submission.firma or "?"
        result.warnings.append(
            f"Registrierung von „{who}“ (Cloudflare-ID {submission.cloudflare_id}) "
            "übersprungen: keine E-Mail-Adresse angegeben."
        )
        return

    existing = web_registration_repo.get_by_email(connection, submission.email)
    incoming = _to_registration(submission, existing)

    if existing is not None and _content_unchanged(existing, incoming):
        result.unveraendert += 1
        return

    web_registration_repo.upsert_from_submission(connection, incoming)
    if existing is None:
        result.neu += 1
    else:
        result.aktualisiert += 1


def _content_unchanged(existing: WebRegistration, incoming: WebRegistration) -> bool:
    """Check whether a repeat submission's visible content is identical.

    Args:
        existing: Currently stored row for this email address.
        incoming: Newly fetched submission for the same email address,
            already converted to a `WebRegistration`.

    Returns:
        `True` if every compared field and the reported meter set are
        both unchanged.
    """
    if any(getattr(existing, name) != getattr(incoming, name) for name in _COMPARED_FIELDS):
        return False
    existing_meters = {(m.meter_number, m.note) for m in existing.meters}
    incoming_meters = {(m.meter_number, m.note) for m in incoming.meters}
    return existing_meters == incoming_meters


def _to_registration(
    submission: RegistrationSubmission, existing: Optional[WebRegistration]
) -> WebRegistration:
    """Convert a fetched submission into a `WebRegistration` ready to upsert.

    Always flags the result for review (`needs_review=True`,
    `reviewed_at=None`) -- the caller only actually persists it when the
    content differs from `existing` (see `_apply_submission`), which is
    exactly the case that must be (re-)flagged.

    Args:
        submission: The fetched submission.
        existing: The currently stored row for this email, if any (so the
            result carries the right `id` for an update).

    Returns:
        A `WebRegistration` ready for `web_registration_repo.upsert_from_submission`.
    """
    return WebRegistration(
        id=existing.id if existing else None,
        cloudflare_id=submission.cloudflare_id,
        firma=submission.firma,
        anrede=submission.anrede,
        vorname=submission.vorname,
        nachname=submission.nachname,
        strasse=submission.strasse,
        hausnummer=submission.hausnummer,
        plz=submission.plz,
        ort=submission.ort,
        email=submission.email,
        telefon=submission.telefon,
        bkw_kundennummer=submission.bkw_kundennummer,
        iban=submission.iban,
        message=submission.message,
        submitted_at=submission.submitted_at,
        imported_at="",
        needs_review=True,
        reviewed_at=None,
        meters=[
            WebRegistrationMeter(id=None, web_registration_id=None, meter_number=number, note=note)
            for number, note in submission.meters
        ],
    )
