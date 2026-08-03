"""Orchestrates generating every PDF for a billing run into `output/`.

The only entry point the GUI needs: given a persisted `BillingRun`, this
creates one invoice or credit note PDF per line item plus a payment list,
all collected in `output/<year>_Q<quarter>/` (project brief, section 6:
"Alle Dokumente eines Laufs gesammelt exportierbar").
"""

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.models import billing_run as billing_run_repo
from app.models import participant as participant_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRun
from app.paths import OUTPUT_DIR
from app.pdf.credit_pdf import generate_credit_pdf
from app.pdf.invoice_pdf import generate_invoice_pdf
from app.pdf.payment_list import generate_payment_list_pdf
from app.pdf.qr_bill_render import QrBillConfigurationError


@dataclass
class ExportResult:
    """Outcome of exporting all documents for one billing run.

    Attributes:
        output_dir: Folder all generated files were written to.
        document_paths: Paths of all generated invoice/credit note PDFs.
        payment_list_path: Path of the generated payment list PDF, or
            `None` if the run has no credit notes.
        errors: Human-readable (German) messages for line items that could
            not be rendered (e.g. missing QR-IBAN configuration).
    """

    output_dir: Path
    document_paths: list[Path] = field(default_factory=list)
    payment_list_path: Path | None = None
    errors: list[str] = field(default_factory=list)


def _sanitize_filename_part(text: str) -> str:
    """Turn arbitrary text into a safe filesystem path segment.

    Args:
        text: Text to sanitize (e.g. a participant name).

    Returns:
        The text with anything but letters, digits, spaces, hyphens and
        underscores replaced by "_", trimmed and capped at 60 characters.
    """
    cleaned = re.sub(r"[^\w\säöüÄÖÜ-]", "_", text, flags=re.UNICODE).strip()
    return cleaned[:60] or "Teilnehmer"


def export_billing_run_documents(
    connection: sqlite3.Connection, run: BillingRun
) -> ExportResult:
    """Generate every invoice, credit note and the payment list for a run.

    Args:
        connection: Open SQLite connection.
        run: The billing run to export documents for.

    Returns:
        An `ExportResult` summarizing what was written and any per-item
        errors encountered.
    """
    items = billing_run_repo.list_items(connection, run.id)
    participants = {p.id: p for p in participant_repo.list_all(connection)}
    settings = settings_repo.get_settings(connection)

    output_dir = OUTPUT_DIR / f"{run.period_year}_Q{run.period_quarter}"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = ExportResult(output_dir=output_dir)

    for item in items:
        participant = participants.get(item.participant_id)
        if participant is None:
            result.errors.append(
                f"Teilnehmer #{item.participant_id} nicht gefunden (Beleg #{item.id} übersprungen)."
            )
            continue

        kind_label = "Rechnung" if item.kind == "rechnung" else "Gutschrift"
        filename = f"{kind_label}_{_sanitize_filename_part(participant.name)}_{item.id}.pdf"
        path = output_dir / filename

        try:
            if item.kind == "rechnung":
                generate_invoice_pdf(run, item, participant, settings, path)
            else:
                generate_credit_pdf(run, item, participant, settings, path)
        except QrBillConfigurationError as exc:
            result.errors.append(f"{participant.name}: {exc}")
            continue

        billing_run_repo.set_item_pdf_path(connection, item.id, str(path))
        result.document_paths.append(path)

    if any(item.kind == "gutschrift" for item in items):
        payment_list_path = output_dir / f"Zahlliste_Q{run.period_quarter}_{run.period_year}.pdf"
        generate_payment_list_pdf(run, items, participants, payment_list_path)
        result.payment_list_path = payment_list_path

    return result
