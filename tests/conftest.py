"""Shared pytest fixtures: an isolated, migrated database per test, with the
demo data built once for the whole session rather than once per test.

Two templates, same idea, and the second one is where the time went.
Migrating from scratch per test once turned a 90-second suite into nine
minutes, which `_migrated_template` fixed by building the schema once and
copying the file afterwards. `create_demo_data` then became the same
problem one layer up: 61 tests call it, each one computing 229'632
readings, which measured at 3.3 seconds a call -- 3.4 minutes of an
8.5-minute suite spent producing byte-identical data over and over.
"""

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

from app.db import connection as connection_module
from app.db.schema import initialize_database
from app.domain import address_lookup as address_lookup_module
from app.domain import demo_data as demo_data_module
from app.domain.demo_data import DemoDataSummary
from app.importers import address_register as address_register_module


@pytest.fixture(scope="session")
def _migrated_template(tmp_path_factory) -> Path:
    """Build one migrated database file for the whole test session.

    Migrating from scratch per test turned a 90-second suite into nine
    minutes; copying this template instead costs microseconds.

    Returns:
        Path of the template file. Never opened for writing by tests.
    """
    template = tmp_path_factory.mktemp("template") / "migrated.sqlite3"
    connection = sqlite3.connect(template)
    initialize_database(connection)
    connection.close()
    return template


@pytest.fixture(autouse=True)
def _never_touch_the_real_database(tmp_path_factory, _migrated_template):
    """Point `connection_scope()` at a throwaway copy for every test.

    The `db` fixture below only isolates code that takes a connection.
    Anything calling `app.db.connection.connection_scope()` without one --
    every GUI page, for instance -- would otherwise open the real
    `data/leg_abrechnung.sqlite3`, which holds actual members' names,
    addresses and IBANs. Rendering a page in a test was enough to read it.

    Autouse and unconditional: this is a safety net, not an opt-in.

    Yields:
        None.
    """
    scratch = Path(tempfile.mkdtemp(dir=tmp_path_factory.getbasetemp())) / "test.sqlite3"
    shutil.copy2(_migrated_template, scratch)

    original = connection_module.connection_scope.__wrapped__.__defaults__
    connection_module.connection_scope.__wrapped__.__defaults__ = (scratch,)
    try:
        yield
    finally:
        connection_module.connection_scope.__wrapped__.__defaults__ = original


