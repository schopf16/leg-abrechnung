"""Swiss metering point designation (Messpunktbezeichnung) validation.

Per the VSE guideline, the 33-character code has a fixed structure:

    Stellen  1–2   Land (immer "CH" für die Schweiz)
    Stellen  3–13  Identifikator des Netzbetreibers (11-stellig)
    Stellen 14–33  Messpunktnummer (20-stellig, alphanumerisch, mit
                   führenden Nullen aufgefüllt)

Unlike an IBAN, this designation has **no built-in check digit** -- the
guideline defines the structure but no checksum, so there is nothing to
compute a MOD-97-style validation against. The practical mitigation used
here: since a single LEG deployment always sits in one grid operator's
territory, Land and Identifikator are entered once (see
`LegSettings.messpunkt_land`/`messpunkt_identifikator`) and only the
20-character Messpunktnummer varies per Messpunkt -- structural validation
(length, allowed characters) is the strongest plausibility check available.
"""

import re
from typing import Optional

#: Land: exactly 2 uppercase letters.
_LAND_RE = re.compile(r"^[A-Z]{2}$")
#: Identifikator: exactly 11 uppercase alphanumeric characters.
_IDENTIFIKATOR_RE = re.compile(r"^[0-9A-Z]{11}$")
#: Full 33-character designation: Land + Identifikator + Messpunktnummer.
_FULL_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{11}[0-9A-Z]{20}$")

#: Total length of a valid Messpunktbezeichnung.
MESSPUNKTBEZEICHNUNG_LENGTH = 33
#: Length of the Messpunktnummer part alone (zero-padded on the left).
MESSPUNKTNUMMER_LENGTH = 20


def assemble_messpunkt_bezeichnung(land: str, identifikator: str, messpunktnummer: str) -> str:
    """Combine the three entry fields into the full 33-character designation.

    The Messpunktnummer is left-padded with zeros to fill all 20
    characters, per the guideline ("Leere Stellen müssen mit einer Null
    belegt werden").

    Args:
        land: 2-letter country code.
        identifikator: 11-character grid-operator identifier.
        messpunktnummer: The meter-specific tail, any length up to 20.

    Returns:
        The assembled, uppercased designation (not necessarily valid --
        call `validate_messpunkt_bezeichnung` to check it).
    """
    return (
        land.strip().upper()
        + identifikator.strip().upper()
        + messpunktnummer.strip().upper().zfill(MESSPUNKTNUMMER_LENGTH)
    )


def validate_messpunkt_bezeichnung(value: str) -> Optional[str]:
    """Check a Messpunktbezeichnung's structural plausibility.

    Args:
        value: The (assembled) 33-character designation.

    Returns:
        `None` if `value` is structurally plausible, otherwise a
        human-readable German error message.
    """
    candidate = value.strip().upper()
    if not candidate:
        return "Messpunkt-Bezeichnung darf nicht leer sein."
    if len(candidate) != MESSPUNKTBEZEICHNUNG_LENGTH:
        return (
            "Messpunkt-Bezeichnung muss genau "
            f"{MESSPUNKTBEZEICHNUNG_LENGTH} Zeichen lang sein (aktuell {len(candidate)})."
        )
    if not _FULL_RE.match(candidate):
        return (
            "Messpunkt-Bezeichnung darf nur Grossbuchstaben und Ziffern "
            "enthalten (Aufbau: 2 Zeichen Land + 11 Zeichen Identifikator "
            "+ 20 Zeichen Messpunktnummer)."
        )
    return None


def validate_land(value: str) -> Optional[str]:
    """Check that a Land value is exactly 2 uppercase letters, if given.

    Args:
        value: Raw user input.

    Returns:
        `None` if `value` is empty or valid, otherwise a German error message.
    """
    candidate = value.strip().upper()
    if not candidate:
        return None
    if not _LAND_RE.match(candidate):
        return "Land muss aus genau 2 Buchstaben bestehen."
    return None


def validate_identifikator(value: str) -> Optional[str]:
    """Check that an Identifikator value is exactly 11 alphanumeric characters, if given.

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
