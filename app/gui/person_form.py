"""Shared Person create/edit dialog."""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.quality_checks import SUBJECT_PERSON
from app.gui.problem_markers import AT_THE_FIELD, load_problems, render_problem_notes
from app.gui.address_input import SuggestionBox, store_dismissals
from app.domain.email_validation import validate_email
from app.domain.iban_validation import iban_entry_is_complete, normalize_iban, validate_iban
from app.domain.iban_validation import format_iban
from app.domain.phone_format import format_swiss_phone
from app.domain.message_templates import iban_request
from app.gui.message_buttons import render_step_messages
from app.gui.cooperative_form import CooperativeEditor
from app.gui.form_dialog import form_guard
from app.gui.safe_notify import safe_notify
from app.models import person as person_repo
from app.models.message_template import OCCASION_IBAN_REQUEST
from app.models.person import SALUTATION_OPTIONS, Person


def _copy_customer_number(person: Person) -> None:
    """Copy a person's formatted customer number to the clipboard and confirm."""
    ui.clipboard.write(person.formatted_customer_number)
    safe_notify("Kundennummer kopiert.")


def render_customer_number_row(
    person: Person, *, label: str, classes: str = "text-caption text-grey-6"
) -> None:
    """Render the Kunden-Nr. label with an inline copy-to-clipboard button."""
    with ui.row().classes("items-center gap-1"):
        ui.label(f"{label} {person.formatted_customer_number}".strip()).classes(classes)
        ui.button(icon="content_copy", on_click=lambda: _copy_customer_number(person)).props(
            "dense flat size=sm"
        ).tooltip("Kundennummer kopieren")


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
    """Resolve one field's initial form value."""
    if existing is not None:
        return getattr(existing, attr)
    return prefill.get(key, default)


