"""Parser for BKW's EBIX / SDAT-CH metering data export.

=====================================================================
 IMPORTANT -- ADAPT THIS FILE ONCE A REAL BKW SAMPLE FILE IS AVAILABLE
=====================================================================
No real EBIX export from BKW was available while this was written. The
element and attribute names below are a best-effort approximation of the
public ebIX / SDAT-CH conventions (UN/CEFACT-style time series: a metering
point id, an OBIS code identifying the register, a period with a start
time and a fixed resolution, and a sequence of position-indexed values --
*not* one timestamp per value).

Everything outside `_extract_time_series` is generic (validation, meter
matching, idempotent storage) and should need no changes. When the real
file arrives:

1. Inspect its actual element/namespace names.
2. Rewrite `_extract_time_series` (and, if the namespace differs, the
   `_NAMESPACES` map) to match.
3. Update `tests/fixtures/sample_ebix.xml` and re-run
   `tests/test_ebix_parser.py` -- the test only asserts on the resulting
   `ParsedReading` list, so it keeps working unchanged.

OBIS codes used to determine direction (Swiss convention):
    1.8.0 (and 1.8.x sub-registers) = Wirkenergie Bezug   -> "bezug"
    2.8.0 (and 2.8.x sub-registers) = Wirkenergie Lieferung/Einspeisung
                                                          -> "einspeisung"
"""

from datetime import datetime, timedelta
from pathlib import Path
from xml.etree.ElementTree import Element

from lxml import etree

from app.importers.base import ImportValidationError, ParsedReading, ParseResult

#: Only 15-minute resolution is supported; anything else is a hard error
#: since the whole distribution engine assumes this granularity.
_SUPPORTED_RESOLUTION = "PT15M"

_NAMESPACES = {"e": "urn:ebix-ch:sdat:demo:v1"}


def _obis_to_direction(obis_code: str) -> str:
    """Map a Swiss OBIS register code to an internal direction.

    Args:
        obis_code: OBIS code string, e.g. "1.8.0" or "2.8.1".

    Returns:
        Either "bezug" or "einspeisung".

    Raises:
        ImportValidationError: If the OBIS code's first component is
            neither "1.8" nor "2.8".
    """
    prefix = ".".join(obis_code.split(".")[:2])
    if prefix == "1.8":
        return "bezug"
    if prefix == "2.8":
        return "einspeisung"
    raise ImportValidationError(
        f"Unbekannter OBIS-Code {obis_code!r}: erwartet 1.8.x (Bezug) "
        "oder 2.8.x (Einspeisung)."
    )


