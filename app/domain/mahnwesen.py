"""Mahnwesen: detects who needs a Mahnung and sends it.

Deliberately follows the LEG's own 2-stage Reglement, not the generic
3-stage/fee model common in accounting software (see the Debitoren plan
for the research behind this decision):

    Stufe 0 -> 1: the invoice's own due date (`BillingRunItem.faellig_am`)
        has passed. The 1. Mahnung grants a new deadline
        (`LegSettings.mahnung_neue_frist_tage` days) and warns that
        missing it leads to a membership-termination review -- the claim
        itself is never affected by that review, see
        `app.models.person_offboarding`.
    Stufe 1 -> 2: the new deadline granted by the 1. Mahnung has also
        passed. The 2. Mahnung is the trigger point for that review
        (never automatic -- a human always confirms the actual exclusion,
        see `app.gui.pages.mahnwesen`).
    No fee at any stage -- not contractually provided for.

Multiple overdue items for the same person are consolidated into a single
letter/email (mirroring how Banana Buchhaltung's Mahnlauf works), with the
*highest* stage among them driving the letter's tone -- a documented
simplification given how rarely a small LEG's member would have more than
one overdue quarter at once.

Eligibility is gated by the person's overall running Saldo (see
`app.models.account_entry`), not just this item's own paid/unpaid state:
a payment matched elsewhere (e.g. a generic correction, or a prepayment
credit) can already cover this item even if no payment was matched to it
specifically -- checking the aggregate Saldo avoids sending a Mahnung to
someone who is, overall, already square.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import GraphConfig
from app.emailing import graph_client
from app.emailing.templates import person_placeholder_values, render_template
from app.models import account_entry as account_entry_repo
from app.models import billing_run as billing_run_repo
from app.models import mahnung_log as mahnung_log_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRunItem
from app.models.person import Person
from app.paths import OUTPUT_DIR
from app.pdf.mahnung_pdf import generate_mahnung_pdf

#: Mahnung-only placeholders, not derived from `Person` -- exposed here
#: (like `app.emailing.bulk_send.INVOICE_EXTRA_PLACEHOLDERS`) so the GUI
#: can validate/hint against the complete set of valid placeholders.
MAHNUNG_EXTRA_PLACEHOLDERS = ("betrag", "neue_frist")


@dataclass
class MahnKandidat:
    """One person currently due for a Mahnung, with every open item that
    should be included in the (single, consolidated) letter.

    Attributes:
        person: The person to send the Mahnung to.
        items: Every `BillingRunItem` newly reaching a Mahnstufe, oldest
            first.
        item_target_stufen: Each included item's own individually-computed
            target stage, keyed by `item.id` -- kept separate from `items`
            (a plain list, for display/PDF code that just wants the
            objects) so `send_mahnung` can advance every item to *its own*
            stage rather than force-advancing all of them to the letter's
            highest one (see that function's docstring for why this
            distinction matters).
        stufe: The stage that drives the *letter's* tone/template -- the
            highest stage among `items` (see module docstring for why the
            highest, not each separately, for the letter's wording).
        total_open_rappen: The person's current overall Saldo (positive =
            owed to the LEG), shown as the amount due in the letter --
            not simply the sum of `items`' own amounts, since that could
            understate or overstate what is actually still owed once
            payments/credits elsewhere on the account are accounted for.
    """

    person: Person
    items: list[BillingRunItem]
    item_target_stufen: dict[int, int]
    stufe: int
    total_open_rappen: int


def _next_stufe(item: BillingRunItem, today: date, neue_frist_tage: int) -> Optional[int]:
    """Determine whether `item` newly reaches the next Mahnstufe today.

    Args:
        item: The billing run item to check.
        today: Reference date.
        neue_frist_tage: `LegSettings.mahnung_neue_frist_tage`, used only
            as a fallback for the 1->2 check -- see the `item.mahnstufe
            == 1` branch below for why the item's own frozen value is
            preferred.

    Returns:
        `1` or `2` if `item` is newly eligible for that stage, else `None`
        (not yet due, or already at the final stage `2`).
    """
    if item.mahnstufe == 0:
        if not item.faellig_am:
            return None
        return 1 if today > date.fromisoformat(item.faellig_am) else None
    if item.mahnstufe == 1:
        if not item.letzte_mahnung_am:
            return None
        # Use the deadline actually granted (and printed via {neue_frist})
        # when the 1. Mahnung was sent, frozen on the item itself -- never
        # the live setting, which may have changed since. Only items sent
        # before this freeze existed (migration 30) fall back to the
        # current setting as a best-effort approximation.
        frist_tage = item.mahnung_frist_tage if item.mahnung_frist_tage is not None else neue_frist_tage
        letzte = datetime.fromisoformat(item.letzte_mahnung_am).date()
        return 2 if today > letzte + timedelta(days=frist_tage) else None
    return None


def list_faellige_mahnungen(connection) -> list[MahnKandidat]:
    """Find every person currently due for a (possibly consolidated) Mahnung.

    Args:
        connection: Open SQLite connection.

    Returns:
        One `MahnKandidat` per person needing a Mahnung, in no particular
        order.
    """
    settings = settings_repo.get_settings(connection)
    today = date.today()
    saldi = account_entry_repo.get_all_saldi(connection)

    items_by_person: dict[int, list[tuple[BillingRunItem, int]]] = {}
    for run in billing_run_repo.list_runs(connection):
        for item in billing_run_repo.list_items(connection, run.id):
            if not item.is_owed_to_leg:
                continue
            target_stufe = _next_stufe(item, today, settings.mahnung_neue_frist_tage)
            if target_stufe is not None:
                items_by_person.setdefault(item.person_id, []).append((item, target_stufe))

    candidates: list[MahnKandidat] = []
    for person_id, entries in items_by_person.items():
        person = person_repo.get(connection, person_id)
        if person is None:
            continue
        saldo = saldi.get(person_id, 0)
        if saldo < settings.mahnung_bagatellgrenze_rappen:
            # Already settled overall, or below the configured minimum
            # amount worth chasing.
            continue
        candidates.append(
            MahnKandidat(
                person=person,
                items=[item for item, _ in entries],
                item_target_stufen={item.id: target_stufe for item, target_stufe in entries},
                stufe=max(target_stufe for _, target_stufe in entries),
                total_open_rappen=saldo,
            )
        )
    return candidates


def _sanitize_filename_part(text: str) -> str:
    """Turn arbitrary text into a safe filesystem path segment.

    Mirrors `app.pdf.export_service._sanitize_filename_part` (kept as a
    small local copy rather than importing a private helper across
    modules).

    Args:
        text: Text to sanitize.

    Returns:
        A filesystem-safe version, capped at 60 characters.
    """
    cleaned = re.sub(r"[^\w\säöüÄÖÜ-]", "_", text, flags=re.UNICODE).strip()
    return cleaned[:60] or "Person"


def render_mahnung_text(settings, candidate: MahnKandidat) -> tuple[str, str]:
    """Render a Mahnung's subject/body from the stage-appropriate template.

    The single source of truth for this rendering -- both `send_mahnung`
    (below) and the GUI's pre-send preview (see `app.gui.pages.mahnwesen`)
    call this, so the preview Michael reviews can never silently drift
    from what actually gets sent.

    Args:
        settings: Current `LegSettings` (provides the two Mahnung
            templates and `mahnung_neue_frist_tage`).
        candidate: The `MahnKandidat` to render for.

    Returns:
        `(subject, body)`, placeholders already substituted.
    """
    if candidate.stufe == 1:
        subject_template = settings.mahnung1_email_betreff
        body_template = settings.mahnung1_email_text
    else:
        subject_template = settings.mahnung2_email_betreff
        body_template = settings.mahnung2_email_text

    neue_frist = (date.today() + timedelta(days=settings.mahnung_neue_frist_tage)).strftime("%d.%m.%Y")
    values = {
        **person_placeholder_values(candidate.person),
        "betrag": f"{candidate.total_open_rappen / 100:.2f}",
        "neue_frist": neue_frist,
    }
    return render_template(subject_template, values), render_template(body_template, values)


async def send_mahnung(connection, config: Optional[GraphConfig], candidate: MahnKandidat) -> Path:
    """Render, send (or leave for manual printing) and log one Mahnung.

    Always generates the PDF (needed either as an email attachment or for
    Michael to print/send by post -- mirrors `Person.paper_invoice`'s
    existing invoice-email behavior). Always advances every included item
    to *its own* target stage (not necessarily `candidate.stufe`, which is
    only the letter's tone -- see `MahnKandidat.item_target_stufen`) and
    records a `mahnung_log` entry, regardless of the send channel.

    Args:
        connection: Open SQLite connection.
        config: Graph API credentials, or `None` if the person has no
            email address / prefers paper -- must not be `None` if an
            email will actually be attempted.
        candidate: The `MahnKandidat` to process.

    Returns:
        Path of the generated PDF.

    Raises:
        app.emailing.graph_client.GraphAuthError: If credentials are invalid.
        app.emailing.graph_client.GraphApiError: For any other send failure.
    """
    settings = settings_repo.get_settings(connection)
    person = candidate.person
    subject, body = render_mahnung_text(settings, candidate)

    output_dir = OUTPUT_DIR / "Mahnungen" / date.today().isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / (
        f"Mahnung{candidate.stufe}_{_sanitize_filename_part(person.display_name)}_{person.id}.pdf"
    )
    generate_mahnung_pdf(connection, candidate, body, settings, pdf_path)

    if person.contact_email.strip() and not person.paper_invoice:
        if config is None:
            raise ValueError("Graph-Konfiguration fehlt, E-Mail-Versand nicht möglich.")
        access_token = await graph_client.get_access_token(config)
        await graph_client.send_email(
            config,
            access_token,
            to_address=person.contact_email,
            to_name=person.display_name,
            subject=subject,
            body=body,
            attachment_path=pdf_path,
            attachment_filename=pdf_path.name,
        )

    now = datetime.now(timezone.utc).isoformat()
    for item in candidate.items:
        # Each item advances to its own target stage, not the letter's
        # (possibly higher) overall stage -- see MahnKandidat.item_target_stufen.
        target_stufe = candidate.item_target_stufen[item.id]
        # Freeze the deadline granted by *this* send only when it's a
        # 1. Mahnung -- there is no further deadline to freeze at stage 2.
        frist_tage = settings.mahnung_neue_frist_tage if target_stufe == 1 else None
        billing_run_repo.set_item_mahnstufe(
            connection, item.id, target_stufe, now, mahnung_frist_tage=frist_tage
        )
    mahnung_log_repo.create(
        connection,
        person_id=person.id,
        stufe=candidate.stufe,
        betrag_rappen=candidate.total_open_rappen,
        billing_run_item_ids=[item.id for item in candidate.items],
    )
    return pdf_path
