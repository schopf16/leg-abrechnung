"""Tests for the guard on every dialog that holds typed-in data.

The defect being fixed cannot be seen by rendering: the dialog looked right
in every test while a click beside it threw seventeen filled-in fields away.
So these drive the keyboard and read the prop that decides it.
"""

import pytest
from nicegui import Client, ui

from app.gui.form_dialog import form_guard


def _listener(element, event_type: str):
    """The handler registered for one event type.

    Args:
        element: The element to look at.
        event_type: e.g. "keydown.escape".

    Returns:
        The handler, callable with one argument.
    """
    for listener in element._event_listeners.values():
        if listener.type == event_type:
            return listener.handler
    raise AssertionError(f"kein Listener fuer {event_type}: {element}")


def _dialog(probe: str, *, with_save: bool = True):
    """A small form dialog with one input and one textarea.

    Args:
        probe: A unique probe route -- every `ui.page` registers itself.
        with_save: Whether Enter should be wired to a save handler.

    Returns:
        `(client, dialog, guard, fields, saved)` where `saved` is a list the
        save handler appends to.
    """
    client = Client(ui.page(probe)(lambda: None), request=None)
    saved: list[bool] = []

    with client:
        with ui.dialog() as dialog, ui.card():
            text = ui.input("Name")
            note = ui.textarea("Bemerkung")
            with ui.input("Adresse") as lookup:
                ui.menu()
        guard = form_guard(dialog, on_save=(lambda: saved.append(True)) if with_save else None)
        dialog.open()

    return client, dialog, guard, {"text": text, "note": note, "lookup": lookup}, saved


def test_a_guarded_dialog_does_not_close_on_a_click_beside_it():
    """The whole point: `persistent` is what stops the accidental loss."""
    _, dialog, _, _, _ = _dialog("/probe-guard-persistent")

    assert dialog._props.get("persistent") is True


def test_escape_closes_a_dialog_nothing_was_typed_into():
    """Making the keyboard work must not make the dialog unclosable."""
    client, dialog, guard, _, _ = _dialog("/probe-guard-escape-clean")

    assert guard.dirty() is False
    with client:
        _listener(dialog, "keydown.escape")(None)

    assert dialog.value is False


def test_escape_asks_before_throwing_typed_input_away():
    """Escape used to be the same gesture as a stray click.

    Now it is the deliberate one, which is exactly why it has to be sure:
    there is no undo and no draft anywhere in this app.
    """
    client, dialog, guard, fields, _ = _dialog("/probe-guard-escape-dirty")

    fields["text"].value = "Muster"
    assert guard.dirty() is True

    with client:
        _listener(dialog, "keydown.escape")(None)

    assert dialog.value is not False, "der Dialog darf sich nicht einfach schliessen"
    assert guard._confirm.value is True, "die Rückfrage muss offen sein"


def test_discarding_from_the_question_closes_both():
    """Otherwise the question stays on screen over a closed form."""
    client, dialog, guard, fields, _ = _dialog("/probe-guard-escape-discard")

    fields["text"].value = "Muster"
    with client:
        _listener(dialog, "keydown.escape")(None)
        discard = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Button" and element.text == "Verwerfen"
        )
        _listener(discard, "click")(None)

    assert guard._confirm.value is False
    assert dialog.value is False


def test_enter_saves_from_a_single_line_field():
    """Point 3 of the list, in the form the administrator asked for."""
    client, _, _, fields, saved = _dialog("/probe-guard-enter")

    with client:
        _listener(fields["text"], "keydown.enter")(None)

    assert saved == [True]


@pytest.mark.parametrize("field", ["note", "lookup"])
def test_enter_belongs_to_the_field_not_the_form(field):
    """In a textarea Enter is a newline; in an address field it is the list.

    `app.gui.address_input` anchors its suggestion menu to the input, so a
    form-wide Enter would save the record instead of taking the suggestion
    the administrator is looking at.
    """
    _, _, _, fields, _ = _dialog("/probe-guard-enter-" + field)

    with pytest.raises(AssertionError):
        _listener(fields[field], "keydown.enter")


