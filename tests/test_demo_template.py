"""The session demo-data template must be indistinguishable from the real thing.

`tests/conftest.py` replaces `create_demo_data` with a restore from a file
built once per session, which cut 3.4 minutes off the suite. That is a
speed-up bought with a substitution, and a substitution is only safe while
what comes out of it is what the generator produces. These tests are that
guarantee -- without them the whole suite would be testing a cache whose
fidelity nobody checks.

`test_demo_data.py` covers the generator itself and opts out of the
template; this file covers the seam between the two.
"""

import sqlite3

import pytest

from app.db.schema import initialize_database
from app.domain import demo_data as demo_data_module

#: Tables the demo data fills, and which therefore have to come out of a
#: restore exactly as the generator left them.
_TABLES = [
    "substation_area",
    "site",
    "leg",
    "metering_point",
    "person",
    "assignment",
    "readings",
    "leg_settings",
]


def _fingerprint(connection: sqlite3.Connection) -> dict:
    """Summarise a database in a way two of them can be compared.

    Counts per table plus the energy totals, which together would catch a
    restore that dropped rows, duplicated them, or landed a different
    quarter's readings.

    Args:
        connection: Open SQLite connection.

    Returns:
        `{name: value}` for every table count and the two energy sums.
    """
    counts = {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in _TABLES}
    energy = connection.execute(
        """
        SELECT ROUND(COALESCE(SUM(CASE WHEN direction = 'consumption' THEN kwh END), 0), 3) AS consumption,
               ROUND(COALESCE(SUM(CASE WHEN direction <> 'consumption' THEN kwh END), 0), 3) AS feed_in,
               MIN(timestamp) AS first_reading,
               MAX(timestamp) AS last_reading
        FROM readings
        """
    ).fetchone()
    counts.update(
        consumption_kwh=energy["consumption"],
        feed_in_kwh=energy["feed_in"],
        first_reading=energy["first_reading"],
        last_reading=energy["last_reading"],
    )
    return counts


@pytest.mark.real_demo_data
def test_a_restored_database_matches_a_generated_one(db, tmp_path):
    """The substitution's whole justification, checked rather than assumed.

    This test opts out of the template so it can run the genuine generator
    itself, then compares that against what every other test in the suite
    receives.
    """
    demo_data_module.create_demo_data(db)
    generated = _fingerprint(db)

    restored_path = tmp_path / "restored.sqlite3"
    restored = sqlite3.connect(restored_path)
    restored.row_factory = sqlite3.Row
    restored.execute("PRAGMA foreign_keys = ON")
    initialize_database(restored)
    source = sqlite3.connect(":memory:")
    db.backup(source)
    source.backup(restored)
    restored.commit()

    assert _fingerprint(restored) == generated
    assert generated["readings"] > 200_000, "die Messwerte sind der Grund für den Umweg"


def test_the_restore_is_repeatable_within_one_test(db):
    """Restoring twice leaves the same database, not a doubled one.

    The real generator refuses a second call (`DemoDataAlreadyExists`); the
    restore simply overwrites, which is fine but must not accumulate. That
    difference is also the sharpest available proof that the substitution
    is in place at all: if it ever stopped happening, this test would hit
    the guard and fail rather than silently putting 3.4 minutes back.
    """
    from app.domain.demo_data import create_demo_data

    create_demo_data(db)
    once = _fingerprint(db)
    create_demo_data(db)

    assert _fingerprint(db) == once


@pytest.mark.real_demo_data
def test_the_marker_really_restores_the_real_generator(db):
    """Otherwise `test_demo_data.py` would be testing the cache.

    A restore cannot raise `DemoDataAlreadyExists`, so that exception is
    the sharpest available proof that the genuine function is in place.
    """
    demo_data_module.create_demo_data(db)

    with pytest.raises(demo_data_module.DemoDataAlreadyExists):
        demo_data_module.create_demo_data(db)
