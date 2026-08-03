"""Generates one synthetic EBIX test file per meter currently configured
in the live app database, each covering a full calendar year of
15-minute readings.

For manual testing of the Import page only -- this is a standalone
utility script, not used by the application or the automated test suite
(those use the small fixtures in tests/fixtures/ instead).

Usage:
    .venv\\Scripts\\python.exe scripts\\generate_test_ebix.py [year]

If no year is given, the last fully completed calendar year is used.
Output files are written to output/test_ebix/<year>/, one per meter,
named after the meter's label and metering point id.
"""

import math
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.paths import DATABASE_PATH, OUTPUT_DIR  # noqa: E402

INTERVAL_MINUTES = 15
NAMESPACE = "urn:ebix-ch:sdat:demo:v1"

# Same household load shape used by app.domain.demo_data, average kW per
# hour-of-day (index 0-23).
_HOURLY_LOAD_KW = [
    0.30, 0.25, 0.20, 0.20, 0.20, 0.25, 0.40, 0.70,
    0.60, 0.50, 0.45, 0.45, 0.50, 0.45, 0.40, 0.45,
    0.55, 0.75, 0.90, 0.85, 0.70, 0.55, 0.45, 0.35,
]
_WEEKDAY_FACTOR = [1.0, 1.0, 1.0, 1.0, 1.05, 1.2, 1.15]  # Mon .. Sun
_WEATHER_DAY_SCALE = [0.5, 1.0, 1.4]  # cloudy / mixed / sunny, cycling

# Per-meter scale factors (relative household/installation size), matched
# to app.domain.demo_data's demo dataset for consistency.
_METER_SCALES = {
    "CH1000000000000000000000001": 1.0,   # Anna - Bezug
    "CH1000000000000000000000002": 4.0,   # Anna - Produktion
    "CH1000000000000000000000003": 1.3,   # Beat - Bezug
    "CH1000000000000000000000004": 3.0,   # Beat - Produktion
    "CH1000000000000000000000005": 0.8,   # Carla - Bezug fix
    "CH1000000000000000000000006": 1.5,   # Carla - Bezug WP (geschaltet)
    "CH1000000000000000000000007": 1.1,   # Bergstrasse 4 - Bezug
}
_DEFAULT_SCALE = 1.0

_CONSUMPTION_ROLES = {"bezug", "bezug_fix", "bezug_geschaltet"}


def _seasonal_solar_factor(day_of_year: int) -> float:
    """Smooth yearly solar envelope: ~1.0 around the summer solstice
    (day 172, ~21 June), ~0.0 around the winter solstice (~21 December).

    Args:
        day_of_year: 0-based day offset since 1 January.

    Returns:
        A factor in [0, 1].
    """
    return 0.5 + 0.5 * math.cos(2 * math.pi * (day_of_year - 172) / 365)


def _seasonal_consumption_factor(day_of_year: int) -> float:
    """Mild inverse-seasonal consumption envelope (more use in winter).

    Args:
        day_of_year: 0-based day offset since 1 January.

    Returns:
        A factor roughly in [0.85, 1.15].
    """
    return 1.15 - 0.30 * _seasonal_solar_factor(day_of_year)


def _consumption_kwh(moment: datetime, day_of_year: int, scale: float) -> float:
    """Synthetic consumption value for one 15-minute interval.

    Args:
        moment: Interval start.
        day_of_year: 0-based day offset since 1 January of the target year.
        scale: Per-meter scale factor.

    Returns:
        Energy for the interval, in kWh.
    """
    kw = (
        _HOURLY_LOAD_KW[moment.hour]
        * _WEEKDAY_FACTOR[moment.weekday()]
        * _seasonal_consumption_factor(day_of_year)
        * scale
    )
    return round(kw * (INTERVAL_MINUTES / 60), 3)


def _production_kwh(moment: datetime, day_of_year: int, scale: float) -> float:
    """Synthetic solar production value for one 15-minute interval.

    Args:
        moment: Interval start.
        day_of_year: 0-based day offset since 1 January of the target year.
        scale: Per-meter scale factor.

    Returns:
        Energy for the interval, in kWh (zero outside daylight hours).
    """
    hour = moment.hour + moment.minute / 60
    if hour < 6 or hour > 20:
        return 0.0
    bell = math.sin(math.pi * (hour - 6) / 14)
    weather_scale = _WEATHER_DAY_SCALE[day_of_year % len(_WEATHER_DAY_SCALE)]
    kw = bell * scale * weather_scale * _seasonal_solar_factor(day_of_year)
    return round(max(kw, 0.0) * (INTERVAL_MINUTES / 60), 3)


