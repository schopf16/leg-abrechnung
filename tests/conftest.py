"""Shared pytest fixtures: an isolated, migrated in-memory database per test."""

import sqlite3

import pytest

from app.db.schema import initialize_database


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
