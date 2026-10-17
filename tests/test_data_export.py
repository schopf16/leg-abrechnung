"""The data export: a self-describing ZIP whose foreign keys all resolve."""

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime

import pytest

from app.domain.billing import create_billing_runs_for_all_legs
from app.domain.demo_data import SUMMER_QUARTER, create_demo_data
from app.export.data_export import FORMAT_VERSION, TABLES, export_data
from app.models import dunning_log

#: Tables the export leaves out on purpose (see `EXCLUDED`). A new table lands here or in `TABLES`.
NOT_EXPORTED_TABLES = {
    "schema_meta",
    "web_registration",
    "web_registration_meter",
    "message_template",
    "message_template_attachment",
    "person_message_log",
    "signatures",
    "email_broadcast_log",
    "leg_document",
    "import_batches",
}

#: Columns of exported tables that stay behind on purpose.
NOT_EXPORTED_COLUMNS = {
    "readings": {"id", "import_batch_id"},
    "billing_run_items": {"pdf_path"},
    "person": {"billing_address_confirmed", "billing_city_confirmed"},
    "site": {"address_confirmed", "locality_confirmed"},
    "dunning_log": {"billing_run_item_ids"},
    "leg_settings": {
        "extra_backup_dir",
        "web_registration_cursor",
        "onboarding_overdue_days",
        "invoice_email_subject",
        "invoice_email_body",
        "dunning1_email_subject",
        "dunning1_email_body",
        "dunning2_email_subject",
        "dunning2_email_body",
        "production_capacity_warn_percent",
        "leg_founding_min_persons",
    },
}


@pytest.fixture
def exported(db, tmp_path):
    """Export a demo community with a billed quarter and a dunning notice."""
    create_demo_data(db)
    create_billing_runs_for_all_legs(db, *SUMMER_QUARTER)
    item_id, person_id = db.execute("SELECT id, person_id FROM billing_run_items LIMIT 1").fetchone()
    dunning_log.create(
        db, person_id=person_id, level=1, amount_rappen=1234, billing_run_item_ids=[item_id, 99999]
    )
    target = tmp_path / "export.zip"
    manifest = export_data(db, target, app_version="test")
    archive = zipfile.ZipFile(target)
    tables = {
        t["name"]: list(
            csv.DictReader(io.TextIOWrapper(archive.open(t["file"]), encoding="utf-8", newline=""))
        )
        for t in manifest["tables"]
    }
    return manifest, archive, tables


def test_the_zip_holds_the_manifest_and_one_file_per_table(exported):
    manifest, archive, _ = exported
    assert manifest["format_version"] == FORMAT_VERSION
    assert sorted(archive.namelist()) == sorted(["manifest.json"] + [t.filename for t in TABLES])
    assert json.loads(archive.read("manifest.json")) == manifest


def test_manifest_row_counts_and_checksums_match_the_files(exported):
    manifest, archive, tables = exported
    for entry in manifest["tables"]:
        data = archive.read(entry["file"])
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert len(data) == entry["bytes"]
        assert len(tables[entry["name"]]) == entry["row_count"]
        assert data.decode("utf-8").splitlines()[0].split(",") == [c["name"] for c in entry["columns"]]


def test_the_demo_export_is_not_empty_where_it_matters(exported):
    _, _, tables = exported
    for name in ("persons", "sites", "metering_points", "assignments", "readings", "billing_run_items"):
        assert tables[name], name


def test_every_foreign_key_resolves(exported):
    manifest, _, tables = exported
    keys = {
        name: {tuple(r[c] for c in t["primary_key"]) for r in tables[name]}
        for t in manifest["tables"]
        for name in [t["name"]]
    }
    for entry in manifest["tables"]:
        for column in entry["columns"]:
            if "references" not in column:
                continue
            target_table, target_column = column["references"].split(".")
            target_values = {r[target_column] for r in tables[target_table]}
            for row in tables[entry["name"]]:
                value = row[column["name"]]
                assert value == "" and column["nullable"] or value in target_values, (
                    f"{entry['name']}.{column['name']} = {value!r} has no row in {column['references']}"
                )
    assert keys  # the primary keys are unique per file (set size == row count)
    for entry in manifest["tables"]:
        assert len(keys[entry["name"]]) == entry["row_count"], entry["name"]


_PARSERS = {
    "integer": int,
    "real": float,
    "text": str,
    "boolean": lambda v: {"0": 0, "1": 1}[v],
    "date": date.fromisoformat,
    "datetime": datetime.fromisoformat,
}


def test_every_cell_parses_as_its_declared_type(exported):
    manifest, _, tables = exported
    for entry in manifest["tables"]:
        for row in tables[entry["name"]]:
            for column in entry["columns"]:
                value = row[column["name"]]
                if value == "":
                    assert column["nullable"] or column["type"] == "text", (entry["name"], column["name"])
                    continue
                _PARSERS[column["type"]](value)
                if "values" in column:
                    assert value in column["values"], (entry["name"], column["name"], value)


def test_money_columns_are_integers(exported):
    manifest, _, tables = exported
    for entry in manifest["tables"]:
        for column in entry["columns"]:
            if column["name"].endswith("_rappen"):
                assert column["type"] == "integer"
                for row in tables[entry["name"]]:
                    assert re.fullmatch(r"-?\d*", row[column["name"]]), row[column["name"]]


def test_a_dunning_notice_is_split_into_notice_and_items(exported):
    _, _, tables = exported
    assert len(tables["dunning_notices"]) == 1
    # The id of an invoice that no longer exists is dropped, not exported dangling.
    assert [r["billing_run_item_id"] for r in tables["dunning_notice_items"]] != []
    assert len(tables["dunning_notice_items"]) == 1


def test_no_database_table_is_forgotten(db):
    """A table added by a migration must be exported or named as left out."""
    in_database = {
        r[0]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }
    exported_sources = {t.source_table for t in TABLES}
    assert in_database - exported_sources - NOT_EXPORTED_TABLES == set()


def test_no_column_is_forgotten(db):
    """A column added by a migration must be exported or named as left out."""
    for spec in TABLES:
        in_database = {r[1] for r in db.execute(f"PRAGMA table_info({spec.source_table})")}
        exported_columns = {c.name for c in spec.columns}
        assert in_database - exported_columns - NOT_EXPORTED_COLUMNS.get(spec.source_table, set()) <= {
            "id"
        } or (spec.rows is not None), spec.name
        if spec.rows is None:
            assert exported_columns <= in_database, spec.name


def test_load_order_puts_referenced_tables_first(exported):
    manifest, _, _ = exported
    position = {name: i for i, name in enumerate(manifest["load_order"])}
    for entry in manifest["tables"]:
        for column in entry["columns"]:
            if "references" in column and not column.get("deferred"):
                assert position[column["references"].split(".")[0]] <= position[entry["name"]], column


def test_a_failed_export_leaves_no_partial_file(db, tmp_path, monkeypatch):
    def boom(connection, spec):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.export.data_export._rows", boom)
    with pytest.raises(RuntimeError):
        export_data(db, tmp_path / "x.zip")
    assert list(tmp_path.iterdir()) == []


def test_the_page_writes_a_zip_into_the_export_folder(tmp_path, monkeypatch):
    """The button's worker: own connection, file lands in the folder."""
    from app.gui.pages import data_export as page

    monkeypatch.setattr(page, "EXPORT_DIR", tmp_path)
    target, manifest = page._run_export()
    assert target.parent == tmp_path and zipfile.is_zipfile(target)
    assert [t["name"] for t in manifest["tables"]] == [t.name for t in TABLES]
