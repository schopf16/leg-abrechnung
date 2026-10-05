"""dunning: detects who needs a dunning notice and sends it."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import GraphConfig
from app.emailing import graph_client
from app.domain import message_templates
from app.emailing.templates import person_placeholder_values, render_template
from app.models.message_template import OCCASION_DUNNING1, OCCASION_DUNNING2
from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import dunning_log as dunning_log_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRunItem
from app.models.person import Person
from app.paths import OUTPUT_DIR
from app.pdf.dunning_pdf import generate_dunning_pdf

#: dunning notice-only placeholders, not derived from `Person` -- exposed here
#: (like `app.emailing.bulk_send.INVOICE_EXTRA_PLACEHOLDERS`) so the GUI
#: can validate/hint against the complete set of valid placeholders.
DUNNING_EXTRA_PLACEHOLDERS = ("betrag", "neue_frist")


@dataclass
class DunningCandidate:
    """One person currently due for a dunning notice, with every open item that should be included in..."""

    person: Person
    items: list[BillingRunItem]
    item_target_levels: dict[int, int]
    level: int
    total_open_rappen: int


def _next_level(item: BillingRunItem, today: date, new_deadline_days: int) -> Optional[int]:
    """Determine whether `item` newly reaches the next dunning level today."""
    if item.dunning_level == 0:
        if not item.due_date:
            return None
        return 1 if today > date.fromisoformat(item.due_date) else None
    if item.dunning_level == 1:
        if not item.last_dunning_at:
            return None
        # Use the deadline actually granted (and printed via {neue_frist})
        # when the 1. dunning notice was sent, frozen on the item itself -- never
        # the live setting, which may have changed since. Only items sent
        # before this freeze existed (migration 30) fall back to the
        # current setting as a best-effort approximation.
        deadline_days = (
            item.dunning_deadline_days if item.dunning_deadline_days is not None else new_deadline_days
        )
        letzte = datetime.fromisoformat(item.last_dunning_at).date()
        return 2 if today > letzte + timedelta(days=deadline_days) else None
    return None


def list_due_dunnings(connection) -> list[DunningCandidate]:
    """Find every person currently due for a (possibly consolidated) dunning notice."""
    settings = settings_repo.get_settings(connection)
    today = date.today()
    saldi = account_entry_repo.get_all_saldi(connection)

    items_by_person: dict[int, list[tuple[BillingRunItem, int]]] = {}
    for run in billing_run_repo.list_runs(connection):
        for item in billing_run_repo.list_items(connection, run.id):
            if not item.is_owed_to_leg:
                continue
            target_level = _next_level(item, today, settings.dunning_new_deadline_days)
            if target_level is not None:
                items_by_person.setdefault(item.person_id, []).append((item, target_level))

    candidates: list[DunningCandidate] = []
    for person_id, entries in items_by_person.items():
        person = person_repo.get(connection, person_id)
        if person is None:
            continue
        balance = saldi.get(person_id, 0)
        if balance < settings.dunning_minimum_rappen:
            # Already settled overall, or below the configured minimum
            # amount worth chasing.
            continue
        candidates.append(
            DunningCandidate(
                person=person,
                items=[item for item, _ in entries],
                item_target_levels={item.id: target_level for item, target_level in entries},
                level=max(target_level for _, target_level in entries),
                total_open_rappen=balance,
            )
        )
    return candidates


def _sanitize_filename_part(text: str) -> str:
    """Turn arbitrary text into a safe filesystem path segment."""
    cleaned = re.sub(r"[^\w\säöüÄÖÜ-]", "_", text, flags=re.UNICODE).strip()
    return cleaned[:60] or "Person"


def render_dunning_text(connection, settings, candidate: DunningCandidate) -> tuple[str, str]:
    """Render a dunning notice's subject/body from the stage-appropriate template."""
    occasion = OCCASION_DUNNING1 if candidate.level == 1 else OCCASION_DUNNING2
    subject_template, body_template = message_templates.text_for(connection, occasion)

    new_deadline = (date.today() + timedelta(days=settings.dunning_new_deadline_days)).strftime("%d.%m.%Y")
    values = {
        **person_placeholder_values(candidate.person),
        "betrag": f"{candidate.total_open_rappen / 100:.2f}",
        "neue_frist": new_deadline,
    }
    return render_template(subject_template, values), render_template(body_template, values)


async def send_dunning(connection, config: Optional[GraphConfig], candidate: DunningCandidate) -> Path:
    """Render, send (or leave for manual printing) and log one dunning notice."""
    settings = settings_repo.get_settings(connection)
    person = candidate.person
    subject, body = render_dunning_text(connection, settings, candidate)

    output_dir = OUTPUT_DIR / "Mahnungen" / date.today().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / (
        f"Mahnung{candidate.level}_{_sanitize_filename_part(person.display_name)}_{person.id}.pdf"
    )
    generate_dunning_pdf(connection, candidate, body, settings, pdf_path)

    if person.contact_emails and not person.paper_invoice:
        if config is None:
            raise ValueError("Graph-Konfiguration fehlt, E-Mail-Versand nicht möglich.")
        access_token = await graph_client.get_access_token(config)
        await graph_client.send_email(
            config,
            access_token,
            to_addresses=person.contact_emails,
            to_name=person.display_name,
            subject=subject,
            body=body,
            attachments=[graph_client.Attachment(path=pdf_path, filename=pdf_path.name)],
        )

    now = datetime.now(timezone.utc).isoformat()
    for item in candidate.items:
        # Each item advances to its own target stage, not the letter's
        # (possibly higher) overall stage -- see DunningCandidate.item_target_levels.
        target_level = candidate.item_target_levels[item.id]
        # Freeze the deadline granted by *this* send only when it's a
        # 1. dunning notice -- there is no further deadline to freeze at stage 2.
        deadline_days = settings.dunning_new_deadline_days if target_level == 1 else None
        billing_run_repo.set_item_dunning_level(
            connection, item.id, target_level, now, dunning_deadline_days=deadline_days
        )
    dunning_log_repo.create(
        connection,
        person_id=person.id,
        level=candidate.level,
        amount_rappen=candidate.total_open_rappen,
        billing_run_item_ids=[item.id for item in candidate.items],
    )
    return pdf_path
