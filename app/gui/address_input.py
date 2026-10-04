"""The one address field with suggestions and hints, for every form.

A plain `ui.input` with a list underneath, never a `ui.select`. That is the
point: a select forces a choice from its options, and an address the
register does not know -- a new building, a PO box, a "c/o" line -- has to
stay typeable. **Nothing is written unless a suggestion is clicked**, so the
typed text survives by construction rather than by care.

The "Meinten Sie: X?" hint belongs **at the field**, and the first build got
that wrong. It put the questions in a card above the Standorte and Personen
lists, where each line read as a name and a suggestion with no sight of
which field was meant or what stood in it -- unanswerable. The lists now
only mark a record; the question is asked here, directly under the row
holding the value it would replace.

One mechanism for all four places that take an address (Standort, Person,
the LEG's own sender address, and the dialogs the Webanmeldungen prefill),
for the same reason `app.gui.sorting` is one mechanism: four
almost-identical autocompletes would drift apart and the administrator would
meet a different behaviour on each page.

The logic sits in methods rather than inside event handlers so a test can
call `update()`, `apply()` and `dismiss()` directly. Rendering a page proves
none of it (see CLAUDE.md on the nicegui 3.16 upload change).
"""

from pathlib import Path
from typing import Optional

from nicegui import ui

from app.domain.address_check import address_signature
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
    """Suggestions and hints for one set of address fields.

    Attributes:
        suggestions: What was offered after the last `update()`.
        dismissals: `{DISMISS_*: confirmed value}` collected from "Nein"
            clicks. The form writes them after saving the record -- a new
            record has no id to attach them to while the dialog is open.
    """

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
        """Wire suggestions and hints onto a form's address fields.

        Args:
            street: The street input. Typing here drives the suggestions.
            postal_code: The postal code input, filled on click.
            locality: The locality input, filled on click. Always receives
                the **postal** locality, never the political municipality.
            house_number: The house number input, if the form has one. The
                settings page keeps street and number in one field.
            street_hint: Container directly under the street row, for
                findings about the street or house number. Without it those
                findings are not shown -- a hint far from its field is what
                made the first version unanswerable.
            locality_hint: Container under the postal code and locality row.
            path: The register file, or `None` for the configured one.

        Returns:
            None.
        """
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
        for field in (street, postal_code, locality):
            with field:
                menu = ui.menu().props("no-focus no-refocus fit auto-close=false")
            self._menus[field] = menu
            # Quasar only reacts to Escape while the menu holds focus, and
            # it deliberately does not take focus here, so the key is bound
            # on the field the administrator is actually typing in.
            field.on("keydown.esc", lambda _=None: self.hide())
            # The list floats and takes no focus (`no-focus`), so the keys
            # that walk it have to be bound on the field being typed in --
            # the same reason Escape is bound here. Without this the list
            # could only be used with the mouse: it opened, and neither the
            # arrows nor Enter did anything.
            field.on("keydown.down", lambda _=None: self._move(1))
            field.on("keydown.up", lambda _=None: self._move(-1))
            field.on("keydown.enter", lambda _=None: self._take_highlighted())

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
        """Recompute the suggestions from the street and number fields.

        Returns:
            None.
        """
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
        """Recompute the suggestions from the postal code or locality field.

        Args:
            source: Whichever of the two fields was typed in.

        Returns:
            None.
        """
        self.suggestions = suggest_localities((source.value or "").strip(), path=self._path)
        self._active = source
        self._highlight = -1
        self._render_list()
        self.refresh_hints()

    def _move(self, step: int) -> None:
        """Walk the open list by one entry.

        Args:
            step: `1` for down, `-1` for up.

        Returns:
            None.
        """
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
        """Apply whichever suggestion the arrows have reached.

        Does nothing while none is highlighted. That is the point: the
        street suggestions can be a correctly spelled *different* street, so
        Enter only ever takes something that has been stepped onto and read.

        Returns:
            None.
        """
        if 0 <= self._highlight < len(self.suggestions):
            self.apply(self.suggestions[self._highlight])

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
        self._highlight = -1
        self._render_list()
        self.refresh_hints()

    def hide(self) -> None:
        """Dismiss the suggestion list without changing anything.

        Bound to Escape, so a list that is in the way can be pushed aside
        while the typed text stays exactly as it is.

        Returns:
            None.
        """
        self.suggestions = []
        self._render_list()

    # -- hints --------------------------------------------------------------

    def findings(self) -> list[AddressFinding]:
        """Check the fields as they currently stand.

        Returns:
            What the register disagrees with, minus anything dismissed in
            this dialog. Empty without a register.
        """
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
        """Which confirmation a "Nein" on this finding would write.

        Args:
            finding: The finding.

        Returns:
            `(key, value)` for `dismissals`. The locality is confirmed by
            its own text; everything else by the whole address, so that
            correcting the house number does not leave a dismissal meant for
            the old one in place.
        """
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
        """Write the register's value into the field this finding is about.

        Args:
            finding: The accepted finding.

        Returns:
            None.
        """
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
        """Record that the current text is intended.

        Args:
            finding: The declined finding.

        Returns:
            None.
        """
        key, value = self._dismissal_for(finding)
        self.dismissals[key] = value
        self.refresh_hints()

    def refresh_hints(self) -> None:
        """Redraw the hints under their own fields.

        Returns:
            None.
        """
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
        """Redraw the floating list under the field being typed in.

        Returns:
            None.
        """
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
    """Persist the "Nein" decisions a dialog collected.

    Called after saving rather than as part of it: a new record has no id
    while the dialog is open, and `update` deliberately leaves the
    confirmation columns alone so that editing an unrelated field cannot
    clear a dismissal.

    One copy here rather than one per form, for the reason `app.gui.sorting`
    is one module: two would drift.

    Args:
        connection: Open SQLite connection.
        box: The dialog's suggestion box.
        record_id: The saved record.
        kind: `"site"` or `"person"`.

    Returns:
        None.
    """
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
