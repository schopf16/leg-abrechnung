"""Synchronizes the leg-ittigen.ch registration inbox into `web_registration`."""

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
    "company",
    "salutation",
    "first_name",
    "last_name",
    "street",
    "house_number",
    "postal_code",
    "city",
    "phone",
    "bkw_customer_number",
    "iban",
    "message",
)


@dataclass
class RegistrationSyncResult:
    """Outcome of one `sync_registrations` run, for display in the GUI."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    warnings: list[str] = field(default_factory=list)


def sync_registrations(connection: sqlite3.Connection, token: str) -> RegistrationSyncResult:
    """Fetch and apply every new/changed registration since the last sync."""
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
    """Insert, update or ignore one submission, updating `result` in place."""
    if not submission.email:
        who = f"{submission.first_name} {submission.last_name}".strip() or submission.company or "?"
        result.warnings.append(
            f"Registrierung von „{who}“ (Cloudflare-ID {submission.cloudflare_id}) "
            "übersprungen: keine E-Mail-Adresse angegeben."
        )
        return

    existing = web_registration_repo.get_by_email(connection, submission.email)
    incoming = _to_registration(submission, existing)

    if existing is not None and _content_unchanged(existing, incoming):
        result.unchanged += 1
        return

    web_registration_repo.upsert_from_submission(connection, incoming)
    if existing is None:
        result.created += 1
    else:
        result.updated += 1


def _content_unchanged(existing: WebRegistration, incoming: WebRegistration) -> bool:
    """Check whether a repeat submission's visible content is identical."""
    if any(getattr(existing, name) != getattr(incoming, name) for name in _COMPARED_FIELDS):
        return False
    existing_meters = {(m.meter_number, m.note) for m in existing.meters}
    incoming_meters = {(m.meter_number, m.note) for m in incoming.meters}
    return existing_meters == incoming_meters


def _to_registration(
    submission: RegistrationSubmission, existing: Optional[WebRegistration]
) -> WebRegistration:
    """Convert a fetched submission into a `WebRegistration` ready to upsert."""
    return WebRegistration(
        id=existing.id if existing else None,
        cloudflare_id=submission.cloudflare_id,
        company=submission.company,
        salutation=submission.salutation,
        first_name=submission.first_name,
        last_name=submission.last_name,
        street=submission.street,
        house_number=submission.house_number,
        postal_code=submission.postal_code,
        city=submission.city,
        email=submission.email,
        phone=submission.phone,
        bkw_customer_number=submission.bkw_customer_number,
        iban=submission.iban,
        message=submission.message,
        submitted_at=submission.submitted_at,
        imported_at="",
        meters=[
            WebRegistrationMeter(id=None, web_registration_id=None, meter_number=number, note=note)
            for number, note in submission.meters
        ],
    )
