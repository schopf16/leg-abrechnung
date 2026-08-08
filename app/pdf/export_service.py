"""Orchestrates generating every document for a billing run into `output/`.

The only entry point the GUI needs: given a persisted `BillingRun`, this
creates one combined billing PDF per person plus two CSV reconciliation
lists (invoices to collect, payouts to make), all collected in
`output/<year>_Q<quarter>/<LEG>/` (project brief, section 6: "Alle
Dokumente eines Laufs gesammelt exportierbar"). Nested by LEG since more
than one LEG can have a run for the same quarter.
"""

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.distribution import compute_quarter_distribution
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models.billing_run import BillingRun
from app.paths import OUTPUT_DIR
from app.pdf.csv_export import generate_invoice_list_csv, generate_payout_list_csv
from app.pdf.person_bill_pdf import generate_person_bill_pdf
from app.pdf.qr_bill_render import QrBillConfigurationError


@dataclass
class ExportResult:
    """Outcome of exporting all documents for one billing run.

    Attributes:
        output_dir: Folder all generated files were written to.
        document_paths: Paths of all generated person billing PDFs.
        invoice_list_path: Path of the generated invoice/reconciliation
            CSV (Rechnungsliste), or `None` if the run has no invoices.
        payout_list_path: Path of the generated payout CSV
            (Auszahlungsliste), or `None` if the run has no payouts to make.
        errors: Human-readable (German) messages for line items that could
            not be rendered (e.g. missing QR-IBAN configuration).
    """

    output_dir: Path
    document_paths: list[Path] = field(default_factory=list)
    invoice_list_path: Path | None = None
    payout_list_path: Path | None = None
    errors: list[str] = field(default_factory=list)


def _sanitize_filename_part(text: str) -> str:
    """Turn arbitrary text into a safe filesystem path segment.

    Args:
        text: Text to sanitize (e.g. a person's name).

    Returns:
        The text with anything but letters, digits, spaces, hyphens and
        underscores replaced by "_", trimmed and capped at 60 characters.
    """
    cleaned = re.sub(r"[^\w\säöüÄÖÜ-]", "_", text, flags=re.UNICODE).strip()
    return cleaned[:60] or "Person"


def export_billing_run_documents(
    connection: sqlite3.Connection, run: BillingRun
) -> ExportResult:
    """Generate every person's billing document and the payment list.

    Args:
        connection: Open SQLite connection.
        run: The billing run to export documents for.

    Returns:
        An `ExportResult` summarizing what was written and any per-item
        errors encountered.
    """
    items = billing_run_repo.list_items(connection, run.id)
    persons = {p.id: p for p in person_repo.list_all(connection)}
    leg = leg_repo.get(connection, run.leg_id)
    settings = settings_repo.get_settings(connection)
    # Monthly Bezug/Vergütung breakdowns are not persisted (only the final
    # netted amount is); recomputed here from the same live readings the
    # run itself was built from.
    distribution = compute_quarter_distribution(
        connection, run.leg_id, run.period_year, run.period_quarter
    )

    output_dir = (
        OUTPUT_DIR
        / f"{run.period_year}_Q{run.period_quarter}"
        / _sanitize_filename_part(leg.name)
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    result = ExportResult(output_dir=output_dir)

    for item in items:
        person = persons.get(item.person_id)
        person_result = distribution.person_results.get(item.person_id)
        if person is None or person_result is None:
            result.errors.append(
                f"Person #{item.person_id} nicht gefunden (Beleg #{item.id} übersprungen)."
            )
            continue

        filename = f"Abrechnung_{_sanitize_filename_part(person.anzeige_name)}_{item.id}.pdf"
        path = output_dir / filename

        try:
            generate_person_bill_pdf(
                run, item, person_result, person, leg, settings, path
            )
        except QrBillConfigurationError as exc:
            result.errors.append(f"{person.anzeige_name}: {exc}")
            continue

        billing_run_repo.set_item_pdf_path(connection, item.id, str(path))
        result.document_paths.append(path)

    if any(item.is_owed_to_leg for item in items):
        invoice_list_path = output_dir / f"Rechnungsliste_Q{run.period_quarter}_{run.period_year}.csv"
        generate_invoice_list_csv(run, items, persons, invoice_list_path)
        result.invoice_list_path = invoice_list_path

    if any(item.is_owed_by_leg for item in items):
        payout_list_path = output_dir / f"Auszahlungsliste_Q{run.period_quarter}_{run.period_year}.csv"
        generate_payout_list_csv(items, persons, payout_list_path)
        result.payout_list_path = payout_list_path

    return result
