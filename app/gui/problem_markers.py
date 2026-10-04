"""The one problem marker and filter used by every list in the app.

Same shape as `app.gui.sorting`, and for the same reason: a list that marks
its broken entries differently from the next list is a second mechanism to
learn. There is one marker, one filter, and one place that decides what
counts as a problem (`app.domain.quality_checks.problems_for`).

**The triangle carries no text.** It says "look at this one" and nothing
more, exactly like the eye and the pencil beside it. The eye then shows what
is wrong, at the section it belongs to, and the pencil shows the same thing
and lets it be fixed. A table row has no room to explain a finding, and the
first attempt at explaining in the list proved it: a name beside "Meinten
Sie: Untere Zollgasse?" said neither which field was meant nor what stood in
it.

**The filter is hidden while nothing is marked.** A control that can only
ever empty the list is clutter, and one left switched on after the last
correction would filter the list down to nothing from off-screen -- so it
also switches itself off when the last finding goes.

This closes a gap the overview's summarising opened: it says "7 Messpunkte
ohne LEG" and links to the list, where before it named every one of them.
Without a marker in that list the reader arrives and cannot tell which.
"""

from typing import Callable, Optional

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.quality_checks import QualityWarning, problems_for

#: Label of the filter. Not "fehlerhafte Adressen": the same control now
#: covers missing LEGs, stuck onboardings and everything else a check can
#: find, so it is named after what it shows.
FILTER_LABEL = "Nur fehlerhafte Einträge"


def load_problems(subject_kind: str) -> dict[int, list[QualityWarning]]:
    """Collect the findings for one list, opening its own connection.

    Args:
        subject_kind: One of `app.domain.quality_checks`'s `SUBJECT_*`.

    Returns:
        `{record id: findings}`, holding only the records that have one.
    """
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
    """The "Nur fehlerhafte Einträge" switch for one list.

    Attributes:
        switch: The rendered switch, so a test can drive it.
    """

    def __init__(self, on_change: Callable[[], None]) -> None:
        """Render the switch, hidden until something is marked.

        Args:
            on_change: The page's filter refresh, called when toggled.

        Returns:
            None.
        """
        # Quasar wires up the `label` *prop*; text in the default slot
        # renders beside the switch and does nothing when clicked.
        self.switch = ui.switch().props(f'label="{FILTER_LABEL}" dense')
        self.switch.visible = False
        self.switch.on_value_change(lambda _: on_change())

    @property
    def active(self) -> bool:
        """Whether the list should currently show only marked entries.

        Returns:
            `True` while the switch is on.
        """
        return bool(self.switch.value)

    def update(self, marked: set[int]) -> None:
        """Show or hide the switch for the current set of findings.

        Args:
            marked: Ids that carry a finding right now.

        Returns:
            None.
        """
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
    """Spell the findings out, for a detail page or an edit dialog.

    This is where the text belongs: the eye shows it, the pencil shows it
    and lets it be fixed. Nothing is drawn when there is nothing to say.

    Args:
        warnings: The findings for this record, or `None`.
        exclude: Categories to leave out -- a dialog passes `AT_THE_FIELD`
            because it renders those beside the input they are about.

    Returns:
        None.
    """
    warnings = [w for w in (warnings or []) if w.category not in exclude]
    if not warnings:
        return
    with ui.card().classes("w-full bg-orange-1"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("warning", color="warning", size="1.715em")
            ui.label("Zu prüfen").classes("text-body1 font-bold")
        for warning in warnings:
            ui.label(warning.message).classes("text-body2")