def test_a_dialog_without_a_save_handler_binds_no_enter():
    """The override, the invoice mail and the billing run keep the guard.

    They deliberately do not get the key: none of them can be taken back,
    and a stray Enter would be enough to set them off.
    """
    _, _, _, fields, _ = _dialog("/probe-guard-no-enter", with_save=False)

    with pytest.raises(AssertionError):
        _listener(fields["text"], "keydown.enter")


@pytest.mark.parametrize(
    "module, function",
    [
        ("app.gui.person_form", "open_person_form"),
        ("app.gui.site_form", "open_site_form"),
        ("app.gui.metering_point_form", "open_metering_point_form"),
    ],
)
def test_the_real_edit_dialogs_are_guarded(module, function, address_register):
    """The helper being right proves nothing about the forms using it.

    The same reasoning as `tests/test_page_routes.py`: a dialog's own tests
    call the function directly, and the prop that decides whether work can
    be lost is set at the very end of it.
    """
    import importlib

    page_module = importlib.import_module(module)
    probe = "/probe-guard-real-" + function

    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        getattr(page_module, function)()

    dialogs = [element for element in client.elements.values() if element.__class__.__name__ == "Dialog"]
    forms = [element for element in dialogs if element._props.get("persistent")]

    assert forms, f"{function}: kein persistenter Dialog"


# --- What using it exposed -------------------------------------------------


def test_the_question_survives_the_keystroke_that_opened_it():
    """It appeared and vanished in one blink.

    The Escape keydown that opens the question goes on to reach the
    question, and Quasar closes a non-persistent dialog on exactly that
    event. Escape here therefore means "Weiter bearbeiten" -- which is also
    the safe reading of pressing it twice.
    """
    client, dialog, guard, fields, _ = _dialog("/probe-guard-question-stays")

    fields["text"].value = "Muster"
    with client:
        _listener(dialog, "keydown.escape")(None)

    assert guard._confirm._props.get("persistent") is True
    assert guard._confirm.value is True

    with client:
        _listener(guard._confirm, "keydown.escape")(None)

    assert guard._confirm.value is False
    assert dialog.value is True, "zurück zum Bearbeiten, nicht verworfen"


def test_the_buttons_stay_in_view_when_the_dialog_is_taller_than_the_window():
    """The Person dialog is taller than a laptop screen.

    Enter pressed in the middle of it ran the save, got a refusal, and wrote
    the message underneath the last field -- off screen. It read as "Enter
    does nothing", which is the worst possible outcome of adding a key.
    """
    client = Client(ui.page("/probe-guard-sticky")(lambda: None), request=None)
    with client:
        with ui.dialog() as dialog, ui.card():
            ui.input("Name")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Abbrechen", on_click=dialog.close).props("flat")
                ui.button("Speichern")
        form_guard(dialog)

    card = dialog.default_slot.children[0]
    footer = [
        child
        for child in card.default_slot.children
        if child.__class__.__name__ == "Row"
        and any(element.__class__.__name__ == "Button" for element in child.descendants())
    ]

    assert footer, "keine Knopfzeile gefunden"
    assert footer[-1]._style.get("position") == "sticky"


def test_the_person_dialog_shows_its_error_beside_the_save_button(address_register):
    """Driven through the real dialog, because that is where it was wrong."""
    from app.gui.person_form import open_person_form

    client = Client(ui.page("/probe-person-error-position")(lambda: None), request=None)
    with client:
        open_person_form()
        save = next(
            element
            for element in client.elements.values()
            if element.__class__.__name__ == "Button" and element.text == "Speichern"
        )
        # An empty form: neither a company nor a name, which is the refusal
        # the administrator ran into.
        _listener(save, "click")(None)

    row = save.parent_slot.parent
    messages = [
        element.text
        for element in row.descendants()
        if element.__class__.__name__ == "Label" and element.text
    ]

    assert any("erforderlich" in message for message in messages), messages