@pytest.fixture
def db() -> sqlite3.Connection:
    """Provide a fresh, fully migrated in-memory SQLite database.

    Each test gets its own connection so tests never interfere with each
    other or with the real application database in ``data/``.

    Returns:
        An initialized `sqlite3.Connection` with `row_factory` set to
        `sqlite3.Row`.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize_database(connection)
    return connection


@pytest.fixture(scope="session")
def _demo_template(_migrated_template, tmp_path_factory) -> tuple[Path, DemoDataSummary]:
    """Build the demo data once, into a file the tests then copy.

    Produced by the **real** `create_demo_data`, so what the tests work on
    is what that function actually generates -- this caches the result, it
    does not reimplement it. `test_demo_data.py` keeps calling the real
    function directly (see `_demo_data_from_template`), so the generator
    itself stays under test.

    Returns:
        `(path, summary)`: the template file, never opened for writing by
        tests, and the `DemoDataSummary` the real call produced, so a
        restore can hand back the same object the real call would have.
    """
    template = tmp_path_factory.mktemp("demo") / "demo.sqlite3"
    shutil.copy2(_migrated_template, template)
    connection = sqlite3.connect(template)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        summary = demo_data_module.create_demo_data(connection)
        connection.commit()
    finally:
        connection.close()
    return template, summary


@pytest.fixture(autouse=True)
def _demo_data_from_template(request, _demo_template, monkeypatch):
    """Serve `create_demo_data` from the session template.

    Replaced in every test module that imported the name, because they
    imported it directly (`from app.domain.demo_data import
    create_demo_data`) and patching the module attribute alone would not
    reach those references. pytest imports every test module during
    collection, so they are all in `sys.modules` by the time this runs.

    A test that needs the genuine article marks itself
    `@pytest.mark.real_demo_data` -- `test_demo_data.py` does, because its
    whole job is checking what the generator produces, including the
    `DemoDataAlreadyExists` guard, which a restore cannot raise.

    Safe to swap in because a restore replaces the whole database: no test
    writes rows before calling it (checked), and none uses the return
    value outside `test_demo_data.py`.

    Yields:
        None.
    """
    if request.node.get_closest_marker("real_demo_data"):
        yield
        return

    template_path, template_summary = _demo_template
    source_uri = f"{template_path.resolve().as_uri()}?mode=ro"

    def restore(connection: sqlite3.Connection):
        """Copy the template over this connection's database.

        Args:
            connection: The target connection, migrated and empty.

        Returns:
            The `DemoDataSummary` the real call would have returned.
        """
        source = sqlite3.connect(source_uri, uri=True)
        try:
            source.backup(connection)
        finally:
            source.close()
        connection.commit()
        return template_summary

    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if name.startswith("tests.") and hasattr(module, "create_demo_data"):
            monkeypatch.setattr(module, "create_demo_data", restore)
    monkeypatch.setattr(demo_data_module, "create_demo_data", restore)
    yield


#: Header of swisstopo's address CSV, in the real column order. Only the
#: columns the importer reads carry meaning; the rest are present because a
#: parser that silently depends on column *count* should fail here, not on
#: the real 446 MB file.
_REGISTER_HEADER = (
    "ADR_EGAID;STR_ESID;BDG_EGID;ADR_EDID;STN_LABEL;ADR_NUMBER;BDG_CATEGORY;BDG_NAME;"
    "ZIP_LABEL;COM_FOSNR;COM_NAME;COM_CANTON;ADR_STATUS;ADR_OFFICIAL;ADR_MODIFIED;"
    "ADR_EASTING;ADR_NORTHING"
)

#: A handful of invented addresses covering every shape the real register
#: holds: a plain number, a dotted one ("31.1", 331'401 of those), a letter
#: suffix, an empty number, a non-official row, a planned row, a multi-word
#: locality, and a postal locality that differs from the political
#: municipality -- the Worblaufen/Ittigen case, which is the reason the app
#: fills the postal name and never the municipality.
REGISTER_ROWS = [
    ("Erstweg", "4", "3048 Musterdorf", "Grossgemeinde", "real", "true"),
    ("Erstweg", "6", "3048 Musterdorf", "Grossgemeinde", "real", "true"),
    ("Erstweg", "31.1", "3048 Musterdorf", "Grossgemeinde", "real", "false"),
    ("Erstweg", "8a", "3048 Musterdorf", "Grossgemeinde", "real", "true"),
    ("Zweitweg", "", "3048 Musterdorf", "Grossgemeinde", "planned", "true"),
    ("Ärniweg", "2", "3048 Musterdorf", "Grossgemeinde", "real", "true"),
    ("Drittweg", "1", "3065 Beispiel Dorf", "Beispiel", "real", "true"),
]


def write_register_zip(target: Path, rows=None) -> Path:
    """Write a tiny register archive in swisstopo's own format.

    A real ZIP with a real CSV rather than a stubbed parser: the importer's
    job is reading that format, and a test that bypasses it would pass while
    the format handling is broken.

    Args:
        target: Path of the ZIP to write.
        rows: `(street, number, zip_label, municipality, status, official)`
            tuples, defaulting to `REGISTER_ROWS`.

    Returns:
        `target`, for chaining.
    """
    import zipfile

    lines = []
    for index, (street, number, zip_label, municipality, status, official) in enumerate(
        rows if rows is not None else REGISTER_ROWS, start=1
    ):
        lines.append(
            f"{index};{index};{index};0;{street};{number};residential;;{zip_label};"
            f"35{index};{municipality};BE;{status};{official};01.01.2026;2600000;1200000"
        )
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            "amtliches-gebaeudeadressverzeichnis_ch_2056.csv",
            "\ufeff" + _REGISTER_HEADER + "\n" + "\n".join(lines) + "\n",
        )
    return target


@pytest.fixture(autouse=True)
def _never_touch_the_real_address_register(tmp_path_factory, monkeypatch):
    """Point the address register at a path that does not exist.

    Same safety net as `_never_touch_the_real_database`, one layer over: the
    register lives in `data/` on the developer's machine, and any test that
    renders the Personen or Standorte page calls into
    `app.domain.address_check`, which would otherwise read it. Results would
    then depend on whether the machine happens to have downloaded it.

    Autouse and unconditional. Tests that want a register ask for the
    `address_register` fixture, which points the same attributes at a real
    little one.

    Yields:
        None.
    """
    absent = Path(tempfile.mkdtemp(dir=tmp_path_factory.getbasetemp())) / "kein-register.sqlite3"
    monkeypatch.setattr(address_lookup_module, "ADDRESS_REGISTER_PATH", absent)
    monkeypatch.setattr(address_register_module, "ADDRESS_REGISTER_PATH", absent)
    yield


@pytest.fixture
def address_register(tmp_path, monkeypatch) -> Path:
    """Build a small real register and point the whole app at it.

    Returns:
        Path of the built register file.
    """
    zip_path = write_register_zip(tmp_path / "register.zip")
    target = tmp_path / "adressregister.sqlite3"
    from datetime import date

    list(address_register_module.build_register(zip_path, target, data_date=date.today()))
    monkeypatch.setattr(address_lookup_module, "ADDRESS_REGISTER_PATH", target)
    monkeypatch.setattr(address_register_module, "ADDRESS_REGISTER_PATH", target)
    return target
