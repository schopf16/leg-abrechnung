"""Fill the bundled, interactive Beitrittserklärung and retain its contract pages."""

from io import BytesIO
from pathlib import Path
from typing import Optional

from pypdf import PdfReader, PdfWriter

from app.domain.iban_validation import format_iban
from app.domain.membership_contract import ContractFields
from app.domain.phone_format import format_swiss_phone

TEMPLATE_PATH = Path(__file__).with_name("templates") / "beitrittserklaerung.pdf"

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
    source_pdf: Optional[bytes],
    target: Path,
) -> Path:
    """Fill page one of a form PDF and keep its remaining pages unchanged.

    ``source_pdf`` remains available for an explicitly supplied edition. When
    absent, the form bundled with the application is used.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    content = source_pdf if source_pdf is not None else TEMPLATE_PATH.read_bytes()
    reader = PdfReader(BytesIO(content))
    actual_fields = reader.get_fields() or {}
    missing = sorted(set(_FIELD_NAMES.values()) - set(actual_fields))
    if missing:
        raise ValueError(
            "Die Beitrittserklärungs-Vorlage ist unvollständig. "
            "Fehlende Formularfelder: " + ", ".join(missing)
        )
    if not reader.pages:
        raise ValueError("Die Beitrittserklärungs-Vorlage enthält keine Seiten.")

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
    writer.update_page_form_field_values(
        writer.pages[0], values, auto_regenerate=True
    )
    with target.open("wb") as handle:
        writer.write(handle)
    return target
