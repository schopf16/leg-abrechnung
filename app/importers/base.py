"""Shared data types for all reading importers."""

from dataclasses import dataclass, field
from datetime import datetime

#: Recognized reading directions, matching the `readings.direction` column
#: and `MeteringPoint.direction`.
VALID_DIRECTIONS = frozenset({"consumption", "feed_in"})


@dataclass
class ParsedReading:
    """One 15-minute interval value read from an import file, not yet matched against the local..."""

    designation: str
    timestamp: datetime
    direction: str
    kwh: float


@dataclass
class ParseResult:
    """Outcome of parsing one import file."""

    readings: list[ParsedReading] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ImportValidationError(Exception):
    """Raised when an import file is structurally invalid and cannot be processed at all (as opposed to..."""


def validate_direction(raw_value: str) -> str:
    """Normalize a direction string from a source file to the app's vocabulary."""
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
