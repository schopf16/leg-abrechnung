"""The table that lets a list skip the checks it cannot be marked by.

Measured on the live deployment: every list page ran all eleven checks, so
`problems_for` cost 128 ms -- 49 ms of it `check_substation_area_one_sided`
and 41 ms `check_addresses`, and on the Personen page the first of those
cannot produce a single finding. The administrator reported the pages
opening slowly and this was most of the server's share.

The table is declared rather than discovered, because a check's subject is
only known after running it, which is the thing being avoided. The risk is
therefore drift, and that is what these tests are for -- derived from the
source rather than from a run, because no test data triggers all eleven
checks at once.
"""

import ast
from pathlib import Path

from app.domain import quality_checks
from app.domain.quality_checks import ALL_CHECKS, CHECK_SUBJECTS, checks_for


def _subjects_in_the_source() -> dict[str, set[str]]:
    """Which `SUBJECT_*` each check's body mentions.

    Returns:
        `{check name: {constant name}}`.
    """
    tree = ast.parse(Path("app/domain/quality_checks.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    found: dict[str, set[str]] = {}
    for check in ALL_CHECKS:
        node = functions[check.__name__]
        found[check.__name__] = {
            inner.id
            for inner in ast.walk(node)
            if isinstance(inner, ast.Name) and inner.id.startswith("SUBJECT_")
        }
    return found


def test_every_check_declares_the_subjects_it_can_mark():
    """A check that gains a subject must gain a table entry with it.

    Otherwise its findings would quietly stop reaching the list they belong
    to: the overview would show them and the list would have no triangle,
    which is exactly the gap the markers were built to close.
    """
    in_source = _subjects_in_the_source()

    for check in ALL_CHECKS:
        declared = {
            name
            for name in dir(quality_checks)
            if name.startswith("SUBJECT_") and getattr(quality_checks, name) in CHECK_SUBJECTS[check]
        }
        assert declared == in_source[check.__name__], check.__name__


def test_every_check_is_in_the_table():
    """`checks_for` indexes the table, so a missing entry is a KeyError."""
    assert set(CHECK_SUBJECTS) == set(ALL_CHECKS)


def test_a_list_runs_only_what_can_mark_it():
    """The point of the table."""
    for check in checks_for(quality_checks.SUBJECT_PERSON):
        assert quality_checks.SUBJECT_PERSON in CHECK_SUBJECTS[check], check.__name__

    assert quality_checks.check_substation_area_one_sided not in checks_for(quality_checks.SUBJECT_PERSON)
    assert quality_checks.check_addresses in checks_for(quality_checks.SUBJECT_PERSON)
    assert quality_checks.check_addresses in checks_for(quality_checks.SUBJECT_SITE)


def test_the_checks_keep_the_order_the_overview_uses():
    """A list's findings must not come out in a different order per page."""
    selected = checks_for(quality_checks.SUBJECT_PERSON)

    positions = [ALL_CHECKS.index(check) for check in selected]
    assert positions == sorted(positions)


def test_deployment_wide_checks_mark_no_list():
    """They belong on the overview and have no record to point at.

    Declared as an empty tuple rather than left out, so the table stays a
    statement about every check instead of a list of some of them.
    """
    assert CHECK_SUBJECTS[quality_checks.check_open_billing_cycle] == ()
    assert CHECK_SUBJECTS[quality_checks.check_unresolved_bank_transactions] == ()
