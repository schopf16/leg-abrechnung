"""How many entries a list is showing, and what to do when it shows none."""

from typing import Callable, Optional

from nicegui import ui


def render_count(*, visible: int, total: int, noun: str) -> None:
    """State how many entries are on screen, and of how many."""
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
    """Say the list is empty, and offer the way out if there is one."""
    with ui.column().classes("w-full items-start gap-2 q-pa-md"):
        ui.label(message).classes("text-grey-6")
        if action_label and on_action is not None:
            ui.button(action_label, on_click=lambda: on_action()).props("flat color=primary")
