"""Looks addresses up in the local register: suggestions, and verification."""

import difflib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.paths import ADDRESS_REGISTER_PATH
from app.sort_keys import fold_for_sort

#: How many suggestions a field offers. More than this and the list stops
#: being a shortlist you can scan.
DEFAULT_LIMIT = 8

#: Splits a typed query into its text and a trailing house number, so
#: "Erstweg 4" narrows to that one address while "Erstweg" still offers the
#: whole street.
_QUERY_TAIL = re.compile(r"^(.*?)[\s,]+(\d[\w.\-/]*)$")

#: Field names a finding can be about. Plain strings rather than an enum to
#: match the rest of the project (see `QualityWarning.category`).
FIELD_STREET = "street"
FIELD_HOUSE_NUMBER = "house_number"
FIELD_POSTAL_CODE = "postal_code"
FIELD_LOCALITY = "locality"

#: Similarity needed before a correction is offered. Localities tolerate the
#: default: the candidates are the handful of names behind one postal code,
#: all quite different from each other. Street names need more, because
#: nearly every one of them ends in "strasse" and the shared suffix alone
#: carries the score: "Rosenstrasse" against "Nelkenstrasse" already
#: reaches 0.720 without the two having anything to do with each other. On
#: real data a correctly spelled street was offered a different real street
#: at 0.733, while the genuine typos in the same deployment sat at 0.828 and
#: above.
_CUTOFF_DEFAULT = 0.6
_CUTOFF_STREET = 0.8


@dataclass(frozen=True)
class AddressSuggestion:
    """One candidate offered while an address is being typed."""

    street: str
    house_number: str
    postal_code: str
    locality: str

    @property
    def label(self) -> str:
        """The single line shown in the suggestion list."""
        left = " ".join(part for part in (self.street, self.house_number) if part)
        return f"{left}, {self.postal_code} {self.locality}".strip(", ")


@dataclass(frozen=True)
class AddressFinding:
    """One thing the register disagrees with about a stored address."""

    field: str
    value: str
    suggestion: str


def _resolve(path: Optional[Path]) -> Path:
    """Fall back to the configured register location."""
    return path if path is not None else ADDRESS_REGISTER_PATH


def _connect(path: Path) -> Optional[sqlite3.Connection]:
    """Open the register read-only, or report that there is none."""
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error:
        return None


def open_register(path: Optional[Path] = None) -> Optional[sqlite3.Connection]:
    """Open the register once, for a caller that will check many addresses."""
    return _connect(_resolve(path))


