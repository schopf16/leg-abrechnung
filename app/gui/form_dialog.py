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

        dialog.props("persistent")
        # With `persistent`, Quasar stops handling Escape itself, so the
        # dialog would become unclosable by keyboard without this.
        dialog.on("keydown.escape", lambda _: self._escape())

        if on_save is not None:
            for field in self._fields:
                if self._enter_belongs_to_the_form(field):
                    field.on("keydown.enter", lambda _: self._save())

        self._confirm = self._build_confirm()
        self._keep_the_footer_in_view()

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

    def dirty(self) -> bool:
        """Whether anything has been typed or picked since the dialog opened.

        Returns:
            `True` when at least one field's value differs.
        """
        return self._values() != self._snapshot

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
                ui.button("Weiter bearbeiten", on_click=lambda: confirm.close()).props("flat")
                ui.button("Verwerfen", on_click=discard, color="negative")
        # Persistent for a reason found by using it: the Escape keystroke
        # that opens this question goes on to reach the question itself,
        # and a non-persistent dialog is closed by Quasar on that same
        # event -- so it appeared and vanished in one blink. Escape here
        # therefore means "Weiter bearbeiten", which is also the safe
        # reading of pressing it twice.
        confirm.props("persistent")
        confirm.on("keydown.escape", lambda _: confirm.close())
        return confirm

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
