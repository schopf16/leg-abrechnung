"""The one address field with suggestions and hints, for every form."""

from pathlib import Path
from typing import Optional

from nicegui import ui

from app.domain.address_check import address_signature
from app.gui.keyboard import KeyboardLayer, push, remove
from app.domain.address_lookup import (
    FIELD_HOUSE_NUMBER,
    FIELD_LOCALITY,
    FIELD_POSTAL_CODE,
    FIELD_STREET,
    AddressFinding,
    AddressSuggestion,
    suggest_addresses,
    suggest_localities,
    verify,
)

#: No debounce on the address fields, set explicitly because the Standort
#: dialog carries one of its own for the duplicate check. Typing a street
#: quickly and then stopping left the list empty until another key was
#: pressed seconds later, and two stacked delays is not a thing worth
#: reasoning about -- the lookup is an indexed prefix query over 197'000
#: streets, so there is nothing to spare the machine.
_DEBOUNCE_MS = 0

#: Keys of `SuggestionBox.dismissals`, matching the two confirmation columns
#: migration 51 added to `site` and `person`.
DISMISS_ADDRESS = "address"
DISMISS_LOCALITY = "locality"


class SuggestionBox:
    """Suggestions and hints for one set of address fields."""

    def __init__(
        self,
        street: ui.input,
        postal_code: ui.input,
        locality: ui.input,
        house_number: Optional[ui.input] = None,
        *,
        street_hint: Optional[ui.element] = None,
        locality_hint: Optional[ui.element] = None,
        path: Optional[Path] = None,
    ) -> None:
        """Wire suggestions and hints onto a form's address fields."""
        self._street = street
        self._postal_code = postal_code
        self._locality = locality
        self._house_number = house_number
        self._street_hint = street_hint
        self._locality_hint = locality_hint
        self._path = path
        self.suggestions: list[AddressSuggestion] = []
        self.dismissals: dict[str, str] = {}

        # A menu per field that can offer something, anchored to that field.
        # An inline list under the form resized the dialog on every
        # keystroke -- distracting at exactly the moment the administrator
        # is reading what they are typing -- so the list floats over the
        # form instead and Escape dismisses it.
        self._menus: dict[ui.input, ui.menu] = {}
        self._active: Optional[ui.input] = None
        #: Which suggestion the arrow keys have moved to, -1 for none.
        #: Deliberately starts at none and stays there until an arrow is
        #: pressed: Enter must never take a suggestion the administrator has
        #: not looked at. A street suggestion can be a *different* real
        #: street (see CLAUDE.md on `FIELD_POSTAL_CODE`), so a blind Enter
        #: taking the first entry is exactly the destructive case this
        #: project already fixed once.
        self._highlight = -1
        #: The list is the innermost thing open while it is showing, so it
        #: takes the keys: the arrows walk it, Enter takes the marked entry
        #: and Escape pushes it aside. Pushed above the dialog's own layer,
        #: so Escape dismisses the list first and only closes the form on
        #: the second press.
        self._layer = KeyboardLayer(
            on_escape=self.hide,
            on_enter=self._take_highlighted,
            on_move=self._move,
        )
        for field in (street, postal_code, locality):
            with field:
                menu = ui.menu().props("no-focus no-refocus fit auto-close=false")
            self._menus[field] = menu
            # Quasar only reacts to Escape while the menu holds focus, and
            # it deliberately does not take focus here, so the key is bound
            # on the field the administrator is actually typing in.
            # No keys are bound here. The list floats with `no-focus` and
            # a Quasar dialog renders its card in a portal, so an element
            # binding depends on both the focus and the event bubbling out
            # -- which is how Escape came to work in one dialog and not the
            # next. `app.gui.keyboard` owns the keys instead.

        for field in (street, postal_code, locality, house_number):
            if field is not None:
                field.props(f"debounce={_DEBOUNCE_MS}")
        # The Standort dialog sets debounce=300 on these for its duplicate
        # check; the line above overrides it, which is deliberate. That
        # check reads 92 rows and does not need the delay either.
        street.on_value_change(lambda _: self.update())
        if house_number is not None:
            house_number.on_value_change(lambda _: self.update())
        for field in (postal_code, locality):
            field.on_value_change(lambda _, element=field: self.update_locality(element))

        self.refresh_hints()

    # -- suggestions --------------------------------------------------------

    def update(self) -> None:
        """Recompute the suggestions from the street and number fields."""
        query = (self._street.value or "").strip()
        if self._house_number is not None and (self._house_number.value or "").strip():
            # The number lives in its own field here, so fold it back into
            # the query: typing "4" there should narrow just as typing
            # "Erstweg 4" into one field does.
            query = f"{query} {(self._house_number.value or '').strip()}"
        # The postal code already in the form ranks the matches: without it,
        # typing a street with "3063 Ittigen" filled in offered six streets
        # from other cantons above the one that fits.
        self.suggestions = suggest_addresses(
            query, path=self._path, postal_code=(self._postal_code.value or "").strip()
        )
        self._active = self._street
        self._highlight = -1
        self._render_list()
        self.refresh_hints()

    def update_locality(self, source: ui.input) -> None:
        """Recompute the suggestions from the postal code or locality field."""
        self.suggestions = suggest_localities((source.value or "").strip(), path=self._path)
        self._active = source
        self._highlight = -1
        self._render_list()
        self.refresh_hints()

    def _move(self, step: int) -> None:
        """Walk the open list by one entry."""
        if not self.suggestions:
            return
        if self._highlight < 0:
            # The first arrow press lands on the first entry going down and
            # on the last going up, rather than on entry 0 either way.
            self._highlight = 0 if step > 0 else len(self.suggestions) - 1
        else:
            self._highlight = (self._highlight + step) % len(self.suggestions)
        self._render_list()

    def _take_highlighted(self) -> None:
        """Apply whichever suggestion the arrows have reached."""
        if 0 <= self._highlight < len(self.suggestions):
            self.apply(self.suggestions[self._highlight])

    def apply(self, suggestion: AddressSuggestion) -> None:
        """Fill the fields from one suggestion."""
        if suggestion.street:
            self._street.value = suggestion.street
            if self._house_number is not None:
                self._house_number.value = suggestion.house_number
            elif suggestion.house_number:
                self._street.value = f"{suggestion.street} {suggestion.house_number}"
        self._postal_code.value = suggestion.postal_code
        self._locality.value = suggestion.locality
        self.suggestions = []
        self._highlight = -1
        self._render_list()
        self.refresh_hints()

    def hide(self) -> None:
        """Dismiss the suggestion list without changing anything."""
        self.suggestions = []
        self._render_list()

    # -- hints --------------------------------------------------------------

    def findings(self) -> list[AddressFinding]:
        """Check the fields as they currently stand."""
        number = (self._house_number.value or "").strip() if self._house_number else ""
        open_findings = []
        for finding in verify(
            (self._street.value or "").strip(),
            number,
            (self._postal_code.value or "").strip(),
            (self._locality.value or "").strip(),
            path=self._path,
        ):
            key, value = self._dismissal_for(finding)
            if self.dismissals.get(key) == value:
                continue
            open_findings.append(finding)
        return open_findings

    def _dismissal_for(self, finding: AddressFinding) -> tuple[str, str]:
        """Which confirmation a "Nein" on this finding would write."""
        if finding.field == FIELD_LOCALITY:
            return (DISMISS_LOCALITY, (self._locality.value or "").strip())
        number = (self._house_number.value or "").strip() if self._house_number else ""
        return (
            DISMISS_ADDRESS,
            address_signature(
                (self._street.value or "").strip(),
                number,
                (self._postal_code.value or "").strip(),
            ),
        )

    def accept(self, finding: AddressFinding) -> None:
        """Write the register's value into the field this finding is about."""
        if not finding.suggestion:
            return
        # An explicit mapping, never "everything that is not the locality is
        # the street". That shape put a postal-code suggestion into the
        # street field once, and the if/elif that replaced it did the same
        # to a house number: correcting "4a" to "4" overwrote the street
        # with "4" and left the number wrong. A field this does not know is
        # left alone rather than written somewhere plausible.
        target = {
            FIELD_STREET: self._street,
            # No fallback to the street: the settings page keeps street and
            # number in one field, so writing a number suggestion there
            # would replace "Strasse 4" with "4". Unreachable today --
            # `findings` passes an empty number when there is no field, and
            # `verify` skips the check for an empty number -- and left
            # unreachable rather than given a plausible-looking fallback.
            FIELD_HOUSE_NUMBER: self._house_number,
            FIELD_POSTAL_CODE: self._postal_code,
            FIELD_LOCALITY: self._locality,
        }.get(finding.field)
        if target is None:
            return
        target.value = finding.suggestion
        self.refresh_hints()

    def dismiss(self, finding: AddressFinding) -> None:
        """Record that the current text is intended."""
        key, value = self._dismissal_for(finding)
        self.dismissals[key] = value
        self.refresh_hints()

    def refresh_hints(self) -> None:
        """Redraw the hints under their own fields."""
        if self._street_hint is None and self._locality_hint is None:
            return
        for container in (self._street_hint, self._locality_hint):
            if container is not None:
                container.clear()

        for finding in self.findings():
            near_locality = finding.field in (FIELD_LOCALITY, FIELD_POSTAL_CODE)
            container = self._locality_hint if near_locality else self._street_hint
            if container is None:
                continue
            with container, ui.row().classes("items-center gap-2"):
                question = (
                    f"Meinten Sie: {finding.suggestion}?"
                    if finding.suggestion
                    else "Nicht im amtlichen Verzeichnis."
                )
                ui.label(question).classes("text-caption text-warning")
                if finding.suggestion:
                    ui.button("Ja", on_click=lambda _=None, chosen=finding: self.accept(chosen)).props(
                        "flat dense no-caps"
                    )
                ui.button("Nein", on_click=lambda _=None, chosen=finding: self.dismiss(chosen)).props(
                    "flat dense no-caps"
                )

    # -- the suggestion list ------------------------------------------------

    def _render_list(self) -> None:
        """Redraw the floating list under the field being typed in."""
        if self.suggestions:
            push(self._layer)
        else:
            remove(self._layer)

        for field, menu in self._menus.items():
            if field is not self._active or not self.suggestions:
                menu.close()
                continue
            menu.clear()
            with menu, ui.column().classes("gap-0 p-0"):
                for index, suggestion in enumerate(self.suggestions):
                    button = (
                        ui.button(
                            suggestion.label or f"{suggestion.postal_code} {suggestion.locality}",
                            on_click=lambda _=None, chosen=suggestion: self.apply(chosen),
                        )
                        .props("flat dense align=left no-caps")
                        .classes("w-full")
                    )
                    if index == self._highlight:
                        # The same grey bar the drawer marks the open entry
                        # with, for the same reason: one mark, read at a
                        # glance, not a second colour to learn.
                        button.style("background: rgba(0,0,0,0.10);")
            menu.open()


def store_dismissals(connection, box: SuggestionBox, record_id: int, kind: str) -> None:
    """Persist the "Nein" decisions a dialog collected."""
    from app.models import person as person_repo
    from app.models import site as site_repo

    writers = (
        {
            DISMISS_ADDRESS: site_repo.confirm_address,
            DISMISS_LOCALITY: site_repo.confirm_locality,
        }
        if kind == "site"
        else {
            DISMISS_ADDRESS: person_repo.confirm_billing_address,
            DISMISS_LOCALITY: person_repo.confirm_billing_city,
        }
    )
    for key, value in box.dismissals.items():
        writers[key](connection, record_id, value)