def register_available(path: Optional[Path] = None) -> bool:
    """Whether address lookups can do anything at all."""
    connection = _connect(_resolve(path))
    if connection is None:
        return False
    try:
        connection.execute("SELECT 1 FROM street LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def split_query(query: str) -> tuple[str, str]:
    """Split a typed query into street text and a trailing house number."""
    match = _QUERY_TAIL.match(query.strip())
    if match:
        return (match.group(1).strip(), match.group(2).strip())
    return (query.strip(), "")


def suggest_addresses(
    query: str,
    limit: int = DEFAULT_LIMIT,
    path: Optional[Path] = None,
    postal_code: str = "",
) -> list[AddressSuggestion]:
    """Narrow the register down to what has been typed so far."""
    street_text, house_number = split_query(query)
    folded = fold_for_sort(street_text)
    code = postal_code.strip()
    if len(folded) < 2:
        return []

    connection = _connect(_resolve(path))
    if connection is None:
        return []
    try:
        if house_number:
            rows = connection.execute(
                """
                SELECT s.street, a.number, s.postal_code, s.locality
                FROM street s JOIN address a ON a.street_id = s.id
                WHERE s.street_fold LIKE ? AND a.number_fold LIKE ?
                ORDER BY (s.postal_code = ?) DESC,
                         a.official DESC, (a.status = 'real') DESC,
                         LENGTH(s.street_fold), s.street_fold, LENGTH(a.number), a.number
                LIMIT ?
                """,
                (f"{folded}%", f"{fold_for_sort(house_number)}%", code, limit),
            ).fetchall()
            return [
                AddressSuggestion(r["street"], r["number"], r["postal_code"], r["locality"]) for r in rows
            ]

        rows = connection.execute(
            """
            SELECT street, postal_code, locality
            FROM street
            WHERE street_fold LIKE ?
            ORDER BY (postal_code = ?) DESC, LENGTH(street_fold), street_fold, postal_code
            LIMIT ?
            """,
            (f"{folded}%", code, limit),
        ).fetchall()
        if not rows:
            # Only now the slower contains-match: a leading wildcard cannot
            # use the index, and over 197'000 rows that is still fast, but
            # it is pointless while the cheap prefix match still has hits.
            rows = connection.execute(
                """
                SELECT street, postal_code, locality
                FROM street
                WHERE street_fold LIKE ?
                ORDER BY (postal_code = ?) DESC, LENGTH(street_fold), street_fold, postal_code
                LIMIT ?
                """,
                (f"%{folded}%", code, limit),
            ).fetchall()
        return [AddressSuggestion(r["street"], "", r["postal_code"], r["locality"]) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def suggest_localities(
    query: str,
    limit: int = DEFAULT_LIMIT,
    path: Optional[Path] = None,
) -> list[AddressSuggestion]:
    """Narrow postal codes and localities, for the PLZ and Ort fields."""
    text = query.strip()
    if len(text) < 2:
        return []
    connection = _connect(_resolve(path))
    if connection is None:
        return []
    try:
        # Two separate statements rather than one with an interpolated WHERE
        # clause: no SQL in this project is built by string formatting, and
        # a fixed set of alternatives is no reason to start.
        if text.isdigit():
            rows = connection.execute(_BY_POSTAL_CODE, (f"{text}%", limit)).fetchall()
        else:
            rows = connection.execute(_BY_LOCALITY, (f"{fold_for_sort(text)}%", limit)).fetchall()
        return [AddressSuggestion("", "", r["postal_code"], r["locality"]) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        connection.close()


#: The two locality queries. `weight` puts the locality that most streets
#: carry first, so "3065 Bolligen" leads "3065 Bolligen Dorf".
_BY_POSTAL_CODE = """
    SELECT postal_code, locality, COUNT(*) AS weight FROM street
    WHERE postal_code LIKE ?
    GROUP BY postal_code, locality ORDER BY weight DESC, postal_code, locality
    LIMIT ?
"""

_BY_LOCALITY = """
    SELECT postal_code, locality, COUNT(*) AS weight FROM street
    WHERE locality_fold LIKE ?
    GROUP BY postal_code, locality ORDER BY weight DESC, postal_code, locality
    LIMIT ?
"""


def _official_localities(connection: sqlite3.Connection, postal_code: str) -> list[str]:
    """Every postal locality the register lists for one postal code."""
    rows = connection.execute(
        """
        SELECT locality, COUNT(*) AS weight FROM street WHERE postal_code = ?
        GROUP BY locality ORDER BY weight DESC
        """,
        (postal_code,),
    ).fetchall()
    return [row["locality"] for row in rows]


def verify(
    street: str,
    house_number: str,
    postal_code: str,
    locality: str,
    path: Optional[Path] = None,
    connection: Optional[sqlite3.Connection] = None,
) -> list[AddressFinding]:
    """Check one address against the register."""
    # Nothing to check before there is an address. A freshly opened dialog
    # otherwise greeted the administrator with "Nicht im amtlichen
    # Verzeichnis." under an empty field -- a complaint about something they
    # had not written yet, sitting exactly where they were looking for help.
    # Every check below needs the postal code, so without one there is no
    # honest statement to make either.
    if not street.strip() or not postal_code.strip():
        return []

    own_connection = connection is None
    connection = connection or _connect(_resolve(path))
    if connection is None:
        return []
    try:
        findings: list[AddressFinding] = []
        code = postal_code.strip()
        folded_street = fold_for_sort(street)

        street_rows = connection.execute(
            "SELECT id, street, locality FROM street WHERE street_fold = ? AND postal_code = ?",
            (folded_street, code),
        ).fetchall()

        # An empty house number is "not typed yet", not "wrong". Reporting
        # it would nag through every keystroke of the street above it, and
        # the register holds 7'078 addresses without a number anyway.
        if street_rows and house_number.strip():
            street_ids = [row["id"] for row in street_rows]
            # The only interpolation into SQL in this module, and it inserts
            # nothing but "?,?,?" -- SQLite has no parameter for a list, so a
            # generated placeholder list is the standard way to write IN.
            # Every value still travels as a bound parameter.
            placeholders = ",".join("?" * len(street_ids))
            folded_number = fold_for_sort(house_number)
            number_query = (
                f"SELECT 1 FROM address WHERE street_id IN ({placeholders}) "  # nosec B608
                "AND number_fold = ? LIMIT 1"
            )
            found = connection.execute(number_query, (*street_ids, folded_number)).fetchone()
            if not found:
                candidates_query = (
                    "SELECT DISTINCT number FROM address "
                    f"WHERE street_id IN ({placeholders})"  # nosec B608
                )
                candidates = [row["number"] for row in connection.execute(candidates_query, street_ids)]
                findings.append(
                    AddressFinding(
                        FIELD_HOUSE_NUMBER,
                        house_number,
                        _closest(house_number, candidates),
                    )
                )
        elif not street_rows:
            # Before proposing a different street: does this one exist under
            # another postal code? Then the street is right and the postal
            # code is wrong, and saying so beats offering a street the
            # administrator never meant. Found by running the real data,
            # where a correctly spelled street was offered a different real
            # street because they share the "strasse" ending.
            elsewhere = connection.execute(
                """
                SELECT postal_code, COUNT(*) AS weight FROM street
                WHERE street_fold = ?
                GROUP BY postal_code ORDER BY weight DESC LIMIT 1
                """,
                (folded_street,),
            ).fetchone()
            if elsewhere:
                findings.append(AddressFinding(FIELD_POSTAL_CODE, code, elsewhere["postal_code"]))
            else:
                candidates = [
                    row["street"]
                    for row in connection.execute(
                        "SELECT DISTINCT street FROM street WHERE postal_code = ?", (code,)
                    )
                ]
                findings.append(
                    AddressFinding(FIELD_STREET, street, _closest(street, candidates, _CUTOFF_STREET))
                )

        official = _official_localities(connection, code)
        wrong_postal_code = any(f.field == FIELD_POSTAL_CODE for f in findings)
        # A wrong postal code makes the locality check meaningless -- it
        # would compare against the localities of a postal code that is
        # itself the mistake, and report two findings for one error.
        if (
            not wrong_postal_code
            and official
            and fold_for_sort(locality) not in {fold_for_sort(n) for n in official}
        ):
            # Deliberately one rule for two causes: a typo and the political
            # municipality both end up here, and both get the postal
            # locality offered. The UI must not tell them apart.
            preferred = next((row["locality"] for row in street_rows if row["locality"]), official[0])
            findings.append(
                AddressFinding(FIELD_LOCALITY, locality, _closest(locality, official) or preferred)
            )
        return findings
    except sqlite3.Error:
        return []
    finally:
        if own_connection:
            connection.close()


def _closest(value: str, candidates: list[str], cutoff: float = _CUTOFF_DEFAULT) -> str:
    """Pick the candidate a human most likely meant, or nothing."""
    if not value.strip() or not candidates:
        return ""
    matches = difflib.get_close_matches(value.strip(), candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else ""
