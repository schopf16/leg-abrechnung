"""Parser for the CSV fallback export BKW offers as an EBIX alternative.

Expected columns (semicolon- or comma-separated, header row required,
column order does not matter, matching is case-insensitive):

    Messpunkt;Zeitstempel;Richtung;Wert_kWh
    CH1000000000000000000000001;2025-07-01T00:00:00;Bezug;0.123
    CH1000000000000000000000001;2025-07-01T00:15:00;Bezug;0.150

- `Messpunkt`: metering point designation (business key, "messpunkt_bezeichnung").
- `Zeitstempel`: ISO-8601 interval start (`YYYY-MM-DDTHH:MM:SS`).
- `Richtung`: "Bezug" or "Einspeisung" (German, case-insensitive; English
  synonyms are also accepted, see `app.importers.base.validate_direction`).
- `Wert_kWh`: energy for the interval, decimal point or comma.

As with the EBIX parser, no real BKW CSV sample was available; adjust the
`_COLUMN_ALIASES` mapping below if the real export uses different header
names -- the rest of the pipeline is unaffected.
"""

import csv
from datetime import datetime
from pathlib import Path

from app.importers.base import ImportValidationError, ParsedReading, ParseResult, validate_direction

#: Maps accepted header spellings (lowercased) to the canonical field name.
_COLUMN_ALIASES = {
    "messpunkt": "messpunkt_bezeichnung",
    "messpunkt_bezeichnung": "messpunkt_bezeichnung",
    "zaehlpunkt": "messpunkt_bezeichnung",
    "zählpunkt": "messpunkt_bezeichnung",
    "zeitstempel": "timestamp",
    "timestamp": "timestamp",
    "richtung": "direction",
    "direction": "direction",
    "wert_kwh": "kwh",
    "wert": "kwh",
    "kwh": "kwh",
    "value": "kwh",
}

_REQUIRED_FIELDS = {"messpunkt_bezeichnung", "timestamp", "direction", "kwh"}


def _sniff_delimiter(sample: str) -> str:
    """Guess the CSV delimiter used by a sample of file content.

    Args:
        sample: First chunk of the file's text content.

    Returns:
        Either ";" or "," -- whichever appears more often in the sample,
        defaulting to ";" as commonly used in Swiss/German exports.
    """
    return ";" if sample.count(";") >= sample.count(",") else ","


def parse_csv_file(path: Path) -> ParseResult:
    """Parse a BKW CSV reading export into `ParsedReading` objects.

    Args:
        path: Filesystem path of the `.csv` file to parse.

    Returns:
        The parsed readings plus any non-fatal warnings.

    Raises:
        ImportValidationError: If the file has no header row, or is
            missing one of the required columns.
    """
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
        raise ImportValidationError(
            f"CSV-Datei: fehlende Spalten {sorted(missing)}. Gefunden: {header}"
        )

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
            result.warnings.append(
                f"Zeile {line_number} übersprungen: negativer Wert {kwh}."
            )
            continue

        result.readings.append(
            ParsedReading(
                messpunkt_bezeichnung=values["messpunkt_bezeichnung"],
                timestamp=timestamp,
                direction=direction,
                kwh=kwh,
            )
        )

    return result