def open_person_form(
    *,
    existing: Optional[Person] = None,
    prefill: Optional[dict] = None,
    on_saved: Optional[Callable[[Person], None]] = None,
) -> None:
    """Open the create/edit dialog for a person."""
    prefill = prefill or {}

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-5xl"):
        ui.label("Person bearbeiten" if existing else "Neue Person").classes("text-lg font-bold")
        # The findings for this record, except the ones rendered
        # beside their own field further down.
        existing_problems = (
            (load_problems(SUBJECT_PERSON).get(existing.id) or []) if existing is not None else []
        )
        if existing is not None:
            render_problem_notes(existing_problems, exclude=AT_THE_FIELD)

        ui.label("Firma").classes("text-base font-bold mt-2")
        ui.label("Optional, zum Beispiel bei einer Firmenanschrift.").classes("text-caption text-grey-6")
        company = (
            ui.input(
                "Firmenname",
                value=_initial(existing, "company", prefill, "company"),
            )
            .classes("w-full max-w-md")
            .props("autofocus")
        )

        ui.separator().classes("my-2")
        ui.label("Kontaktpersonen").classes("text-base font-bold")
        ui.label("Name und Kontaktdaten sind jeweils bei der Person zusammengefasst.").classes(
            "text-caption text-grey-6"
        )
        with (
            ui.element("div")
            .classes("grid w-full gap-3 mt-2")
            .style("grid-template-columns: repeat(auto-fit, minmax(min(100%, 24rem), 1fr));")
        ):
            with ui.card().classes("w-full h-full"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("1").classes(
                        "w-7 h-7 rounded-full bg-blue-1 text-primary flex items-center justify-center font-bold"
                    )
                    ui.label("Person 1").classes("text-base font-bold")
                    ui.label("Hauptkontakt").classes("text-caption text-grey-6")
                with ui.row().classes("w-full items-start gap-2 mt-2"):
                    salutation = ui.select(
                        ["", *SALUTATION_OPTIONS],
                        label="Anrede",
                        value=_initial(existing, "salutation", prefill, "salutation"),
                    ).classes("w-32")
                    first_name = ui.input(
                        "Vorname", value=_initial(existing, "first_name", prefill, "first_name")
                    ).classes("w-full max-w-56")
                last_name = ui.input(
                    "Nachname", value=_initial(existing, "last_name", prefill, "last_name")
                ).classes("w-full max-w-sm")
                email = ui.input(
                    "E-Mail", value=_initial(existing, "contact_email", prefill, "email")
                ).classes("w-full")
                first_email_error = ui.label("").classes("text-negative text-caption")
                phone = ui.input(
                    "Telefon Person 1 (optional)",
                    value=format_swiss_phone(_initial(existing, "contact_phone", prefill, "phone")),
                ).classes("w-full max-w-xs")

            with ui.card().classes("w-full h-full"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("2").classes(
                        "w-7 h-7 rounded-full bg-blue-1 text-primary flex items-center justify-center font-bold"
                    )
                    ui.label("Person 2").classes("text-base font-bold")
                    ui.label("optional").classes("text-caption text-grey-6")
                with ui.row().classes("w-full items-start gap-2 mt-2"):
                    second_salutation = ui.select(
                        ["", *SALUTATION_OPTIONS],
                        label="Anrede",
                        value=existing.second_salutation if existing else "",
                    ).classes("w-32")
                    second_first_name = ui.input(
                        "Vorname", value=existing.second_first_name if existing else ""
                    ).classes("w-full max-w-56")
                second_last_name = ui.input(
                    "Nachname", value=existing.second_last_name if existing else ""
                ).classes("w-full max-w-sm")
                second_email = ui.input(
                    "E-Mail Person 2 (optional)",
                    value=existing.second_contact_email if existing else "",
                ).classes("w-full")
                second_email_error = ui.label("").classes("text-negative text-caption")
                ui.label(
                    "Gemeinsame Anschrift und Rechnung. Telefonkontakt über Person 1; "
                    "Nachrichten gehen an beide E-Mail-Adressen."
                ).classes("text-caption text-grey-6 mt-2")

        ui.separator().classes("my-2")
        ui.label("Adresse").classes("text-base font-bold")
        ui.label("Gilt für beide Personen und ist zugleich die Rechnungsadresse.").classes(
            "text-caption text-grey-6"
        )
        with ui.row().classes("w-full gap-2"):
            street = ui.input(
                "Strasse",
                value=_initial(existing, "billing_street", prefill, "street"),
            ).classes("w-full max-w-lg")
            house_number = ui.input(
                "Hausnummer",
                value=_initial(existing, "billing_house_number", prefill, "house_number"),
            ).classes("w-28")
        street_hint = ui.column().classes("w-full gap-0")
        with ui.row().classes("w-full gap-2"):
            postal_code = ui.input(
                "PLZ", value=_initial(existing, "billing_postal_code", prefill, "postal_code")
            ).classes("w-28")
            city = ui.input("Ort", value=_initial(existing, "billing_city", prefill, "city")).classes(
                "w-full max-w-sm"
            )
            country = ui.input(
                "Land", value=_initial(existing, "billing_country", prefill, "country", "CH")
            ).classes("w-28")
        locality_hint = ui.column().classes("w-full gap-0")
        # Suggestions on the billing address too. A PO box or a foreign
        # address is legitimate and the register cannot know it, but the
        # administrator decided that catching the many ordinary typos beats
        # ignoring the check over the occasional PO box: one "Nein" retires
        # a PO box for good.
        suggestions = SuggestionBox(
            street,
            postal_code,
            city,
            house_number,
            street_hint=street_hint,
            locality_hint=locality_hint,
        )

        ui.separator().classes("my-2")
        ui.label("Abrechnung").classes("text-base font-bold")
        with ui.row().classes("w-full gap-2"):
            iban = ui.input(
                "IBAN (für Gutschriften)", value=format_iban(_initial(existing, "iban", prefill, "iban"))
            ).classes("w-full max-w-md")
            paper_invoice = ui.checkbox(
                "Papierrechnung (kostenpflichtig)",
                value=existing.paper_invoice if existing else False,
            ).classes("self-center")
        missing_iban_finding = next(
            (warning for warning in existing_problems if warning.category == "feed_in_without_iban"), None
        )
        iban_error = ui.label("IBAN für Gutschriften fehlt" if missing_iban_finding else "").classes(
            "text-warning text-caption"
        )
        if missing_iban_finding:
            iban.props("error")
        if missing_iban_finding and existing:

            @ui.refreshable
            def iban_request_controls() -> None:
                """The IBAN-Anfrage or its date, refreshed in place so typed data survives a send."""
                with connection_scope() as connection:
                    message = iban_request(connection, existing)
                if message:
                    render_step_messages(
                        existing, [message], OCCASION_IBAN_REQUEST, on_changed=iban_request_controls.refresh
                    )

            iban_request_controls()

        ui.separator().classes("my-2")
        ui.label("Mitgliedschaft").classes("text-base font-bold")
        cooperative = CooperativeEditor(
            existing.id if existing else None,
            # A membership deleted or corrected in here is written at once,
            # so the list behind this dialog is stale from that moment --
            # even if the dialog is closed with Abbrechen.
            on_changed=(lambda: on_saved(existing)) if (on_saved and existing) else None,
        )

        ui.separator().classes("my-2")
        ui.label("Weitere Angaben").classes("text-base font-bold")
        with ui.row().classes("w-full gap-2"):
            bkw_customer_number_prefill = prefill.get("bkw_customer_number")
            bkw_customer_number = ui.number(
                "BKW-Kundennummer",
                value=existing.bkw_customer_number if existing else bkw_customer_number_prefill,
                format="%.0f",
            ).classes("w-full max-w-xs")
            bkw_customer_number.props('hint="Optional"')
        note = (
            ui.textarea("Interne Bemerkung (optional)", value=existing.note if existing else "")
            .classes("w-full max-w-2xl")
            .props("rows=3")
        )
        note.props('hint="Erscheint auf keinem Beleg und in keiner E-Mail"')

        def check_iban(finished: bool) -> None:
            """Validate on blur, or while typing once the country's full length is reached."""
            value = iban.value or ""
            if not value.strip():
                iban_error.text = "IBAN für Gutschriften fehlt" if missing_iban_finding else ""
                iban_error.classes(remove="text-negative", add="text-warning")
            elif finished or iban_entry_is_complete(value):
                iban_error.text = validate_iban(value) or ""
                iban_error.classes(remove="text-warning", add="text-negative")
            else:
                iban_error.text = ""
            # A props string "error=False" reaches Vue as the truthy text "False".
            if iban_error.text:
                iban.props("error")
            else:
                iban.props(remove="error")

        iban.on_value_change(lambda _: check_iban(finished=False))
        iban.on("blur", lambda: check_iban(finished=True))
        iban.on("blur", lambda: setattr(iban, "value", format_iban(iban.value or "")))
        phone.on("blur", lambda: setattr(phone, "value", format_swiss_phone(phone.value or "")))

        def check_emails() -> None:
            """Report a certainly-wrong address when a field loses focus."""
            first_email_error.text = validate_email(email.value) or ""
            second_email_error.text = validate_email(second_email.value) or ""

        email.on("blur", check_emails)
        second_email.on("blur", check_emails)
        if existing:
            render_customer_number_row(existing, label="Kunden-Nr. (automatisch vergeben, nicht änderbar):")
        else:
            ui.label("Die Kunden-Nr. wird beim Speichern vergeben.").classes("text-caption text-grey-6")

        def save() -> None:
            """Validate the form and persist the person."""
            if not company.value.strip() and not (first_name.value.strip() or last_name.value.strip()):
                error_label.text = "Firma oder Vorname/Nachname sind erforderlich."
                return
            iban_problem = validate_iban(iban.value)
            if iban_problem:
                iban_error.text = iban_problem
                error_label.text = iban_problem
                return
            # A field nobody clicked into never lost focus, so the blur
            # check alone would let a pasted-in form through.
            first_email_problem = validate_email(email.value)
            second_email_problem = validate_email(second_email.value)
            first_email_error.text = first_email_problem or ""
            second_email_error.text = second_email_problem or ""
            if first_email_problem or second_email_problem:
                error_label.text = first_email_problem or second_email_problem
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
                store_dismissals(connection, suggestions, saved.id, "person")
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

        with ui.row().classes("w-full items-center gap-2 mt-2"):
            error_label = ui.label("").classes("text-negative mr-auto")
            ui.button("Abbrechen", on_click=dialog.close).props("flat")
            ui.button("Speichern", on_click=save)
    form_guard(dialog, on_save=save)
    dialog.open()
