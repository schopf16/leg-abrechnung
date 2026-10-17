"""Data export: every LEG table as CSV inside one ZIP, described by `manifest.json`.

The declarations in `TABLES` are the contract with the importing system (leg-platform): the manifest
is generated from them, so file, columns and documentation cannot drift apart. Changing a column
means raising `FORMAT_VERSION`.
"""

import csv
import hashlib
import json
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

FORMAT_NAME = "leg-export"
FORMAT_VERSION = "1.0"

#: Rules for every CSV file. Echoed verbatim into the manifest.
CONVENTIONS = {
    "encoding": "UTF-8 without BOM",
    "delimiter": ",",
    "quote": '"',
    "line_terminator": "\\n",
    "header": "The first row of every CSV file holds the column names.",
    "null": "A nullable column holds NULL as an empty field. A non-nullable text column holds '' as an empty field.",
    "integer": "Decimal digits, optional leading minus.",
    "real": "Decimal point, no thousands separator, possibly exponent notation (1e-05).",
    "boolean": "0 or 1.",
    "date": "YYYY-MM-DD.",
    "datetime": "ISO 8601 as stored by the app, usually with UTC offset (2026-01-31T12:00:00+00:00).",
    "money": "Columns ending in _rappen are integer Rappen (CHF 12.34 = 1234). Never a fraction.",
    "energy": "Columns ending in _kwh are kWh as real numbers at full precision.",
    "ids": "id columns are the exporting app's primary keys and are kept so that foreign keys resolve. "
    "They are unique within their file only.",
    "periods": "valid_from is inclusive. valid_to is inclusive and empty while the period is still open.",
}

#: What the export deliberately leaves out, with the reason. Echoed into the manifest.
EXCLUDED = {
    "web_registrations": "Inbox of the website form; not part of the community's state.",
    "message_templates, signatures, person_message_log, email_broadcast_log": "Correspondence, not LEG data.",
    "leg_document": "Binary template of the membership contract.",
    "billing_run_items.pdf_path": "Local file path of the generated PDF.",
    "leg_settings (mail texts, backup path, UI thresholds)": "Application configuration.",
}


@dataclass(frozen=True)
class Column:
    """One CSV column."""

    name: str
    type: str  # integer | real | text | boolean | date | datetime
    nullable: bool = False
    references: Optional[str] = None  # "table.column" in this export
    values: Optional[tuple[str, ...]] = None  # allowed values of an enumerated text column
    deferred: bool = False  # references a table loaded later; load as NULL, then update
    doc: str = ""


@dataclass(frozen=True)
class TableSpec:
    """One CSV file."""

    name: str
    source_table: str
    description: str
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]
    order_by: str = "id"
    rows: Optional[Callable[[sqlite3.Connection], Iterator[tuple]]] = field(default=None, compare=False)

    @property
    def filename(self) -> str:
        """Name of the file inside the ZIP."""
        return f"{self.name}.csv"


def _c(name: str, type_: str, **kw) -> Column:
    return Column(name, type_, **kw)


def _id() -> Column:
    return Column("id", "integer", doc="Primary key.")


_CREATED = Column("created_at", "datetime")
_DIRECTION = ("consumption", "feed_in")


def _dunning_notice_rows(connection: sqlite3.Connection) -> Iterator[tuple]:
    for row in connection.execute(
        "SELECT id, sent_at, person_id, level, amount_rappen FROM dunning_log ORDER BY id"
    ):
        yield tuple(row)


def _dunning_item_rows(connection: sqlite3.Connection) -> Iterator[tuple]:
    """Unfold the JSON id list of a dunning notice; an id whose item is gone is dropped."""
    existing = {r[0] for r in connection.execute("SELECT id FROM billing_run_items")}
    for notice_id, ids in connection.execute("SELECT id, billing_run_item_ids FROM dunning_log ORDER BY id"):
        for item_id in json.loads(ids):
            if item_id in existing:
                yield (notice_id, item_id)


