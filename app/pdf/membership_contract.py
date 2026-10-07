"""Fill an uploaded, interactive Beitrittserklärung and retain its contract pages."""

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from app.domain.iban_validation import format_iban
from app.domain.membership_contract import ContractFields
from app.domain.phone_format import format_swiss_phone


# These are the form fields this software fills. A changed or incomplete
# template must fail visibly instead of producing an apparently complete PDF.
_FIELD_NAMES = {
    "company": "firma",
    "salutation": "anrede",
    "first_name": "vorname",
    "last_name": "nachname",
    "address": "adresse",
    "postal_code": "plz",
    "city": "ort",
    "email": "email",
    "phone": "telefon",
    "consumption_designations": "messpunkt_bezug",
    "feed_in_designations": "messpunkt_einspeisung",
    "iban": "iban",
    "pv_capacity": "pv_leistung_kwp",
    "battery_capacity": "batteriespeicher_kwh",
    "wallbox_capacity": "wallbox_leistung_kw",
    "ort_datum": "ort_datum",
}


def build_contract(
    fields: ContractFields,
    source_pdf: bytes | None,
    target: Path,
) -> Path:
    """Fill page one of an explicitly supplied form PDF and retain its pages."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if source_pdf is None:
        raise ValueError(
            "Keine Beitrittserklärungs-Vorlage in den Einstellungen hinterlegt. "
            "Bitte laden Sie dort ein ausfüllbares PDF hoch."
        )
    content = source_pdf
    validate_contract_template(content)
    reader = PdfReader(BytesIO(content))

    values = {
        "firma": fields.company,
        "anrede": fields.salutation,
        "vorname": fields.first_name or fields.names,
        "nachname": fields.last_name,
        "adresse": fields.address,
        "plz": fields.postal_code,
        "ort": fields.city,
        "email": fields.email,
        "telefon": format_swiss_phone(fields.phone),
        "messpunkt_bezug": " / ".join(fields.consumption_designations),
        "messpunkt_einspeisung": " / ".join(fields.feed_in_designations),
        "iban": format_iban(fields.iban) if fields.iban else "",
        "pv_leistung_kwp": fields.pv_capacity,
        "batteriespeicher_kwh": fields.battery_capacity,
        "wallbox_leistung_kw": fields.wallbox_capacity,
        # The participant fills in the date by hand when signing.
        "ort_datum": "",
    }

    writer = PdfWriter(clone_from=reader)
    # Every page, not only the first: validation accepts a field anywhere in
    # the document, so filling page one alone could leave one silently blank.
    writer.update_page_form_field_values(None, values, auto_regenerate=True)
    with target.open("wb") as handle:
        writer.write(handle)
    return target


def validate_contract_template(content: bytes) -> None:
    """Reject PDF templates that lack a page or a field the app must fill."""
    # pypdf raises more than PdfReadError on a damaged file (struct, key and
    # value errors), and an encrypted one only fails once its fields are read.
    try:
        reader = PdfReader(BytesIO(content))
        page_count = len(reader.pages)
        actual_fields = reader.get_fields() or {}
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Die Beitrittserklärungs-Vorlage ist keine lesbare PDF-Datei.") from exc
    if not page_count:
        raise ValueError("Die Beitrittserklärungs-Vorlage enthält keine Seiten.")
    missing = sorted(set(_FIELD_NAMES.values()) - set(actual_fields))
    if missing:
        raise ValueError(
            "Die Beitrittserklärungs-Vorlage ist unvollständig. "
            "Fehlende Formularfelder: " + ", ".join(missing)
        )
