"""Orchestrates generating every document for a billing run into `output/`."""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.domain.distribution import compute_quarter_distribution
from app.models import billing_run as billing_run_repo
from app.models import leg as leg_repo
from app.models import metering_point as metering_point_repo
from app.models import person as person_repo
from app.models import settings as settings_repo
from app.models import site as site_repo
from app.models.billing_run import BillingRun
from app.paths import OUTPUT_DIR
from app.pdf.bill_breakdown import MeteringPointInfo
from app.pdf.csv_export import generate_invoice_list_csv, generate_payout_list_csv
from app.pdf.qr_bill_render import qr_debtor_name_note
from app.pdf.person_bill_pdf import PAYMENT_TERM, generate_person_bill_pdf
from app.pdf.qr_bill_render import QrBillConfigurationError


@dataclass
class ExportResult:
    """Outcome of exporting all documents for one billing run."""

    output_dir: Path
    document_paths: list[Path] = field(default_factory=list)
    invoice_list_path: Path | None = None
    payout_list_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    removed_paths: list[Path] = field(default_factory=list)


def _sanitize_filename_part(text: str) -> str:
    """Turn arbitrary text into a safe filesystem path segment."""
    cleaned = re.sub(r"[^\w\säöüÄÖÜ-]", "_", text, flags=re.UNICODE).strip()
    return cleaned[:60] or "Person"


#: Filename patterns this module itself produces, and the only ones it
#: will ever delete. Anything else in the folder belongs to the user.
_GENERATED_PATTERNS = ("Abrechnung_*.pdf", "Rechnungsliste_*.csv", "Auszahlungsliste_*.csv")


def _remove_superseded_documents(output_dir: Path, keep: set[Path]) -> list[Path]:
    """Delete the documents of an earlier run for this LEG and quarter."""
    removed: list[Path] = []
    problems: list[str] = []
    for pattern in _GENERATED_PATTERNS:
        for path in sorted(output_dir.glob(pattern)):
            if not path.is_file() or path.resolve() in keep:
                continue
            try:
                path.unlink()
            except OSError as exc:
                # A superseded document the administrator happens to have
                # open in a PDF viewer cannot be deleted on Windows. The
                # export itself succeeded -- the new documents are already
                # written -- so this is reported, never raised: presenting
                # a completed export as a failure would be worse than the
                # stale file it is warning about.
                problems.append(f"„{path.name}“ konnte nicht entfernt werden ({exc.strerror or exc}).")
                continue
            removed.append(path)
    return removed, problems


def _load_metering_point_info(connection: sqlite3.Connection) -> dict[int, MeteringPointInfo]:
    """Resolve every metering point into what the billing document prints."""
    sites = {site.id: site for site in site_repo.list_all(connection)}
    info: dict[int, MeteringPointInfo] = {}
    for metering_point in metering_point_repo.list_all(connection):
        site = sites.get(metering_point.site_id)
        if site is None:
            continue
        info[metering_point.id] = MeteringPointInfo(
            designation=metering_point.designation,
            label=metering_point.label,
            site_id=site.id,
            site_address=site.full_address,
            direction=metering_point.direction,
        )
    return info


def export_billing_run_documents(connection: sqlite3.Connection, run: BillingRun) -> ExportResult:
    """Generate every person's billing document and the payment list."""
    items = billing_run_repo.list_items(connection, run.id)
    persons = {p.id: p for p in person_repo.list_all(connection)}
    leg = leg_repo.get(connection, run.leg_id)
    settings = settings_repo.get_settings(connection)
    # Monthly consumption/Vergütung breakdowns are not persisted (only the final
    # netted amount is); recomputed here from the same live readings the
    # run itself was built from.
    distribution = compute_quarter_distribution(connection, run.leg_id, run.period_year, run.period_quarter)
    metering_point_info = _load_metering_point_info(connection)

    output_dir = OUTPUT_DIR / f"{run.period_year}_Q{run.period_quarter}" / _sanitize_filename_part(leg.name)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = ExportResult(output_dir=output_dir)

    for item in items:
        person = persons.get(item.person_id)
        person_result = distribution.person_results.get(item.person_id)
        if person is None or person_result is None:
            result.errors.append(f"Person #{item.person_id} nicht gefunden (Beleg #{item.id} übersprungen).")
            continue

        filename = f"Abrechnung_{_sanitize_filename_part(person.display_name)}_{item.id}.pdf"
        path = output_dir / filename

        # Freeze the due date on first export only -- a re-export (e.g. to
        # fix a typo in the LEG address) must keep printing the same date
        # already communicated to the person and already relied upon by
        # app.domain.dunning, never push it back out by another
        # PAYMENT_TERM. `item` is updated in-memory so the PDF below
        # prints exactly the value now persisted, whichever branch ran.
        if item.due_date is None:
            item.due_date = (date.today() + PAYMENT_TERM).isoformat()
            billing_run_repo.set_item_due_date(connection, item.id, item.due_date)

        try:
            generate_person_bill_pdf(
                run,
                item,
                person_result,
                person,
                leg,
                settings,
                path,
                metering_point_info=metering_point_info,
            )
        except QrBillConfigurationError as exc:
            result.errors.append(f"{person.display_name}: {exc}")
            continue

        billing_run_repo.set_item_pdf_path(connection, item.id, str(path))
        result.document_paths.append(path)

        # Reported after the document was written, because it is a remark
        # about a document that exists, not a failure to produce one.
        shortened = qr_debtor_name_note(person)
        if shortened:
            result.errors.append(shortened)

    if any(item.is_owed_to_leg for item in items):
        invoice_list_path = output_dir / f"Rechnungsliste_Q{run.period_quarter}_{run.period_year}.csv"
        generate_invoice_list_csv(run, items, persons, invoice_list_path)
        result.invoice_list_path = invoice_list_path

    if any(item.is_owed_by_leg for item in items):
        payout_list_path = output_dir / f"Auszahlungsliste_Q{run.period_quarter}_{run.period_year}.csv"
        generate_payout_list_csv(items, persons, payout_list_path)
        result.payout_list_path = payout_list_path

    written = {path.resolve() for path in result.document_paths}
    for path in (result.invoice_list_path, result.payout_list_path):
        if path is not None:
            written.add(path.resolve())
    result.removed_paths, cleanup_problems = _remove_superseded_documents(output_dir, written)
    result.errors.extend(cleanup_problems)

    return result