#: In load order: a table only references tables above it (except `deferred` columns).
TABLES: tuple[TableSpec, ...] = (
    TableSpec(
        "substation_areas",
        "substation_area",
        "Trafokreise (physical BKW transformer areas).",
        ("id",),
        (
            _id(),
            _c("name", "text", doc="Unique."),
            _c("bkw_designation", "text", doc="BKW's own designation; '' if unknown."),
            _c("note", "text"),
            _CREATED,
        ),
    ),
    TableSpec(
        "sites",
        "site",
        "Standorte: connection sites with an address.",
        ("id",),
        (
            _id(),
            _c("street", "text"),
            _c("house_number", "text"),
            _c("postal_code", "text"),
            _c("municipality", "text", doc="Holds the postal locality (Ortschaft), despite the name."),
            _c("address_detail", "text", doc="Free text, e.g. building part."),
            _c("substation_area_id", "integer", nullable=True, references="substation_areas.id"),
            _c("dwelling_count", "integer", nullable=True, doc="Wohneinheiten at the site."),
            _CREATED,
        ),
    ),
    TableSpec(
        "legs",
        "leg",
        "LEGs: the billing groups.",
        ("id",),
        (
            _id(),
            _c("name", "text", doc="Unique."),
            _c("note", "text"),
            _c(
                "production_capacity_percent",
                "real",
                nullable=True,
                doc="Installed production as % of the participants' total connection capacity "
                "(statutory minimum 5).",
            ),
            _c("production_capacity_recorded_at", "date", nullable=True),
            _CREATED,
        ),
    ),
    TableSpec(
        "metering_points",
        "metering_point",
        "Messpunkte: one meter, one direction, at one site, optionally in one LEG.",
        ("id",),
        (
            _id(),
            _c("designation", "text", doc="Messpunktbezeichnung as BKW names it. Unique; the readings' key."),
            _c("direction", "text", values=_DIRECTION),
            _c("label", "text", doc="Free label, e.g. 'Whg. 3. OG'."),
            _c("site_id", "integer", references="sites.id"),
            _c("leg_id", "integer", nullable=True, references="legs.id"),
            _c("pv_capacity_kwp", "real", nullable=True),
            _c("battery_capacity_kwh", "real", nullable=True),
            _c("wallbox_capacity_kw", "real", nullable=True),
            _CREATED,
        ),
    ),
    TableSpec(
        "persons",
        "person",
        "Personen: one contract customer each. A couple is one row with two names. "
        "Always one invoice, one balance, one payment per row.",
        ("id",),
        (
            _id(),
            _c("customer_number", "integer", nullable=True, doc="The LEG's customer number; unique if set."),
            _c("bkw_customer_number", "integer", nullable=True),
            _c("company", "text", doc="Company name; '' for a private person."),
            _c("salutation", "text", doc="'' is a valid state."),
            _c("first_name", "text"),
            _c("last_name", "text"),
            _c("second_salutation", "text", doc="Second person of a couple."),
            _c("second_first_name", "text"),
            _c("second_last_name", "text"),
            _c("contact_email", "text"),
            _c("second_contact_email", "text"),
            _c("contact_phone", "text"),
            _c("billing_street", "text"),
            _c("billing_house_number", "text"),
            _c("billing_postal_code", "text"),
            _c("billing_city", "text"),
            _c("billing_country", "text", doc="ISO 3166 alpha-2, 'CH' by default."),
            _c("iban", "text", doc="For payouts to the person; '' if unknown."),
            _c("paper_invoice", "boolean", doc="1 = invoice goes by post."),
            _c("active", "boolean", doc="0 = deactivated (left the community)."),
            _c("deactivated_at", "date", nullable=True),
            _c("note", "text", doc="Internal remark of the administrator."),
            _CREATED,
        ),
    ),
    TableSpec(
        "assignments",
        "assignment",
        "Zuordnungen: which person holds which metering point in which period.",
        ("id",),
        (
            _id(),
            _c("person_id", "integer", references="persons.id"),
            _c("metering_point_id", "integer", references="metering_points.id"),
            _c("valid_from", "date"),
            _c("valid_to", "date", nullable=True),
            _CREATED,
        ),
    ),
    TableSpec(
        "cooperative_memberships",
        "cooperative_membership",
        "Genossenschaft: one row per period of a person's membership. Gaps are legitimate.",
        ("id",),
        (
            _id(),
            _c("person_id", "integer", references="persons.id"),
            _c("shares", "integer", doc="Anteile; 0 is allowed."),
            _c("valid_from", "date"),
            _c("valid_to", "date", nullable=True),
            _CREATED,
        ),
    ),
    TableSpec(
        "person_onboardings",
        "person_onboarding",
        "Aufnahme: progress of a person's admission. At most one row per person.",
        ("id",),
        (
            _id(),
            _c("person_id", "integer", references="persons.id", doc="Unique."),
            _c("leg_id", "integer", nullable=True, references="legs.id"),
            _c("registered_at", "date", nullable=True),
            _c("leg_assigned_at", "date", nullable=True),
            _c("contract_signed_at", "date", nullable=True),
            _c("bkw_registered_at", "date", nullable=True),
            _c("bkw_confirmed_at", "date", nullable=True),
            _CREATED,
        ),
    ),
    TableSpec(
        "person_offboardings",
        "person_offboarding",
        "Austritt: progress of a person's exit.",
        ("id",),
        (
            _id(),
            _c("person_id", "integer", references="persons.id"),
            _c("reason", "text", values=("payment_default", "voluntary", "other")),
            _c("decided_at", "date", nullable=True),
            _c("metering_point_exit_at", "date", nullable=True),
            _c("bkw_informed_at", "date", nullable=True),
            _c("person_confirmed_at", "date", nullable=True),
            _CREATED,
        ),
    ),
    TableSpec(
        "readings",
        "readings",
        "Messwerte: kWh per metering point and 15-minute interval. By far the largest file.",
        ("metering_point_id", "timestamp", "direction"),
        (
            _c("metering_point_id", "integer", references="metering_points.id"),
            _c(
                "timestamp",
                "datetime",
                doc="Start of the 15-minute interval, as delivered by BKW: usually local time without UTC offset "
                "(2025-07-01T00:15:00), sometimes with one. Compare as text only within one metering point.",
            ),
            _c("direction", "text", values=_DIRECTION),
            _c("kwh", "real"),
            _c("source", "text", doc="Where the value came from (ebix, csv, demo)."),
        ),
        order_by="metering_point_id, timestamp, direction",
    ),
    TableSpec(
        "billing_runs",
        "billing_runs",
        "Rechnungsläufe: one per LEG and quarter.",
        ("id",),
        (
            _id(),
            _c("leg_id", "integer", references="legs.id"),
            _c("period_year", "integer"),
            _c("period_quarter", "integer", doc="1..4. (leg_id, period_year, period_quarter) is unique."),
            _c("price_rp_per_kwh", "real", doc="Rappen per kWh, frozen at billing time."),
            _c("status", "text"),
            _c("notes", "text"),
            _CREATED,
        ),
    ),
    TableSpec(
        "billing_run_items",
        "billing_run_items",
        "Rechnungspositionen: one document per person and run. The rates are frozen at billing time.",
        ("id",),
        (
            _id(),
            _c("billing_run_id", "integer", references="billing_runs.id"),
            _c("person_id", "integer", references="persons.id"),
            _c("consumed_kwh", "real"),
            _c("produced_kwh", "real"),
            _c("price_rp_per_kwh", "real", doc="Rappen per kWh."),
            _c("admin_fee_consumption_rp_per_kwh", "real"),
            _c("admin_fee_feed_in_rp_per_kwh", "real"),
            _c("admin_fee_consumption_rappen", "integer"),
            _c("admin_fee_feed_in_rappen", "integer"),
            _c("paper_invoice_rappen", "integer"),
            _c(
                "net_amount_rappen",
                "integer",
                doc="Positive: the person owes the LEG. Negative: the LEG owes the person.",
            ),
            _c("due_date", "date", nullable=True, doc="Printed on the invoice."),
            _c("email_sent_at", "datetime", nullable=True),
            _c("dunning_level", "integer", doc="0 = none, 1 = reminder, 2 = notice of termination."),
            _c("last_dunning_at", "datetime", nullable=True),
            _c("dunning_deadline_days", "integer", nullable=True),
            _CREATED,
        ),
    ),
    TableSpec(
        "bank_import_batches",
        "bank_import_batches",
        "Imported bank statement files (camt.053/054).",
        ("id",),
        (
            _id(),
            _c("filename", "text"),
            _c("imported_at", "datetime"),
            _c("account_iban", "text"),
            _c("statement_from", "date", nullable=True),
            _c("statement_to", "date", nullable=True),
            _c("entry_count", "integer"),
        ),
    ),
    TableSpec(
        "bank_transactions",
        "bank_transactions",
        "Bank transactions as imported.",
        ("id",),
        (
            _id(),
            _c("bank_import_batch_id", "integer", references="bank_import_batches.id"),
            _c("bank_reference", "text"),
            _c("booking_date", "date"),
            _c("amount_rappen", "integer", doc="Always positive; see credit_debit_indicator."),
            _c("currency", "text"),
            _c("credit_debit_indicator", "text", values=("CRDT", "DBIT")),
            _c("counterparty_name", "text"),
            _c("counterparty_iban", "text"),
            _c("structured_reference", "text", doc="QR reference as paid."),
            _c("remittance_text", "text"),
            _c("source_format", "text", values=("camt053", "camt054")),
            _c("is_reversal", "boolean"),
            _c(
                "status",
                "text",
                values=(
                    "auto_matched",
                    "suggested_pending_review",
                    "manually_matched",
                    "unmatched",
                    "ignored",
                ),
            ),
            _c("matched_person_id", "integer", nullable=True, references="persons.id"),
            _c(
                "account_entry_id",
                "integer",
                nullable=True,
                references="account_entries.id",
                deferred=True,
                doc="Circular with account_entries.bank_transaction_id.",
            ),
            _CREATED,
        ),
    ),
    TableSpec(
        "account_entries",
        "account_entries",
        "Debitorenkonto: payments, payouts and corrections per person.",
        ("id",),
        (
            _id(),
            _c("person_id", "integer", references="persons.id"),
            _c("kind", "text", values=("payment_received", "payout", "correction")),
            _c(
                "amount_rappen",
                "integer",
                doc="Positive: the person owes the LEG more. Negative: the LEG owes the person; "
                "an incoming payment is negative.",
            ),
            _c("booked_at", "date"),
            _c("billing_run_item_id", "integer", nullable=True, references="billing_run_items.id"),
            _c("bank_transaction_id", "integer", nullable=True, references="bank_transactions.id"),
            _c("note", "text"),
            _CREATED,
        ),
    ),
    TableSpec(
        "dunning_notices",
        "dunning_log",
        "Mahnungen that were sent. The invoices they cover are in dunning_notice_items.",
        ("id",),
        (
            _id(),
            _c("sent_at", "datetime"),
            _c("person_id", "integer", references="persons.id"),
            _c("level", "integer", doc="1 or 2."),
            _c("amount_rappen", "integer", doc="Outstanding amount named in the notice."),
        ),
        rows=_dunning_notice_rows,
    ),
    TableSpec(
        "dunning_notice_items",
        "dunning_log",
        "Which billing_run_items a dunning notice covered (many to many).",
        ("dunning_notice_id", "billing_run_item_id"),
        (
            _c("dunning_notice_id", "integer", references="dunning_notices.id"),
            _c("billing_run_item_id", "integer", references="billing_run_items.id"),
        ),
        rows=_dunning_item_rows,
    ),
    TableSpec(
        "billing_cycles",
        "billing_cycle",
        "Quarterly workflow: when each step of the guided billing run was done. One row per quarter.",
        ("id",),
        (
            _id(),
            _c("period_year", "integer"),
            _c("period_quarter", "integer", doc="1..4. (period_year, period_quarter) is unique."),
            _c("readings_imported_at", "date", nullable=True),
            _c("readings_checked_at", "date", nullable=True),
            _c("computed_at", "date", nullable=True),
            _c("emails_sent_at", "date", nullable=True),
            _c("paper_invoices_sent_at", "date", nullable=True),
            _c("payouts_done_at", "date", nullable=True),
            _c("override_reason", "text"),
            _c("override_at", "datetime", nullable=True),
            _CREATED,
        ),
    ),
    TableSpec(
        "settings",
        "leg_settings",
        "Tariffs and sender data. Exactly one row.",
        ("id",),
        (
            _id(),
            _c("address_street", "text", doc="Sender of the invoices (QR-bill creditor)."),
            _c("address_house_number", "text"),
            _c("address_zip", "text"),
            _c("address_city", "text"),
            _c("address_country", "text"),
            _c("qr_iban", "text"),
            _c("price_rp_per_kwh", "real", doc="Current price, Rappen per kWh."),
            _c("admin_fee_consumption_rp_per_kwh", "real"),
            _c("admin_fee_feed_in_rp_per_kwh", "real"),
            _c("paper_invoice_rappen", "integer", doc="Flat fee for a paper invoice."),
            _c("dunning_new_deadline_days", "integer"),
            _c("dunning_minimum_rappen", "integer", doc="Below this balance no dunning notice is issued."),
            _c("metering_point_country", "text"),
            _c("metering_point_identifier", "text"),
            _c("updated_at", "datetime"),
        ),
    ),
)


