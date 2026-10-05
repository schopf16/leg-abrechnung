"""The one time-axis control used by every chart in the app."""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence

from nicegui import ui

from app.domain import period

__all__ = ["TimeAxisControl", "render_time_axis"]


@dataclass
class _Anchor:
    """A moment inside the window currently shown."""

    moment: datetime


class TimeAxisControl:
    """The resolution select, the navigation arrows and the window label."""

    def __init__(
        self,
        select: ui.select,
        anchor: _Anchor,
        label: ui.label,
        on_change: Callable[[], None],
    ) -> None:
        """Wire the select and the arrows to the page's refresh."""
        self._select = select
        self._anchor = anchor
        self._label = label
        self._on_change = on_change

    @property
    def key(self) -> str:
        """The selected resolution, one of `period`'s `GRANULARITY_*`."""
        return self._select.value

    @property
    def window(self) -> tuple[datetime, datetime]:
        """The half-open window currently shown."""
        return period.window_for(self.key, self._anchor.moment)

    def step(self, steps: int) -> None:
        """Move one window back or forward and refresh."""
        self._anchor.moment = period.shift_anchor(self.key, self._anchor.moment, steps)
        self.refresh()

    def go_to_now(self) -> None:
        """Jump back to the window containing this moment."""
        self._anchor.moment = datetime.now()
        self.refresh()

    def refresh(self) -> None:
        """Update the window label and let the page redraw."""
        self._label.text = period.window_label(self.key, self.window)
        self._on_change()

    def bucket_starts(self) -> list[datetime]:
        """Every bucket start in the visible window."""
        return period.buckets_in(self.key, self.window)

    def axis_labels(self) -> list[str]:
        """The x-axis tick labels for the visible window."""
        return [period.bucket_label(self.key, start) for start in self.bucket_starts()]


def render_time_axis(
    granularity_keys: Sequence[str],
    on_change: Callable[[], None],
    *,
    default: Optional[str] = None,
    anchor: Optional[datetime] = None,
) -> TimeAxisControl:
    """Render the time-axis row above a chart."""
    options = {key: period.granularity(key).label for key in granularity_keys}
    holder = _Anchor(anchor or datetime.now())

    with ui.row().classes("w-full items-center gap-2"):
        select = ui.select(options, value=default or granularity_keys[-1], label="Auflösung").classes("w-44")
        back = ui.button(icon="chevron_left").props("dense flat").tooltip("Ein Fenster zurück")
        window_label = ui.label("").classes("text-body2 font-bold min-w-[180px] text-center")
        forward = ui.button(icon="chevron_right").props("dense flat").tooltip("Ein Fenster vorwärts")
        today = ui.button("Heute").props("dense flat")

    control = TimeAxisControl(select, holder, window_label, on_change)

    # No "zeigt einen Tag" hint beside the arrows: the window label already
    # reads "Dienstag, 29.09.2026", which says the same thing and says it
    # about the window actually on screen.
    #
    # The anchor stays put when the resolution changes, so switching from a
    # day to its quarter keeps that day in view rather than jumping to today.
    select.on_value_change(lambda _: control.refresh())
    back.on_click(lambda: control.step(-1))
    forward.on_click(lambda: control.step(1))
    today.on_click(control.go_to_now)

    window_label.text = period.window_label(control.key, control.window)
    return control
