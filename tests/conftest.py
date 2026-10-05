"""Shared pytest fixtures: an isolated, migrated database per test, with the demo data built once for
the whole session rather than once per test."""

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
    """Build one migrated database file for the whole test session."""
    template = tmp_path_factory.mktemp("template") / "migrated.sqlite3"
    connection = sqlite3.connect(template)
    initialize_database(connection)
    connection.close()
    return template


@pytest.fixture(autouse=True)
def _never_touch_the_real_database(tmp_path_factory, _migrated_template):
    """Point `connection_scope()` at a throwaway copy for every test."""
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
    """Provide a fresh, fully migrated in-memory SQLite database."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize_database(connection)
    return connection


#: The demo database, built once per process on first use. A module-level
#: cache rather than a session fixture, because the only caller is the
#: closure inside `_demo_data_from_template` and reaching a fixture from
#: there (`request.getfixturevalue`) trips pytest's own finalizer
#: bookkeeping -- `assert not self._finalizers` -- as soon as a second test
#: asks for it.
_DEMO_TEMPLATE: list = []

#: The genuine generator, captured at import time. Building the template
#: lazily means building it *after* `_demo_data_from_template` has already
#: replaced `create_demo_data` with the restore -- so looking the name up at
#: call time makes the builder call the restore, which calls the builder.
#: The first symptom was not a RecursionError but
#: `'WindowsPath' object has no attribute '_str'`, from pathlib running out
#: of stack halfway down.
_REAL_CREATE_DEMO_DATA = demo_data_module.create_demo_data


def _demo_template(migrated: Path) -> tuple[Path, DemoDataSummary]:
    """Build the demo data once, into a file the tests then copy."""
    if not _DEMO_TEMPLATE:
        template = Path(tempfile.mkdtemp(prefix="leg-demo-")) / "demo.sqlite3"
        shutil.copy2(migrated, template)
        connection = sqlite3.connect(template)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            summary = _REAL_CREATE_DEMO_DATA(connection)
            connection.commit()
        finally:
            connection.close()
        _DEMO_TEMPLATE.append((template, summary))
    return _DEMO_TEMPLATE[0]


@pytest.fixture(autouse=True)
def _demo_data_from_template(request, _migrated_template, monkeypatch):
    """Serve `create_demo_data` from the session template."""
    if request.node.get_closest_marker("real_demo_data"):
        yield
        return

    def restore(connection: sqlite3.Connection):
        """Copy the template over this connection's database."""
        template_path, template_summary = _demo_template(_migrated_template)
        source_uri = f"{template_path.resolve().as_uri()}?mode=ro"
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
    """Write a tiny register archive in swisstopo's own format."""
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


def build_test_register(zip_path: Path, target: Path, data_date=None) -> Path:
    """Run both halves of the build, as the application does."""
    list(address_register_module.build_register(zip_path, target, data_date=data_date))
    address_register_module.finalise_register(target, data_date)
    return target


@pytest.fixture(autouse=True)
def _never_touch_the_real_address_register(tmp_path_factory, monkeypatch):
    """Point the address register at a path that does not exist."""
    absent = Path(tempfile.mkdtemp(dir=tmp_path_factory.getbasetemp())) / "kein-register.sqlite3"
    monkeypatch.setattr(address_lookup_module, "ADDRESS_REGISTER_PATH", absent)
    monkeypatch.setattr(address_register_module, "ADDRESS_REGISTER_PATH", absent)
    yield


@pytest.fixture
def address_register(tmp_path, monkeypatch) -> Path:
    """Build a small real register and point the whole app at it."""
    zip_path = write_register_zip(tmp_path / "register.zip")
    target = tmp_path / "adressregister.sqlite3"
    from datetime import date

    build_test_register(zip_path, target, date.today())
    monkeypatch.setattr(address_lookup_module, "ADDRESS_REGISTER_PATH", target)
    monkeypatch.setattr(address_register_module, "ADDRESS_REGISTER_PATH", target)
    return target


def _key_event(name: str, *, code: str = "", keydown: bool = True):
    """Build one key press, as NiceGUI's keyboard would deliver it."""
    from nicegui.events import KeyboardAction, KeyboardKey, KeyboardModifiers, KeyEventArguments

    return KeyEventArguments(
        sender=None,
        client=None,
        action=KeyboardAction(keydown=keydown, keyup=not keydown, repeat=False),
        key=KeyboardKey(name=name, code=code or name, location=0),
        modifiers=KeyboardModifiers(alt=False, ctrl=False, meta=False, shift=False),
    )


@pytest.fixture(autouse=True)
def _no_list_state_outlives_its_test():
    """Empty the remembered filters around every test."""
    from app.gui import list_state

    list_state.forget_everything()
    yield
    list_state.forget_everything()


@pytest.fixture(autouse=True)
def _no_keyboard_layer_outlives_its_test():
    """Empty the keyboard stack around every test."""
    from app.gui import keyboard

    keyboard._FALLBACK.clear()
    yield
    keyboard._FALLBACK.clear()


@pytest.fixture
def press():
    """Press keys through the app's one dispatcher."""
    from app.gui.keyboard import handle_key

    def _press(name: str) -> None:
        """Press one key."""
        handle_key(_key_event(name))

    return _press


#: The fixture whose tests need the demo database. Carrying 229'632 readings
#: around is the one genuinely expensive thing in this suite, so the tests
#: that do are marked and can be left out while developing.
#:
#: `real_demo_data` is a *marker*, not a fixture, and is checked separately
#: below -- it builds the data for real, which is the most expensive case of
#: all.
_HEAVY_FIXTURES = frozenset({"demo_data"})


def pytest_collection_modifyitems(items) -> None:
    """Mark every test that needs the demo database as `heavy`."""
    for item in items:
        names = set(getattr(item, "fixturenames", ()))
        if names & _HEAVY_FIXTURES or item.get_closest_marker("real_demo_data"):
            item.add_marker(pytest.mark.heavy)
