"""One guard for every dialog that holds typed-in data.

Quasar closes a `q-dialog` when the click lands outside it, and nothing in
this app said otherwise: **none** of the 39 dialogs was `persistent`. The
Person dialog holds seventeen inputs, so a click a few pixels off the card
discarded a filled-in membership without a word -- there is no undo, no
draft, and no notification that anything was lost.

`form_guard` is applied once, after a dialog's body is built and before it
is opened:

```python
with ui.dialog() as dialog, ui.card():
    ...
    with ui.row():
        ui.button("Abbrechen", on_click=dialog.close).props("flat")
        ui.button("Speichern", on_click=save)
form_guard(dialog, on_save=save)
dialog.open()
```

It does three things, and each is the answer to one way of losing work:

- **`persistent`**, so a click beside the card does nothing at all.
- **Escape closes, but asks first when something was typed.** Making the
  keyboard work must not make discarding easier than it was: before this,
  Escape and an outside click were the same gesture, and now Escape is the
  deliberate one -- so it is the one that has to be sure.
- **Enter saves**, from any single-line input. Not from a textarea, where
  Enter is a newline, and not from a field that carries a lookup menu (the
  address fields, `app.gui.address_input`), where Enter belongs to the
  suggestion list rather than to the form.

Dirtiness is measured rather than wired up: the guard snapshots the value of
every `ValueElement` inside the dialog at the moment it is applied, and
compares on Escape. That is why it needs no cooperation from the dialog it
guards -- and also where its limit is. A field created *after* the guard
(a sub-editor that rebuilds itself) is not in the snapshot, so a change made
only there reads as clean and Escape closes without asking. The protection
that matters for an accidental click is `persistent`, which has no such gap.

Read-only dialogs deliberately do **not** get this. An invoice preview or a
detail view holds nothing to lose, and clicking beside it is the fastest way
to dismiss it.
"""

from typing import Callable, Optional

from nicegui import ui
from nicegui.elements.mixins.value_element import ValueElement

from app.gui.keyboard import KeyboardLayer, push, remove


class FormGuard:
    """Protects one dialog's typed-in data.

    Attributes:
        dialog: The guarded dialog.
    """

    def __init__(self, dialog: ui.dialog, *, on_save: Optional[Callable[[], None]] = None) -> None:
        """Make the dialog persistent and wire the keyboard up.

        Args:
            dialog: The dialog, with its body already built.
            on_save: The dialog's save handler, if Enter should call it.

        Returns:
            None.
        """
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
        """Whether pressing Enter in this field should save.

        Args:
            field: One field inside the dialog.

        Returns:
            `True` for a single-line input that owns no lookup menu.
        """
        if field.__class__.__name__ != "Input":
            return False
        if field._props.get("type") == "textarea":
            return False
        # An address field anchors its suggestion list to itself (see
        # `app.gui.address_input`); there Enter is the list's key, not the
        # form's.
        return not any(descendant.__class__.__name__ == "Menu" for descendant in field.descendants())

    def _values(self) -> list:
        """The current value of every field, in a stable order.

        Returns:
            One entry per field.
        """
        return [field.value for field in self._fields]

    def _note_typing(self) -> None:
        """Remember that a key was pressed inside this dialog.

        Returns:
            None.
        """
        self._typed = True

    def _follow_the_dialog(self, is_open: bool) -> None:
        """Take the keys while the dialog is open, and give them back after.

        Args:
            is_open: The dialog's new state.

        Returns:
            None.
        """
        if is_open:
            push(self._layer)
        else:
            remove(self._layer)

    def dirty(self) -> bool:
        """Whether anything has been typed or picked since the dialog opened.

        Returns:
            `True` when a key was pressed or a field's value differs.
        """
        return self._typed or self._values() != self._snapshot

    def _build_confirm(self) -> ui.dialog:
        """The question asked when Escape would discard something.

        Returns:
            The confirmation dialog, closed.
        """

        def discard() -> None:
            """Close both the question and the form."""
            self._confirm.close()
            self.dialog.close()

        with ui.dialog() as confirm, ui.card():
            ui.label("Eingaben verwerfen?")
            with ui.row().classes("w-full justify-end gap-2"):
                keep = ui.button("Weiter bearbeiten", on_click=lambda: self._answer(0)).props("flat")
                throw_away = ui.button("Verwerfen", on_click=lambda: self._answer(1), color="negative")
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
        """Take the keys while the question is open.

        It is pushed above the form's own layer, so Escape answers the
        question rather than closing the form behind it.

        Args:
            is_open: The question's new state.

        Returns:
            None.
        """
        if is_open:
            self._marked = 0
            self._show_the_mark()
            push(self._confirm_layer)
        else:
            remove(self._confirm_layer)

    def _move_mark(self, step: int) -> None:
        """Move the mark between the two answers.

        Args:
            step: `-1` or `+1`.

        Returns:
            None.
        """
        self._marked = (self._marked + step) % len(self._answers)
        self._show_the_mark()

    def _show_the_mark(self) -> None:
        """Draw the mark on the answer the keys would take.

        The same grey bar the drawer marks the open chapter with, rather
        than a third way of saying "this one".

        Returns:
            None.
        """
        for index, (button, _) in enumerate(self._answers):
            if index == self._marked:
                button.style("outline: 2px solid rgba(0,0,0,0.45); outline-offset: 2px;")
            else:
                button.style("outline: none;")

    def _answer(self, index: int) -> None:
        """Take one of the two answers.

        Args:
            index: `0` to keep editing, `1` to discard.

        Returns:
            None.
        """
        self._answers[index][1]()

    def _keep_the_footer_in_view(self) -> None:
        """Pin the button row to the bottom of the visible dialog.

        A dialog with seventeen fields is taller than the window, and the
        administrator pressed Enter in the middle of it: the save handler
        ran, refused, and wrote its message underneath the last field --
        off screen. It read as "Enter does nothing", which is the worst
        possible outcome of adding a key.

        So the row that carries Abbrechen and Speichern (and, where the
        dialog puts it there, the error) sticks to the bottom edge while
        the fields scroll behind it. Nothing in this app needs scrolling
        to reach an action any more.

        Returns:
            None.
        """
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
        """Close the dialog, asking first if there is something to lose.

        Returns:
            None.
        """
        if not self.dirty():
            self.dialog.close()
            return
        self._confirm.open()

    def _save(self) -> None:
        """Call the dialog's own save handler.

        Returns:
            None.
        """
        if self._on_save is not None:
            self._on_save()


def form_guard(dialog: ui.dialog, *, on_save: Optional[Callable[[], None]] = None) -> FormGuard:
    """Guard one dialog that holds typed-in data.

    Args:
        dialog: The dialog, with its body already built.
        on_save: The dialog's save handler, if Enter should call it.

    Returns:
        The guard, so a test can ask it whether the dialog is dirty.
    """
    return FormGuard(dialog, on_save=on_save)
