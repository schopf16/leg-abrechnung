"""Parser for BKW's EBIX / SDAT-CH metering data export."""

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
    """Map a Swiss OBIS register code to an internal direction."""
    prefix = ".".join(obis_code.split(".")[:2])
    if prefix == "1.8":
        return "consumption"
    if prefix == "2.8":
        return "feed_in"
    raise ImportValidationError(
        f"Unbekannter OBIS-Code {obis_code!r}: erwartet 1.8.x (Bezug) oder 2.8.x (Einspeisung)."
    )


def _make_parser() -> etree.XMLParser:
    """Build a hardened lxml parser that refuses external entities and DTDs."""
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def parse_ebix_file(path: Path) -> ParseResult:
    """Parse a BKW EBIX/SDAT-CH export into `ParsedReading` objects."""
    try:
        tree = etree.parse(str(path), parser=_make_parser())
    except etree.XMLSyntaxError as exc:
        raise ImportValidationError(f"Ungültiges XML: {exc}") from exc

    root = tree.getroot()
    return _extract_time_series(root)


def _extract_time_series(root: Element) -> ParseResult:
    """Walk the document tree and turn each time series into readings."""
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
        designation = _find_text(series, "MeteringPointID")
        obis_code = _find_text(series, "ObisCode")
        period = _find_ns(series, "Period")
        if designation is None or obis_code is None or period is None:
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
                f"{designation}: nur {_SUPPORTED_RESOLUTION} wird unterstützt."
            )

        start_text = _find_text(period, "Start")
        if start_text is None:
            result.warnings.append(
                f"Periode ohne Start-Zeitstempel bei Messpunkt {designation} übersprungen."
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
                    f"Wert ohne Position oder Inhalt bei Messpunkt {designation} übersprungen."
                )
                continue
            try:
                position = int(position_text)
                kwh = float(value_element.text)
            except ValueError:
                result.warnings.append(
                    f"Ungültiger Wert {value_element.text!r} (Position {position_text}) "
                    f"bei Messpunkt {designation} übersprungen."
                )
                continue
            if kwh < 0:
                result.warnings.append(
                    f"Negativer Wert {kwh} (Position {position}) bei Messpunkt {designation} übersprungen."
                )
                continue
            timestamp = start + timedelta(minutes=15 * (position - 1))
            result.readings.append(
                ParsedReading(
                    designation=designation,
                    timestamp=timestamp,
                    direction=direction,
                    kwh=kwh,
                )
            )

    return result


def _find_text(element: Element, tag: str) -> str | None:
    """Find a direct child element by tag (namespace-agnostic) and return its text."""
    for child in element:
        local_tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else None
        if local_tag == tag:
            return child.text.strip() if child.text else None
    return None


def _find_all_ns(element: Element, tag: str) -> list[Element]:
    """Find all direct child elements matching a local tag name, ignoring namespaces."""
    return [
        child for child in element if isinstance(child.tag, str) and etree.QName(child.tag).localname == tag
    ]


def _find_ns(element: Element, tag: str) -> Element | None:
    """Find a direct child element by local tag name, ignoring namespaces."""
    for child in element:
        local_tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else None
        if local_tag == tag:
            return child
    return None