def _generate_year_values(role: str, year: int, scale: float) -> list[float]:
    """Generate a full year of 15-minute values for one meter.

    Args:
        role: Meter role ("bezug", "produktion", "bezug_fix", "bezug_geschaltet").
        year: Calendar year to generate.
        scale: Per-meter scale factor.

    Returns:
        Values in interval order, position 1 first.
    """
    is_production = role == "produktion"
    start = datetime(year, 1, 1)
    end = datetime(year + 1, 1, 1)
    values = []
    moment = start
    while moment < end:
        day_of_year = (moment.date() - start.date()).days
        if is_production:
            values.append(_production_kwh(moment, day_of_year, scale))
        else:
            values.append(_consumption_kwh(moment, day_of_year, scale))
        moment += timedelta(minutes=INTERVAL_MINUTES)
    return values


def _obis_code_for_role(role: str) -> str:
    """Map a meter role to the OBIS code the parser expects.

    Args:
        role: Meter role.

    Returns:
        "2.8.0" for production, "1.8.0" for any consumption role.
    """
    return "2.8.0" if role == "produktion" else "1.8.0"


def _sanitize_filename(text: str) -> str:
    """Turn arbitrary text into a safe filename segment.

    Args:
        text: Text to sanitize.

    Returns:
        A filesystem-safe version of `text`.
    """
    cleaned = re.sub(r"[^\w\-]", "_", text, flags=re.UNICODE)
    return re.sub(r"_+", "_", cleaned).strip("_")


def _write_ebix_file(
    output_path: Path, metering_point_id: str, role: str, year: int, values: list[float]
) -> None:
    """Write one meter's full-year values as an EBIX-style XML file.

    Args:
        output_path: Destination file path.
        metering_point_id: Business key of the meter.
        role: Meter role, used to pick the OBIS code.
        year: Calendar year the values start at.
        values: Interval values in order, position 1 first.

    Returns:
        None.
    """
    obis_code = _obis_code_for_role(role)
    start = datetime(year, 1, 1)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<DocumentSeries xmlns="{NAMESPACE}">',
        "  <MeteringPointTimeSeries>",
        f"    <MeteringPointID>{metering_point_id}</MeteringPointID>",
        f"    <ObisCode>{obis_code}</ObisCode>",
        "    <Period>",
        f"      <Start>{start.isoformat()}</Start>",
        "      <Resolution>PT15M</Resolution>",
        "      <Values>",
    ]
    for position, value in enumerate(values, start=1):
        lines.append(f'        <Value position="{position}">{value:.3f}</Value>')
    lines.extend(
        [
            "      </Values>",
            "    </Period>",
            "  </MeteringPointTimeSeries>",
            "</DocumentSeries>",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Generate one full-year EBIX file per meter in the live database.

    Reads the target year from `sys.argv[1]` if given, otherwise uses the
    last fully completed calendar year. Reads meters from the live
    application database (`data/leg_abrechnung.sqlite3`) and writes one
    file per meter to `output/test_ebix/<year>/`.

    Returns:
        None.
    """
    year = int(sys.argv[1]) if len(sys.argv) > 1 else date.today().year - 1

    if not DATABASE_PATH.exists():
        print(f"Keine Datenbank gefunden unter {DATABASE_PATH}. App zuerst starten.")
        sys.exit(1)

    connection = sqlite3.connect(str(DATABASE_PATH))
    connection.row_factory = sqlite3.Row
    meters = connection.execute(
        "SELECT metering_point_id, label, role FROM meters ORDER BY id"
    ).fetchall()
    connection.close()

    if not meters:
        print("Keine Zähler in der Datenbank gefunden. Zuerst Zähler anlegen.")
        sys.exit(1)

    output_dir = OUTPUT_DIR / "test_ebix" / str(year)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Erzeuge EBIX-Testdateien für {len(meters)} Zähler, Jahr {year} ...")
    for meter in meters:
        scale = _METER_SCALES.get(meter["metering_point_id"], _DEFAULT_SCALE)
        values = _generate_year_values(meter["role"], year, scale)
        filename = f"{_sanitize_filename(meter['label'])}_{meter['metering_point_id']}.xml"
        output_path = output_dir / filename
        _write_ebix_file(output_path, meter["metering_point_id"], meter["role"], year, values)
        print(f"  {output_path.name}  ({len(values)} Werte, {output_path.stat().st_size / 1024:.0f} KB)")

    print(f"\nFertig. Dateien liegen in: {output_dir}")


if __name__ == "__main__":
    main()
