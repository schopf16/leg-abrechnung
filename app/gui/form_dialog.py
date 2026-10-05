"""One guard for every dialog that holds typed-in data."""

from typing import Callable, Optional

from nicegui import ui
from nicegui.elements.mixins.value_element import ValueElement

from app.gui.keyboard import KeyboardLayer, push, remove


class FormGuard:
    """Protects one dialog's typed-in data."""

    def __init__(self, dialog: ui.dialog, *, on_save: Optional[Callable[[], None]] = None) -> None:
        """Make the dialog persistent and wire the keyboard up."""
        self.dialog = dialog
        self._on_save = on_save
        self._fields = [element for element in dialog.descendants() if isinstance(element, ValueElement)]
        self._snapshot = self._values()
        #: Set by the first key that would change text. The value snapshot
        #: alone was not enough and the administrator found it on the LEG
        #: dialog: that field carries `debounce=300` for its duplicate
        #: check, so what was typed had not reached the server yet and
        #: Escape closed the dialog without asking. "A key was pressed" is
        #: the thing we actually want to know, and it needs no round trip.
        self._typed = False

        dialog.props("persistent")

        #: What this dialog does with the keys while it is the innermost
        #: thing open. The arrows are deliberately not claimed: in a form
        #: they move the caret, and a list or a question opened on top of
        #: this one takes them over for as long as it is open.
        self._layer = KeyboardLayer(on_escape=self._escape, on_typing=self._note_typing)
        dialog.on_value_change(lambda event: self._follow_the_dialog(bool(event.value)))

        if on_save is not None:
            for field in self._fields:
                if self._enter_belongs_to_the_form(field):
                    field.on("keydown.enter", lambda _: self._save())

        self._confirm = self._build_confirm()
        self._keep_the_footer_in_view()
        # Hung on the dialog so a test can ask any dialog in the app
        # whether it notices a change, rather than each page having to hand
        # its guard out. The LEG dialog closed on Escape without asking
        # because its fields were not in the snapshot, and nothing could
        # see that from outside.
        dialog.form_guard = self

    @staticmethod
    def _enter_belongs_to_the_form(field: ValueElement) -> bool:
        """Whether pressing Enter in this field should save."""
        if field.__class__.__name__ != "Input":
            return False
        if field._props.get("type") == "textarea":
            return False
        # An address field anchors its suggestion list to itself (see
        # `app.gui.address_input`); there Enter is the list's key, not the
        # form's.
        return not any(descendant.__class__.__name__ == "Menu" for descendant in field.descendants())

    def _values(self) -> list:
        """The current value of every field, in a stable order."""
        return [field.value for field in self._fields]

    def _note_typing(self) -> None:
        """Remember that a key was pressed inside this dialog."""
        self._typed = True

    def _follow_the_dialog(self, is_open: bool) -> None:
        """Take the keys while the dialog is open, and give them back after."""
        if is_open:
            push(self._layer)
        else:
            remove(self._layer)

    def dirty(self) -> bool:
        """Whether anything has been typed or picked since the dialog opened."""
        return self._typed or self._values() != self._snapshot

    def _build_confirm(self) -> ui.dialog:
        """The question asked when Escape would discard something."""

        def discard() -> None:
            """Close both the question and the form."""
            self._confirm.close()
            self.dialog.close()

        with ui.dialog() as confirm, ui.card():
            ui.label("Eingaben verwerfen?")
            with ui.row().classes("w-full justify-end gap-2"):
                # Both start flat; the marked one loses `flat` and is drawn
                # filled in its own colour. One filled button among flat
                # ones cannot be mistaken, which an outline could -- this
                # started with a thin ring and the administrator could not
                # see which answer the keys would take.
                keep = ui.button("Weiter bearbeiten", on_click=lambda: self._answer(0)).props(
                    "flat color=primary"
                )
                throw_away = ui.button("Verwerfen", on_click=lambda: self._answer(1), color="negative").props(
                    "flat"
                )
        # Persistent for a reason found by using it: the Escape keystroke
        # that opens this question goes on to reach the question itself,
        # and a non-persistent dialog is closed by Quasar on that same
        # event -- so it appeared and vanished in one blink.
        confirm.props("persistent")

        #: The question is two buttons and no text, so it is the one place
        #: where the arrows have nothing else to do: they move between the
        #: answers and Enter takes the marked one. Escape means "Weiter
        #: bearbeiten", which is also the safe reading of pressing it twice.
        self._answers = [(keep, lambda: confirm.close()), (throw_away, discard)]
        self._marked = 0
        self._confirm_layer = KeyboardLayer(
            on_escape=lambda: self._answer(0),
            on_enter=lambda: self._answer(self._marked),
            on_move=self._move_mark,
        )
        confirm.on_value_change(lambda event: self._follow_the_question(bool(event.value)))
        return confirm

    def _follow_the_question(self, is_open: bool) -> None:
        """Take the keys while the question is open."""
        if is_open:
            self._marked = 0
            self._show_the_mark()
            push(self._confirm_layer)
        else:
            remove(self._confirm_layer)

    def _move_mark(self, step: int) -> None:
        """Move the mark between the two answers."""
        self._marked = (self._marked + step) % len(self._answers)
        self._show_the_mark()

    def _show_the_mark(self) -> None:
        """Draw the mark on the answer the keys would take."""
        for index, (button, _) in enumerate(self._answers):
            if index == self._marked:
                button.props(remove="flat")
                button.style("font-weight: 700;")
            else:
                button.props("flat")
                button.style("font-weight: 400;")

    def _answer(self, index: int) -> None:
        """Take one of the two answers."""
        self._answers[index][1]()

    def _keep_the_footer_in_view(self) -> None:
        """Pin the button row to the bottom of the visible dialog."""
        card = next(iter(self.dialog.default_slot.children), None)
        if card is None:
            return
        footer = None
        for child in card.default_slot.children:
            if child.__class__.__name__ == "Row" and any(
                element.__class__.__name__ == "Button" for element in child.descendants()
            ):
                footer = child
        if footer is None:
            return
        footer.style(
            "position: sticky; bottom: 0; z-index: 2; background: white; "
            "padding-top: 8px; border-top: 1px solid rgba(0,0,0,0.08);"
        )

    def _escape(self) -> None:
        """Close the dialog, asking first if there is something to lose."""
        if not self.dirty():
            self.dialog.close()
            return
        self._confirm.open()

    def _save(self) -> None:
        """Call the dialog's own save handler."""
        if self._on_save is not None:
            self._on_save()


def form_guard(dialog: ui.dialog, *, on_save: Optional[Callable[[], None]] = None) -> FormGuard:
    """Guard one dialog that holds typed-in data."""
    return FormGuard(dialog, on_save=on_save)
