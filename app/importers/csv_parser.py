"""Parser for the CSV fallback export BKW offers as an EBIX alternative."""

import csv
from datetime import datetime
from pathlib import Path

from app.importers.base import ImportValidationError, ParsedReading, ParseResult, validate_direction

#: Maps accepted header spellings (lowercased) to the canonical field name.
_COLUMN_ALIASES = {
    "metering_point": "designation",
    "designation": "designation",
    "zaehlpunkt": "designation",
    "zählpunkt": "designation",
    "zeitstempel": "timestamp",
    "timestamp": "timestamp",
    "richtung": "direction",
    "direction": "direction",
    "wert_kwh": "kwh",
    "wert": "kwh",
    "kwh": "kwh",
    "value": "kwh",
}

_REQUIRED_FIELDS = {"designation", "timestamp", "direction", "kwh"}


def _sniff_delimiter(sample: str) -> str:
    """Guess the CSV delimiter used by a sample of file content."""
    return ";" if sample.count(";") >= sample.count(",") else ","


def parse_csv_file(path: Path) -> ParseResult:
    """Parse a BKW CSV reading export into `ParsedReading` objects."""
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ImportValidationError("CSV-Datei ist leer.")

    delimiter = _sniff_delimiter(text[:2000])
    reader = csv.reader(text.splitlines(), delimiter=delimiter)

    try:
        header = next(reader)
    except StopIteration:
        raise ImportValidationError("CSV-Datei enthält keine Kopfzeile.") from None

    field_by_index = {}
    for index, raw_name in enumerate(header):
        canonical = _COLUMN_ALIASES.get(raw_name.strip().lower())
        if canonical:
            field_by_index[index] = canonical

    missing = _REQUIRED_FIELDS - set(field_by_index.values())
    if missing:
        raise ImportValidationError(f"CSV-Datei: fehlende Spalten {sorted(missing)}. Gefunden: {header}")

    result = ParseResult()
    for line_number, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        values = {}
        for index, cell in enumerate(row):
            field = field_by_index.get(index)
            if field:
                values[field] = cell.strip()

        try:
            timestamp = datetime.fromisoformat(values["timestamp"])
            direction = validate_direction(values["direction"])
            kwh = float(values["kwh"].replace(",", "."))
        except (KeyError, ValueError, ImportValidationError) as exc:
            result.warnings.append(f"Zeile {line_number} übersprungen: {exc}")
            continue

        if kwh < 0:
            result.warnings.append(f"Zeile {line_number} übersprungen: negativer Wert {kwh}.")
            continue

        result.readings.append(
            ParsedReading(
                designation=values["designation"],
                timestamp=timestamp,
                direction=direction,
                kwh=kwh,
            )
        )

    return result
