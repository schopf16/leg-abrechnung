"""How many entries a list is showing, and what to do when it shows none.

Two small things that were inconsistent in opposite directions.

**The count.** A table says it by itself -- Quasar's footer prints
"1-50 von 92" -- so the lists that are tables needed nothing. The card lists
said nothing at all: the Debitoren page could be filtered down to nine people
out of ninety-two with no indication that it had been. `render_count` gives
them the same sentence a table's footer gives.

**The empty state.** "Keine passenden Aufnahmen." is a statement, and the
reader's next question is what to do about it. Sometimes that is "clear the
filter", sometimes "create the first one", and sometimes nothing at all -- an
empty Mahnwesen worklist is good news and needs no suggestion. So the action
is optional and never invented: a page says what its way out is, or says
nothing.
"""

from typing import Callable, Optional

from nicegui import ui


def render_count(*, visible: int, total: int, noun: str) -> None:
    """State how many entries are on screen, and of how many.

    Args:
        visible: Entries the filter left standing.
        total: Entries the list holds in all.
        noun: German plural, e.g. "Personen".

    Returns:
        None.
    """
    if visible == total:
        ui.label(f"{total} {noun}").classes("text-caption text-grey-6")
        return
    ui.label(f"{visible} von {total} {noun}").classes("text-caption text-grey-6")


def render_empty(
    message: str,
    *,
    action_label: Optional[str] = None,
    on_action: Optional[Callable[[], None]] = None,
) -> None:
    """Say the list is empty, and offer the way out if there is one.

    Args:
        message: What is empty, in one sentence.
        action_label: Button text, e.g. "Filter zurücksetzen".
        on_action: What the button does. Both or neither.

    Returns:
        None.
    """
    with ui.column().classes("w-full items-start gap-2 q-pa-md"):
        ui.label(message).classes("text-grey-6")
        if action_label and on_action is not None:
            ui.button(action_label, on_click=lambda: on_action()).props("flat color=primary")
