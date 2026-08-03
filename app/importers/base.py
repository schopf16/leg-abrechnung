"""Shared data types for all reading importers.

Both the EBIX and the CSV parser produce exactly this shape, so
`app.importers.import_service` (and everything above it) never has to know
which file format a given reading originally came from.
"""

from dataclasses import dataclass, field
from datetime import datetime

#: Recognized reading directions, matching the `readings.direction` column.
VALID_DIRECTIONS = frozenset({"bezug", "produktion"})


@dataclass
class ParsedReading:
    """One 15-minute interval value read from an import file, not yet
    matched against the local meter registry.

    Attributes:
        metering_point_id: Business key as it appears in the source file.
        timestamp: Interval start (naive local datetime).
        direction: Either "bezug" or "produktion".
        kwh: Energy for the interval in kWh, non-negative.
    """

    metering_point_id: str
    timestamp: datetime
    direction: str
    kwh: float


@dataclass
class ParseResult:
    """Outcome of parsing one import file.

    Attributes:
        readings: Successfully parsed readings.
        warnings: Human-readable (German) messages about rows that were
            skipped or look suspicious, for display in the import UI.
    """

    readings: list[ParsedReading] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ImportValidationError(Exception):
    """Raised when an import file is structurally invalid and cannot be
    processed at all (as opposed to a single bad row, which becomes a
    warning instead).
    """


def validate_direction(raw_value: str) -> str:
    """Normalize a direction string from a source file to the app's vocabulary.

    Args:
        raw_value: Raw direction text from the source file (e.g. "Bezug",
            "BEZUG", "production", or an OBIS code prefix already resolved
            by the caller).

    Returns:
        Either "bezug" or "produktion".

    Raises:
        ImportValidationError: If `raw_value` cannot be mapped to a known
            direction.
    """
    normalized = raw_value.strip().lower()
    mapping = {
        "bezug": "bezug",
        "consumption": "bezug",
        "import": "bezug",
        "produktion": "produktion",
        "production": "produktion",
        "einspeisung": "produktion",
        "export": "produktion",
    }
    if normalized not in mapping:
        raise ImportValidationError(f"Unbekannte Richtung: {raw_value!r}")
    return mapping[normalized]
