"""The one problem marker and filter used by every list in the app."""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.quality_checks import QualityWarning, problems_for

#: Label of the filter. Not "fehlerhafte Adressen": the same control now
#: covers missing LEGs, stuck onboardings and everything else a check can
#: find, so it is named after what it shows.
FILTER_LABEL = "Nur fehlerhafte Einträge"


def load_problems(subject_kind: str) -> dict[int, list[QualityWarning]]:
    """Collect the findings for one list, opening its own connection."""
    with connection_scope() as connection:
        return problems_for(connection, subject_kind)


#: The marker, for a Quasar table's action slot. There used to be a second
#: rendering as Python elements (`render_marker`), for when the lists that
#: carry markers were cards. All five are tables now -- see CLAUDE.md on
#: tables and paging -- so that one had no caller left and is gone rather
#: than kept warm: an unused second rendering of the same thing is exactly
#: what drifts.
TABLE_MARKER_HTML = (
    '<q-icon v-if="props.row.has_problem" name="warning" color="warning" size="1.715em" class="q-px-sm" />'
)


class ProblemFilter:
    """The "Nur fehlerhafte Einträge" switch for one list."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        """Render the switch, hidden until something is marked."""
        # Quasar wires up the `label` *prop*; text in the default slot
        # renders beside the switch and does nothing when clicked.
        self.switch = ui.switch().props(f'label="{FILTER_LABEL}" dense')
        self.switch.visible = False
        self.switch.on_value_change(lambda _: on_change())

    @property
    def active(self) -> bool:
        """Whether the list should currently show only marked entries."""
        return bool(self.switch.value)

    def update(self, marked: set[int]) -> None:
        """Show or hide the switch for the current set of findings."""
        self.switch.visible = bool(marked)
        if not marked:
            # Correcting the last entry must not leave the list filtered down
            # to nothing by a control that is no longer on screen.
            self.switch.value = False


#: Findings an edit dialog already shows at the field itself. Repeating them
#: in the block at the top would say the same thing twice, once far from the
#: input it is about.
AT_THE_FIELD = frozenset({"address_not_official"})


def render_problem_notes(
    warnings: Optional[list[QualityWarning]],
    *,
    exclude: frozenset = frozenset(),
) -> None:
    """Spell the findings out, for a detail page or an edit dialog."""
    warnings = [w for w in (warnings or []) if w.category not in exclude]
    if not warnings:
        return
    with ui.card().classes("w-full bg-orange-1"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("warning", color="warning", size="1.715em")
            ui.label("Zu prüfen").classes("text-body1 font-bold")
        for warning in warnings:
            ui.label(warning.message).classes("text-body2")