def tables_by_name() -> dict[str, TableSpec]:
    """The declarations by table name."""
    return {t.name: t for t in TABLES}


def _select(spec: TableSpec) -> str:
    columns = ", ".join(c.name for c in spec.columns)
    # Identifiers come from the constants above, never from input.
    return f"SELECT {columns} FROM {spec.source_table} ORDER BY {spec.order_by}"  # nosec B608


def _rows(connection: sqlite3.Connection, spec: TableSpec) -> Iterator[tuple]:
    if spec.rows is not None:
        yield from spec.rows(connection)
    else:
        for row in connection.execute(_select(spec)):
            yield tuple(row)


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return repr(value)
    return str(value)


class _ZipSink:
    """Text sink for `csv.writer` that encodes into a ZIP entry while hashing and counting."""

    def __init__(self, entry) -> None:
        self._entry = entry
        self.sha256 = hashlib.sha256()
        self.size = 0

    def write(self, text: str) -> int:
        data = text.encode("utf-8")
        self.sha256.update(data)
        self.size += len(data)
        self._entry.write(data)
        return len(text)


def _manifest_table(spec: TableSpec, row_count: int, sink: _ZipSink) -> dict:
    columns = []
    for c in spec.columns:
        entry: dict = {"name": c.name, "type": c.type, "nullable": c.nullable}
        if c.references:
            entry["references"] = c.references
        if c.deferred:
            entry["deferred"] = True
        if c.values:
            entry["values"] = list(c.values)
        if c.doc:
            entry["description"] = c.doc
        columns.append(entry)
    return {
        "name": spec.name,
        "file": spec.filename,
        "description": spec.description,
        "primary_key": list(spec.primary_key),
        "row_count": row_count,
        "bytes": sink.size,
        "sha256": sink.sha256.hexdigest(),
        "columns": columns,
    }


def export_data(connection: sqlite3.Connection, target: Path, *, app_version: str = "") -> dict:
    """Write the ZIP to `target` and return its manifest."""
    from app.db.schema import get_schema_version

    tables = []
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
            for spec in TABLES:
                with archive.open(spec.filename, "w", force_zip64=True) as entry:
                    sink = _ZipSink(entry)
                    writer = csv.writer(sink, lineterminator="\n")
                    writer.writerow([c.name for c in spec.columns])
                    count = 0
                    date_columns = {i for i, c in enumerate(spec.columns) if c.type == "date"}
                    for row in _rows(connection, spec):
                        # A date column that was ever stored with a time of day is cut to the day.
                        writer.writerow(
                            [_cell(v)[:10] if i in date_columns else _cell(v) for i, v in enumerate(row)]
                        )
                        count += 1
                tables.append(_manifest_table(spec, count, sink))
            manifest = {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "app_version": app_version,
                "schema_version": get_schema_version(connection),
                "conventions": CONVENTIONS,
                "load_order": [t.name for t in TABLES],
                "excluded": EXCLUDED,
                "tables": tables,
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return manifest
