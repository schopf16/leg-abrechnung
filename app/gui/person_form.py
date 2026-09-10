"""Shared Person create/edit dialog.

Used both by the persons page itself and by the Web-Registrierungen page
(to prefill a new Person from a reviewed registration without having to
re-type its data) -- see `open_person_form`'s `prefill` argument.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.iban_validation import normalize_iban, validate_iban
from app.gui.safe_notify import safe_notify
from app.models import person as person_repo
from app.models.person import SALUTATION_OPTIONS, Person

#: Person-shaped fields `open_person_form`'s `prefill` dict may set for a
#: new person -- see that function's docstring.
_PREFILL_KEYS = (
    "company",
    "salutation",
    "first_name",
    "last_name",
    "street",
    "house_number",
    "postal_code",
    "city",
    "country",
    "email",
    "phone",
    "iban",
    "bkw_customer_number",
)


def _initial(existing: Optional[Person], attr: str, prefill: dict, key: str, default: str = "") -> str:
    """Resolve one field's initial form value.

    Args:
        existing: Person being edited, or `None` when creating.
        attr: Attribute name on `existing` to read when editing.
        prefill: Prefill dict passed to `open_person_form`.
        key: Key to look up in `prefill` when creating.
        default: Fallback if neither `existing` nor `prefill` has a value.

    Returns:
        The value the corresponding input should start with.
    """
    if existing is not None:
        return getattr(existing, attr)
    return prefill.get(key, default)


def open_person_form(
    *,
    existing: Optional[Person] = None,
    prefill: Optional[dict] = None,
    on_saved: Optional[Callable[[Person], None]] = None,
) -> None:
    """Open the create/edit dialog for a person.

    Args:
        existing: Person to edit, or `None` to create a new one.
        prefill: Initial field values for a new person, ignored if
            `existing` is set. Keys: any of `_PREFILL_KEYS` (`company`,
            `salutation`, `first_name`, `last_name`, `street`, `house_number`,
            `postal_code`, `city`, `country`, `email`, `phone`, `iban`,
            `bkw_customer_number`); missing keys use the usual defaults
            (`country` defaults to `"CH"`).
        on_saved: Called with the created/updated `Person` right after a
            successful save (dialog already closed) -- e.g. so a caller
            elsewhere on the page can refresh its own list or react to
            the new person's id.

    Returns:
        None.
    """
    prefill = prefill or {}

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
        ui.label("Person bearbeiten" if existing else "Neue Person").classes("text-lg font-bold")
        company = ui.input(
            "Firma (optional -- leer lassen für eine Privatperson)",
            value=_initial(existing, "company", prefill, "company"),
        ).classes("w-full")
        ui.label(
            "Vorname/Nachname: der Person selbst, oder der "
            "Ansprechsperson bei einer Firma (kann bei einer reinen "
            "Firmenadresse ohne Ansprechsperson leer bleiben)."
        ).classes("text-caption text-grey-6")
        with ui.row().classes("w-full gap-2"):
            salutation = ui.select(
                ["", *SALUTATION_OPTIONS],
                label="Anrede",
                value=_initial(existing, "salutation", prefill, "salutation"),
            ).classes("w-32")
            first_name = ui.input(
                "Vorname", value=_initial(existing, "first_name", prefill, "first_name")
            ).classes("flex-grow")
            last_name = ui.input(
                "Nachname", value=_initial(existing, "last_name", prefill, "last_name")
            ).classes("flex-grow")
        with ui.row().classes("w-full gap-2"):
            street = ui.input(
                "Adresse: Strasse",
                value=_initial(existing, "billing_street", prefill, "street"),
            ).classes("flex-grow")
            house_number = ui.input(
                "Hausnummer",
                value=_initial(existing, "billing_house_number", prefill, "house_number"),
            ).classes("w-24")
        with ui.row().classes("w-full gap-2"):
            postal_code = ui.input(
                "PLZ", value=_initial(existing, "billing_postal_code", prefill, "postal_code")
            ).classes("w-24")
            city = ui.input(
                "Ort", value=_initial(existing, "billing_city", prefill, "city")
            ).classes("flex-grow")
            country = ui.input(
                "Land", value=_initial(existing, "billing_country", prefill, "country", "CH")
            ).classes("w-24")

        ui.separator().classes("my-2")
        ui.label("Weitere Angaben").classes("text-body1 font-bold")
        with ui.row().classes("w-full gap-2"):
            email = ui.input(
                "E-Mail", value=_initial(existing, "contact_email", prefill, "email")
            ).classes("flex-grow")
            phone = ui.input(
                "Telefon (optional)", value=_initial(existing, "contact_phone", prefill, "phone")
            ).classes("flex-grow")
        with ui.row().classes("w-full gap-2"):
            iban = ui.input(
                "IBAN (für Gutschriften)", value=_initial(existing, "iban", prefill, "iban")
            ).classes("flex-grow")
            bkw_customer_number_prefill = prefill.get("bkw_customer_number")
            bkw_customer_number = ui.number(
                "BKW-Kundennummer (optional)",
                value=existing.bkw_customer_number if existing else bkw_customer_number_prefill,
                format="%.0f",
            ).classes("w-48")
        iban_error = ui.label("").classes("text-negative text-caption")

        def check_iban() -> None:
            """Validate the IBAN once the field loses focus (not on every keystroke).

            Returns:
                None.
            """
            iban_error.text = validate_iban(iban.value) or ""

        iban.on("blur", check_iban)
        paper_invoice = ui.checkbox(
            "Papierrechnung (statt elektronisch, kostenpflichtig)",
            value=existing.paper_invoice if existing else False,
        )
        if existing:
            ui.label(
                f"Kunden-Nr.: {existing.formatted_customer_number} "
                "(automatisch vergeben, nicht änderbar)"
            ).classes("text-caption text-grey-6")
        else:
            ui.label(
                "Die Kunden-Nr. wird beim Speichern automatisch und "
                "zufällig vergeben (keine fortlaufende Nummer, um "
                "Rückschlüsse auf Kundenanzahl oder -reihenfolge zu "
                "verhindern) und ist danach nicht mehr änderbar."
            ).classes("text-caption text-grey-6")
        error_label = ui.label("").classes("text-negative")

        def save() -> None:
            """Validate the form and persist the person.

            Returns:
                None.
            """
            if not company.value.strip() and not (first_name.value.strip() or last_name.value.strip()):
                error_label.text = "Firma oder Vorname/Nachname sind erforderlich."
                return
            iban_problem = validate_iban(iban.value)
            if iban_problem:
                iban_error.text = iban_problem
                error_label.text = iban_problem
                return
            iban_normalized = normalize_iban(iban.value)
            bkw_customer_number_value = (
                int(bkw_customer_number.value) if bkw_customer_number.value is not None else None
            )
            with connection_scope() as connection:
                if existing:
                    saved = Person(
                        id=existing.id,
                        salutation=salutation.value or "",
                        company=company.value.strip(),
                        first_name=first_name.value.strip(),
                        last_name=last_name.value.strip(),
                        contact_email=email.value.strip(),
                        contact_phone=phone.value.strip(),
                        billing_street=street.value.strip(),
                        billing_house_number=house_number.value.strip(),
                        billing_postal_code=postal_code.value.strip(),
                        billing_city=city.value.strip(),
                        billing_country=country.value.strip() or "CH",
                        iban=iban_normalized,
                        customer_number=existing.customer_number,
                        bkw_customer_number=bkw_customer_number_value,
                        paper_invoice=paper_invoice.value,
                        active=existing.active,
                        created_at=existing.created_at,
                    )
                    person_repo.update(connection, saved)
                else:
                    saved = Person(
                        id=None,
                        salutation=salutation.value or "",
                        company=company.value.strip(),
                        first_name=first_name.value.strip(),
                        last_name=last_name.value.strip(),
                        contact_email=email.value.strip(),
                        contact_phone=phone.value.strip(),
                        billing_street=street.value.strip(),
                        billing_house_number=house_number.value.strip(),
                        billing_postal_code=postal_code.value.strip(),
                        billing_city=city.value.strip(),
                        billing_country=country.value.strip() or "CH",
                        iban=iban_normalized,
                        customer_number=None,
                        bkw_customer_number=bkw_customer_number_value,
                        paper_invoice=paper_invoice.value,
                        active=True,
                        created_at="",
                    )
                    new_id = person_repo.create(connection, saved)
                    saved.id = new_id
            dialog.close()
            # Notify before any caller-side refresh(): a caller that shows
            # this dialog from a card-based list (e.g. Web-Registrierungen)
            # may clear/rebuild that list inside on_saved(), which can tear
            # down this dialog's own UI context first -- see app.gui.safe_notify.
            safe_notify("Gespeichert.", type="positive")
            if on_saved:
                on_saved(saved)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    dialog.open()
