"""Swiss metering point designation (metering point designation) validation.

Per the VSE guideline, the 33-character code has a fixed structure:

    Stellen  1–2   Land (immer "CH" für die Schweiz)
    Stellen  3–13  identifier des Netzbetreibers (11-stellig)
    Stellen 14–33  metering point number (20-stellig, alphanumerisch, mit
                   führenden Nullen aufgefüllt)

Unlike an IBAN, this designation has **no built-in check digit** -- the
guideline defines the structure but no checksum, so there is nothing to
compute a MOD-97-style validation against. The practical mitigation used
here: since a single LEG deployment always sits in one grid operator's
territory, Land and identifier are entered once (see
`LegSettings.metering_point_country`/`metering_point_identifier`) and only the
20-character metering point number varies per MeteringPoint -- structural validation
(length, allowed characters) is the strongest plausibility check available.
"""

import re
from typing import Optional

#: Land: exactly 2 uppercase letters.
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
#: identifier: exactly 11 uppercase alphanumeric characters.
_IDENTIFIKATOR_RE = re.compile(r"^[0-9A-Z]{11}$")
#: Full 33-character designation: Land + identifier + metering point number.
_FULL_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{11}[0-9A-Z]{20}$")

#: Total length of a valid metering point designation.
DESIGNATION_LENGTH = 33
#: Length of the metering point number part alone (zero-padded on the left).
METERING_POINT_NUMBER_LENGTH = 20


def assemble_metering_point_designation(country: str, identifier: str, metering_point_number: str) -> str:
    """Combine the three entry fields into the full 33-character designation.

    The metering point number is left-padded with zeros to fill all 20
    characters, per the guideline ("Leere Stellen müssen mit einer Null
    belegt werden").

    Args:
        land: 2-letter country code.
        identifier: 11-character grid-operator identifier.
        metering_point_number: The meter-specific tail, any length up to 20.

    Returns:
        The assembled, uppercased designation (not necessarily valid --
        call `validate_metering_point_designation` to check it).
    """
    return (
        country.strip().upper()
        + identifier.strip().upper()
        + metering_point_number.strip().upper().zfill(METERING_POINT_NUMBER_LENGTH)
    )


def validate_metering_point_designation(value: str) -> Optional[str]:
    """Check a metering point designation's structural plausibility.

    Args:
        value: The (assembled) 33-character designation.

    Returns:
        `None` if `value` is structurally plausible, otherwise a
        human-readable German error message.
    """
    candidate = value.strip().upper()
    if not candidate:
        return "Messpunkt-Bezeichnung darf nicht leer sein."
    if len(candidate) != DESIGNATION_LENGTH:
        return (
            "Messpunkt-Bezeichnung muss genau "
            f"{DESIGNATION_LENGTH} Zeichen lang sein (aktuell {len(candidate)})."
        )
    if not _FULL_RE.match(candidate):
        return (
            "Messpunkt-Bezeichnung darf nur Grossbuchstaben und Ziffern "
            "enthalten (Aufbau: 2 Zeichen Land + 11 Zeichen Identifikator "
            "+ 20 Zeichen Messpunktnummer)."
        )
    return None


def validate_country(value: str) -> Optional[str]:
    """Check that a Land value is exactly 2 uppercase letters, if given.

    Args:
        value: Raw user input.

    Returns:
        `None` if `value` is empty or valid, otherwise a German error message.
    """
    candidate = value.strip().upper()
    if not candidate:
        return None
    if not _COUNTRY_RE.match(candidate):
        return "Land muss aus genau 2 Buchstaben bestehen."
    return None


def validate_identifier(value: str) -> Optional[str]:
    """Check that an identifier value is exactly 11 alphanumeric characters, if given.

    Args:
        value: Raw user input.

    Returns:
        `None` if `value` is empty or valid, otherwise a German error message.
    """
    candidate = value.strip().upper()
    if not candidate:
        return None
    if not _IDENTIFIKATOR_RE.match(candidate):
        return "Identifikator muss aus genau 11 Ziffern/Grossbuchstaben bestehen."
    return None
