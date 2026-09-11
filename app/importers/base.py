"""Shared data types for all reading importers.

Both the EBIX and the CSV parser produce exactly this shape, so
`app.importers.import_service` (and everything above it) never has to know
which file format a given reading originally came from.
"""

from dataclasses import dataclass, field
from datetime import datetime

#: Recognized reading directions, matching the `readings.direction` column
#: and `MeteringPoint.direction`.
VALID_DIRECTIONS = frozenset({"consumption", "feed_in"})


@dataclass
class ParsedReading:
    """One 15-minute interval value read from an import file, not yet
    matched against the local MeteringPoint registry.

    Attributes:
        designation: Business key (grid operator's metering
            point id) as it appears in the source file.
        timestamp: Interval start (naive local datetime).
        direction: Either "consumption" or "feed_in" -- the persisted
            enum values, see `VALID_DIRECTIONS`.
        kwh: Energy for the interval in kWh, non-negative.
    """

    designation: str
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
            "BEZUG", "consumption", "production", or an OBIS code prefix
            already resolved by the caller).

    Returns:
        Either "consumption" or "feed_in" (the persisted enum values).

    Raises:
        ImportValidationError: If `raw_value` cannot be mapped to a known
            direction.
    """
    normalized = raw_value.strip().lower()
    # Source files (BKW EBIX/CSV) label the direction in German; the
    # persisted vocabulary is English. Both spellings are accepted.
    mapping = {
        "bezug": "consumption",
        "consumption": "consumption",
        "import": "consumption",
        "einspeisung": "feed_in",
        "feed_in": "feed_in",
        "produktion": "feed_in",
        "production": "feed_in",
        "export": "feed_in",
    }
    if normalized not in mapping:
        raise ImportValidationError(f"Unbekannte Richtung: {raw_value!r}")
    return mapping[normalized]