def _make_parser() -> etree.XMLParser:
    """Build a hardened lxml parser that refuses external entities and DTDs.

    Import files come from an external data source; disabling DTD loading
    and entity resolution avoids XXE-style attacks via a crafted file.

    Returns:
        A configured `lxml.etree.XMLParser`.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def parse_ebix_file(path: Path) -> ParseResult:
    """Parse a BKW EBIX/SDAT-CH export into `ParsedReading` objects.

    Args:
        path: Filesystem path of the `.xml` file to parse.

    Returns:
        The parsed readings plus any non-fatal warnings.

    Raises:
        ImportValidationError: If the file is not well-formed XML, or a
            time series uses an unsupported resolution.
    """
    try:
        tree = etree.parse(str(path), parser=_make_parser())
    except etree.XMLSyntaxError as exc:
        raise ImportValidationError(f"Ungültiges XML: {exc}") from exc

    root = tree.getroot()
    return _extract_time_series(root)


def _extract_time_series(root: Element) -> ParseResult:
    """Walk the document tree and turn each time series into readings.

    This is the function to rewrite once the real BKW schema is known --
    see the module docstring.

    Args:
        root: Root element of the parsed EBIX document.

    Returns:
        The parsed readings plus any non-fatal warnings.
    """
    result = ParseResult()

    series_elements = root.findall(".//e:MeteringPointTimeSeries", _NAMESPACES) or root.findall(
        ".//MeteringPointTimeSeries"
    )
    if not series_elements:
        raise ImportValidationError(
            "Keine MeteringPointTimeSeries-Elemente gefunden -- unerwartetes "
            "Dateiformat. Siehe Hinweis in app/importers/ebix_parser.py."
        )

    for series in series_elements:
        messpunkt_bezeichnung = _find_text(series, "MeteringPointID")
        obis_code = _find_text(series, "ObisCode")
        period = _find_ns(series, "Period")
        if messpunkt_bezeichnung is None or obis_code is None or period is None:
            result.warnings.append(
                "Zeitreihe ohne Messpunkt-Bezeichnung, OBIS-Code oder Periode übersprungen."
            )
            continue

        try:
            direction = _obis_to_direction(obis_code)
        except ImportValidationError as exc:
            result.warnings.append(str(exc))
            continue

        resolution = _find_text(period, "Resolution")
        if resolution != _SUPPORTED_RESOLUTION:
            raise ImportValidationError(
                f"Nicht unterstützte Auflösung {resolution!r} bei Messpunkt "
                f"{messpunkt_bezeichnung}: nur {_SUPPORTED_RESOLUTION} wird unterstützt."
            )

        start_text = _find_text(period, "Start")
        if start_text is None:
            result.warnings.append(
                f"Periode ohne Start-Zeitstempel bei Messpunkt {messpunkt_bezeichnung} übersprungen."
            )
            continue
        start = datetime.fromisoformat(start_text)

        values_container = _find_ns(period, "Values")
        if values_container is None:
            continue
        for value_element in _find_all_ns(values_container, "Value"):
            position_text = value_element.get("position")
            if position_text is None or value_element.text is None:
                result.warnings.append(
                    f"Wert ohne Position oder Inhalt bei Messpunkt {messpunkt_bezeichnung} übersprungen."
                )
                continue
            try:
                position = int(position_text)
                kwh = float(value_element.text)
            except ValueError:
                result.warnings.append(
                    f"Ungültiger Wert {value_element.text!r} (Position {position_text}) "
                    f"bei Messpunkt {messpunkt_bezeichnung} übersprungen."
                )
                continue
            if kwh < 0:
                result.warnings.append(
                    f"Negativer Wert {kwh} (Position {position}) bei Messpunkt "
                    f"{messpunkt_bezeichnung} übersprungen."
                )
                continue
            timestamp = start + timedelta(minutes=15 * (position - 1))
            result.readings.append(
                ParsedReading(
                    messpunkt_bezeichnung=messpunkt_bezeichnung,
                    timestamp=timestamp,
                    direction=direction,
                    kwh=kwh,
                )
            )

    return result


def _find_text(element: Element, tag: str) -> str | None:
    """Find a direct child element by tag (namespace-agnostic) and return its text.

    Args:
        element: Parent element to search.
        tag: Local tag name to look for (namespace prefix stripped).

    Returns:
        The stripped text content, or `None` if no matching child exists.
    """
    for child in element:
        local_tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else None
        if local_tag == tag:
            return child.text.strip() if child.text else None
    return None


def _find_all_ns(element: Element, tag: str) -> list[Element]:
    """Find all direct child elements matching a local tag name, ignoring namespaces.

    Args:
        element: Parent element to search.
        tag: Local tag name to look for.

    Returns:
        All matching direct children, in document order.
    """
    return [
        child
        for child in element
        if isinstance(child.tag, str) and etree.QName(child.tag).localname == tag
    ]


def _find_ns(element: Element, tag: str) -> Element | None:
    """Find a direct child element by local tag name, ignoring namespaces.

    Args:
        element: Parent element to search.
        tag: Local tag name to look for.

    Returns:
        The matching child element, or `None`.
    """
    for child in element:
        local_tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else None
        if local_tag == tag:
            return child
    return None
