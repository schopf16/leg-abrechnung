"""Shared Person create/edit dialog."""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.quality_checks import SUBJECT_PERSON
from app.gui.problem_markers import AT_THE_FIELD, load_problems, render_problem_notes
from app.gui.address_input import SuggestionBox, store_dismissals
from app.domain.email_validation import validate_email
from app.domain.iban_validation import normalize_iban, validate_iban
from app.gui.cooperative_form import CooperativeEditor
from app.gui.form_dialog import form_guard
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

    # Wider than the other forms on purpose: this is the one dialog with
    # five sections, and the width is what lets each row hold its fields
    # side by side instead of stacking them into a dialog taller than the
    # window.
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-3xl"):
        ui.label("Person bearbeiten" if existing else "Neue Person").classes("text-lg font-bold")
        # The findings for this record, except the ones rendered
        # beside their own field further down.
        if existing is not None:
            render_problem_notes(load_problems(SUBJECT_PERSON).get(existing.id), exclude=AT_THE_FIELD)

        ui.label("Name").classes("text-body1 font-bold")
        company = (
            ui.input(
                "Firma (optional -- leer lassen für eine Privatperson)",
                value=_initial(existing, "company", prefill, "company"),
            )
            .classes("w-full")
            .props("autofocus")
        )
        with ui.row().classes("w-full gap-2"):
            salutation = ui.select(
                ["", *SALUTATION_OPTIONS],
                label="Anrede",
                value=_initial(existing, "salutation", prefill, "salutation"),
            ).classes("w-32")
            first_name = ui.input(
                "Vorname", value=_initial(existing, "first_name", prefill, "first_name")
            ).classes("flex-grow")
            first_name.props('hint="Person selbst oder Ansprechsperson der Firma"')
            last_name = ui.input(
                "Nachname", value=_initial(existing, "last_name", prefill, "last_name")
            ).classes("flex-grow")
        ui.separator().classes("my-2")
        ui.label("Rechnungsadresse").classes("text-body1 font-bold")
        with ui.row().classes("w-full gap-2"):
            street = ui.input(
                "Adresse: Strasse",
                value=_initial(existing, "billing_street", prefill, "street"),
            ).classes("flex-grow")
            house_number = ui.input(
                "Hausnummer",
                value=_initial(existing, "billing_house_number", prefill, "house_number"),
            ).classes("w-24")
        street_hint = ui.column().classes("w-full gap-0")
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
        ui.label("Beide Namen auf Anschrift und Anrede, eine Rechnung.").classes("text-caption text-grey-6")
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
        email_error = ui.label("").classes("text-negative text-caption")

        def check_iban() -> None:
            """Validate the IBAN once the field loses focus (not on every keystroke)."""
            iban_error.text = validate_iban(iban.value) or ""

        iban.on("blur", check_iban)

        def check_emails() -> None:
            """Report a certainly-wrong address when a field loses focus."""
            email_error.text = validate_email(email.value) or validate_email(second_email.value) or ""

        email.on("blur", check_emails)
        second_email.on("blur", check_emails)
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
            email_problem = validate_email(email.value) or validate_email(second_email.value)
            if email_problem:
                email_error.text = email_problem
                error_label.text = email_problem
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
