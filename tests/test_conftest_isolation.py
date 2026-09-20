"""The test suite must never reach the real database.

`data/leg_abrechnung.sqlite3` holds real members' names, addresses and
IBANs. The `db` fixture only isolates code that takes a connection --
anything calling `connection_scope()` without one (every GUI page) would
open the real file. An autouse fixture in `conftest.py` redirects it; this
pins that the redirect is in force, because losing it silently would be
both a privacy problem and a source of impossible-to-reproduce failures.
"""

from app.db import connection as connection_module
from app.paths import DATABASE_PATH


def test_connection_scope_does_not_point_at_the_real_database():
    (target,) = connection_module.connection_scope.__wrapped__.__defaults__

    assert target != DATABASE_PATH
    assert "data" not in target.parts, f"suspiciously close to the real thing: {target}"


def test_a_page_level_connection_opens_the_throwaway_database():
    """What the guard is actually for: code that asks for a connection
    itself, the way a page does."""
    from app.models import person as person_repo

    with connection_module.connection_scope() as connection:
        assert person_repo.list_all(connection) == []
