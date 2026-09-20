"""Shared pytest fixtures: an isolated, migrated in-memory database per test."""

import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.db import connection as connection_module
from app.db.schema import initialize_database


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
