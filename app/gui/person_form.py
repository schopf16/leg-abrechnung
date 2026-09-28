"""Shared Person create/edit dialog.

Used both by the persons page itself and by the Web-Registrierungen page
(to prefill a new Person from a reviewed registration without having to
re-type its data) -- see `open_person_form`'s `prefill` argument.

The "Zweite Person" block holds a couple's second name and email address on
the same record, because a couple is one customer with one invoice -- see
`app.models.person`. It repeats the first person's field order and widths on
purpose: it is the same thing, entered the same way, not a different kind of
data.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.iban_validation import normalize_iban, validate_iban
from app.gui.cooperative_form import CooperativeEditor
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
            city = ui.input("Ort", value=_initial(existing, "billing_city", prefill, "city")).classes(
                "flex-grow"
            )
            country = ui.input(
                "Land", value=_initial(existing, "billing_country", prefill, "country", "CH")
            ).classes("w-24")

        ui.separator().classes("my-2")
        # Read-only on purpose, but shown here because this dialog is where
        # the administrator looks for every other fact about a person -- and
        # did look, without finding it. A membership is a sequence of dated
        # periods, not a field, so it is maintained on the detail page
        # (see `app.gui.pages.persons._render_cooperative_section`); what
        # belongs here is the answer to "is this person a member", plus
        # where to change it.
        ui.label("Genossenschaft").classes("text-body1 font-bold")
        cooperative = CooperativeEditor(
            existing.id if existing else None,
            # A membership deleted or corrected in here is written at once,
            # so the list behind this dialog is stale from that moment --
            # even if the dialog is closed with Abbrechen.
            on_changed=(lambda: on_saved(existing)) if (on_saved and existing) else None,
        )

        ui.separator().classes("my-2")
        ui.label("Zweite Person (optional)").classes("text-body1 font-bold")
        ui.label(
            "Für ein Paar oder eine Partnerschaft: beide Namen stehen auf "
            "der Anschrift und in der Anrede, abgerechnet wird weiterhin "
            "einmal -- ein Kunde, ein Beleg."
        ).classes("text-caption text-grey-6")
        with ui.row().classes("w-full gap-2"):
            second_salutation = ui.select(
                ["", *SALUTATION_OPTIONS],
                label="Anrede",
                value=existing.second_salutation if existing else "",
            ).classes("w-32")
            second_first_name = ui.input(
                "Vorname", value=existing.second_first_name if existing else ""
            ).classes("flex-grow")
            second_last_name = ui.input(
                "Nachname", value=existing.second_last_name if existing else ""
            ).classes("flex-grow")
        second_email = ui.input(
            "E-Mail der zweiten Person (optional)",
            value=existing.second_contact_email if existing else "",
        ).classes("w-full")
        second_email.props('hint="Erhält jede Nachricht zusammen mit der ersten Adresse"')

        ui.separator().classes("my-2")
        ui.label("Weitere Angaben").classes("text-body1 font-bold")
        with ui.row().classes("w-full gap-2"):
            email = ui.input("E-Mail", value=_initial(existing, "contact_email", prefill, "email")).classes(
                "flex-grow"
            )
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
        note = (
            ui.textarea("Bemerkung (optional)", value=existing.note if existing else "")
            .classes("w-full")
            .props("rows=3")
        )
        note.props('hint="Nur intern -- erscheint auf keinem Beleg und in keiner E-Mail"')
        if existing:
            ui.label(
                f"Kunden-Nr.: {existing.formatted_customer_number} (automatisch vergeben, nicht änderbar)"
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
            cooperative_problem = cooperative.validate()
            if cooperative_problem:
                error_label.text = cooperative_problem
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
                        deactivated_at=existing.deactivated_at,
                        note=note.value.strip(),
                        second_salutation=second_salutation.value or "",
                        second_first_name=second_first_name.value.strip(),
                        second_last_name=second_last_name.value.strip(),
                        second_contact_email=second_email.value.strip(),
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
                        note=note.value.strip(),
                        second_salutation=second_salutation.value or "",
                        second_first_name=second_first_name.value.strip(),
                        second_last_name=second_last_name.value.strip(),
                        second_contact_email=second_email.value.strip(),
                    )
                    new_id = person_repo.create(connection, saved)
                    saved.id = new_id
            # Applied after the person is saved, because a membership needs
            # a person to hang on -- for a new one the id only exists now.
            cooperative_warnings: list[str] = []
            cooperative.apply(saved.id, on_warning=cooperative_warnings.append)

            dialog.close()
            # Notify before any caller-side refresh(): a caller that shows
            # this dialog from a card-based list (e.g. Web-Registrierungen)
            # may clear/rebuild that list inside on_saved(), which can tear
            # down this dialog's own UI context first -- see app.gui.safe_notify.
            safe_notify("Gespeichert.", type="positive")
            for message in cooperative_warnings:
                safe_notify(message, type="warning")
            if on_saved:
                on_saved(saved)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    dialog.open()
