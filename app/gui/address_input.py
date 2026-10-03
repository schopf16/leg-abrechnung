"""The one address field with suggestions, used by every form that has one.

A plain `ui.input` with a list underneath, never a `ui.select`. That is the
whole point: a select forces a choice from its options, and an address that
is not in the register -- a new building, a PO box, a "c/o" line -- has to
stay typeable. **Nothing is written unless a suggestion is clicked**, so the
text the administrator typed survives by construction rather than by care.

One mechanism for all four places that take an address (Standort, Person,
the LEG's own sender address, and the dialogs the Webanmeldungen prefill),
for the same reason `app.gui.sorting` is one mechanism: four
almost-identical autocompletes would drift apart, and the administrator
would meet a different behaviour on each page.

The logic sits in `SuggestionBox` methods rather than inside event
handlers, so a test can call `update()` and `apply()` directly and check
that a click fills every field and that no click leaves the typed text
alone. Rendering a page proves neither (see CLAUDE.md on the nicegui 3.16
upload change).
"""

from pathlib import Path
from typing import Optional

from nicegui import ui

from app.domain.address_lookup import (
    AddressSuggestion,
    suggest_addresses,
    suggest_localities,
)

#: Debounce for the keystroke handler. Long enough that typing a street does
#: not run a query per character, short enough to feel immediate.
_DEBOUNCE_MS = 250


class SuggestionBox:
    """Suggestions for one set of address fields.

    Attributes:
        suggestions: What was offered after the last `update()`. Public so a
            test can assert on it without reaching into NiceGUI elements.
    """

    def __init__(
        self,
        street: ui.input,
        postal_code: ui.input,
        locality: ui.input,
        house_number: Optional[ui.input] = None,
        *,
        path: Optional[Path] = None,
    ) -> None:
        """Wire a list of suggestions under the street field.

        Args:
            street: The street input. Typing here drives the suggestions.
            postal_code: The postal code input, filled on click.
            locality: The locality input, filled on click. Always receives
                the **postal** locality, never the political municipality.
            house_number: The house number input, if the form has one. The
                settings page keeps street and number in one field, so this
                is optional and then the number stays part of `street`.
            path: The register file, or `None` for the configured one.

        Returns:
            None.
        """
        self._street = street
        self._postal_code = postal_code
        self._locality = locality
        self._house_number = house_number
        self._path = path
        self.suggestions: list[AddressSuggestion] = []

        self._list = ui.column().classes("w-full gap-0")
        self._list.visible = False

        street.props(f"debounce={_DEBOUNCE_MS}")
        street.on_value_change(lambda _: self.update())
        for field in (postal_code, locality):
            field.props(f"debounce={_DEBOUNCE_MS}")
            field.on_value_change(lambda _, element=field: self.update_locality(element))

    def update(self) -> None:
        """Recompute the suggestions from what is in the street field.

        Returns:
            None.
        """
        query = (self._street.value or "").strip()
        if self._house_number is not None and (self._house_number.value or "").strip():
            # The number lives in its own field here, so fold it back into
            # the query: typing "4" there should narrow just as typing
            # "Erstweg 4" into one field does.
            query = f"{query} {(self._house_number.value or '').strip()}"
        self.suggestions = suggest_addresses(query, path=self._path)
        self._render()

    def update_locality(self, source: ui.input) -> None:
        """Recompute the suggestions from the postal code or locality field.

        Args:
            source: Whichever of the two fields was typed in.

        Returns:
            None.
        """
        self.suggestions = suggest_localities((source.value or "").strip(), path=self._path)
        self._render()

    def apply(self, suggestion: AddressSuggestion) -> None:
        """Fill the fields from one suggestion.

        A locality-only suggestion leaves the street alone: picking "3048
        Worblaufen" in the postal code field must not wipe a street that is
        already typed.

        Args:
            suggestion: The clicked suggestion.

        Returns:
            None.
        """
        if suggestion.street:
            self._street.value = suggestion.street
            if self._house_number is not None:
                self._house_number.value = suggestion.house_number
            elif suggestion.house_number:
                self._street.value = f"{suggestion.street} {suggestion.house_number}"
        self._postal_code.value = suggestion.postal_code
        self._locality.value = suggestion.locality
        self.suggestions = []
        self._render()

    def _render(self) -> None:
        """Redraw the suggestion list.

        Returns:
            None.
        """
        self._list.clear()
        self._list.visible = bool(self.suggestions)
        with self._list:
            for suggestion in self.suggestions:
                ui.button(
                    suggestion.label or f"{suggestion.postal_code} {suggestion.locality}",
                    on_click=lambda _=None, chosen=suggestion: self.apply(chosen),
                ).props("flat dense align=left no-caps").classes("w-full")
