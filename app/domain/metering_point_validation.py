"""Swiss metering point designation (metering point designation) validation."""

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
    """Combine the three entry fields into the full 33-character designation."""
    return (
        country.strip().upper()
        + identifier.strip().upper()
        + metering_point_number.strip().upper().zfill(METERING_POINT_NUMBER_LENGTH)
    )


def validate_metering_point_designation(value: str) -> Optional[str]:
    """Check a metering point designation's structural plausibility."""
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
    """Check that a Land value is exactly 2 uppercase letters, if given."""
    candidate = value.strip().upper()
    if not candidate:
        return None
    if not _COUNTRY_RE.match(candidate):
        return "Land muss aus genau 2 Buchstaben bestehen."
    return None


def validate_identifier(value: str) -> Optional[str]:
    """Check that an identifier value is exactly 11 alphanumeric characters, if given."""
    candidate = value.strip().upper()
    if not candidate:
        return None
    if not _IDENTIFIKATOR_RE.match(candidate):
        return "Identifikator muss aus genau 11 Ziffern/Grossbuchstaben bestehen."
    return None
