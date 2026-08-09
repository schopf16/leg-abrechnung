"""Shared Person create/edit dialog.

Used both by the Personen page itself and by the Web-Registrierungen page
(to prefill a new Person from a reviewed registration without having to
re-type its data) -- see `open_person_form`'s `prefill` argument.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.iban_validation import normalize_iban, validate_iban
from app.gui.safe_notify import safe_notify
from app.models import person as person_repo
from app.models.person import ANREDE_OPTIONS, Person

#: Person-shaped fields `open_person_form`'s `prefill` dict may set for a
#: new person -- see that function's docstring.
_PREFILL_KEYS = (
    "firma",
    "anrede",
    "vorname",
    "nachname",
    "strasse",
    "hausnummer",
    "plz",
    "ort",
    "land",
    "email",
    "telefon",
    "iban",
    "bkw_kundennummer",
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
            `existing` is set. Keys: any of `_PREFILL_KEYS` (`firma`,
            `anrede`, `vorname`, `nachname`, `strasse`, `hausnummer`,
            `plz`, `ort`, `land`, `email`, `telefon`, `iban`,
            `bkw_kundennummer`); missing keys use the usual defaults
            (`land` defaults to `"CH"`).
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
        firma = ui.input(
            "Firma (optional -- leer lassen für eine Privatperson)",
            value=_initial(existing, "firma", prefill, "firma"),
        ).classes("w-full")
        ui.label(
            "Vorname/Nachname: der Person selbst, oder der "
            "Ansprechsperson bei einer Firma (kann bei einer reinen "
            "Firmenadresse ohne Ansprechsperson leer bleiben)."
        ).classes("text-caption text-grey-6")
        with ui.row().classes("w-full gap-2"):
            anrede = ui.select(
                ["", *ANREDE_OPTIONS],
                label="Anrede",
                value=_initial(existing, "anrede", prefill, "anrede"),
            ).classes("w-32")
            vorname = ui.input(
                "Vorname", value=_initial(existing, "vorname", prefill, "vorname")
            ).classes("flex-grow")
            nachname = ui.input(
                "Nachname", value=_initial(existing, "nachname", prefill, "nachname")
            ).classes("flex-grow")
        with ui.row().classes("w-full gap-2"):
            strasse = ui.input(
                "Adresse: Strasse",
                value=_initial(existing, "rechnungsadresse_strasse", prefill, "strasse"),
            ).classes("flex-grow")
            hausnummer = ui.input(
                "Hausnummer",
                value=_initial(existing, "rechnungsadresse_hausnummer", prefill, "hausnummer"),
            ).classes("w-24")
        with ui.row().classes("w-full gap-2"):
            plz = ui.input(
                "PLZ", value=_initial(existing, "rechnungsadresse_plz", prefill, "plz")
            ).classes("w-24")
            ort = ui.input(
                "Ort", value=_initial(existing, "rechnungsadresse_ort", prefill, "ort")
            ).classes("flex-grow")
            land = ui.input(
                "Land", value=_initial(existing, "rechnungsadresse_land", prefill, "land", "CH")
            ).classes("w-24")

        ui.separator().classes("my-2")
        ui.label("Weitere Angaben").classes("text-body1 font-bold")
        with ui.row().classes("w-full gap-2"):
            email = ui.input(
                "E-Mail", value=_initial(existing, "kontakt_email", prefill, "email")
            ).classes("flex-grow")
            telefon = ui.input(
                "Telefon (optional)", value=_initial(existing, "kontakt_telefon", prefill, "telefon")
            ).classes("flex-grow")
        with ui.row().classes("w-full gap-2"):
            iban = ui.input(
                "IBAN (für Gutschriften)", value=_initial(existing, "iban", prefill, "iban")
            ).classes("flex-grow")
            bkw_kundennummer_prefill = prefill.get("bkw_kundennummer")
            bkw_kundennummer = ui.number(
                "BKW-Kundennummer (optional)",
                value=existing.bkw_kundennummer if existing else bkw_kundennummer_prefill,
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
        papierrechnung = ui.checkbox(
            "Papierrechnung (statt elektronisch, kostenpflichtig)",
            value=existing.papierrechnung if existing else False,
        )
        if existing:
            ui.label(
                f"Kunden-Nr.: {existing.kundennummer_formatiert} "
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
            if not firma.value.strip() and not (vorname.value.strip() or nachname.value.strip()):
                error_label.text = "Firma oder Vorname/Nachname sind erforderlich."
                return
            iban_problem = validate_iban(iban.value)
            if iban_problem:
                iban_error.text = iban_problem
                error_label.text = iban_problem
                return
            iban_normalized = normalize_iban(iban.value)
            bkw_kundennummer_value = (
                int(bkw_kundennummer.value) if bkw_kundennummer.value is not None else None
            )
            with connection_scope() as connection:
                if existing:
                    saved = Person(
                        id=existing.id,
                        anrede=anrede.value or "",
                        firma=firma.value.strip(),
                        vorname=vorname.value.strip(),
                        nachname=nachname.value.strip(),
                        kontakt_email=email.value.strip(),
                        kontakt_telefon=telefon.value.strip(),
                        rechnungsadresse_strasse=strasse.value.strip(),
                        rechnungsadresse_hausnummer=hausnummer.value.strip(),
                        rechnungsadresse_plz=plz.value.strip(),
                        rechnungsadresse_ort=ort.value.strip(),
                        rechnungsadresse_land=land.value.strip() or "CH",
                        iban=iban_normalized,
                        kundennummer=existing.kundennummer,
                        bkw_kundennummer=bkw_kundennummer_value,
                        papierrechnung=papierrechnung.value,
                        aktiv=existing.aktiv,
                        created_at=existing.created_at,
                    )
                    person_repo.update(connection, saved)
                else:
                    saved = Person(
                        id=None,
                        anrede=anrede.value or "",
                        firma=firma.value.strip(),
                        vorname=vorname.value.strip(),
                        nachname=nachname.value.strip(),
                        kontakt_email=email.value.strip(),
                        kontakt_telefon=telefon.value.strip(),
                        rechnungsadresse_strasse=strasse.value.strip(),
                        rechnungsadresse_hausnummer=hausnummer.value.strip(),
                        rechnungsadresse_plz=plz.value.strip(),
                        rechnungsadresse_ort=ort.value.strip(),
                        rechnungsadresse_land=land.value.strip() or "CH",
                        iban=iban_normalized,
                        kundennummer=None,
                        bkw_kundennummer=bkw_kundennummer_value,
                        papierrechnung=papierrechnung.value,
                        aktiv=True,
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
