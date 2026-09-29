"""The one time-axis control used by every chart in the app.

Same idea as `app.gui.sorting`, for the other axis: a chart says which
resolutions it offers and gets back a control that owns the rest -- the
select, the back/forward arrows, a "Heute" button and the label naming the
window on screen. One mechanism, so the question "how do I change the
period?" has the same answer on every chart.

**The window follows the resolution** rather than being picked separately.
The tempting alternative -- free from/to dates beside a free resolution --
lets somebody ask for a year in quarter-hours, which is 35'040 points: a
chart the browser gives up on, after a query nobody wants to wait for.
Coupling them means no combination a user can reach produces an unusable
picture. See `app.domain.period.Granularity`.

The bucket arithmetic itself lives in `app.domain.period`, which imports
no NiceGUI, and is only driven from here -- the same split as
`app.sort_keys` against this package, and for the same reason: a chart and
a later CSV export of the same figures must cut time into identical
buckets, or the export will quietly disagree with the picture it came
from.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence

from nicegui import ui

from app.domain import period

__all__ = ["TimeAxisControl", "render_time_axis"]


@dataclass
class _Anchor:
    """A moment inside the window currently shown.

    Held in its own object so the nested button handlers can reassign it
    without `nonlocal` gymnastics.

    Attributes:
        moment: Any moment inside the visible window.
    """

    moment: datetime


class TimeAxisControl:
    """The resolution select, the navigation arrows and the window label.

    A page passes the whole control to whatever draws the chart and reads
    `key` and `window` from it, exactly as it passes a `SortControl` to
    `apply_sort`. Nothing else needs to know how a window is derived.
    """

    def __init__(
        self,
        select: ui.select,
        anchor: _Anchor,
        label: ui.label,
        on_change: Callable[[], None],
    ) -> None:
        """Wire the select and the arrows to the page's refresh.

        Args:
            select: The rendered resolution select.
            anchor: Holds the moment the window is derived from.
            label: The label naming the visible window.
            on_change: The page's refresh, called after any change.
        """
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
        """Move one window back or forward and refresh.

        Args:
            steps: Negative goes back.

        Returns:
            None.
        """
        self._anchor.moment = period.shift_anchor(self.key, self._anchor.moment, steps)
        self.refresh()

    def go_to_now(self) -> None:
        """Jump back to the window containing this moment.

        Returns:
            None.
        """
        self._anchor.moment = datetime.now()
        self.refresh()

    def refresh(self) -> None:
        """Update the window label and let the page redraw.

        Returns:
            None.
        """
        self._label.text = period.window_label(self.key, self.window)
        self._on_change()

    def bucket_starts(self) -> list[datetime]:
        """Every bucket start in the visible window.

        Returns:
            The bucket start moments, oldest first.
        """
        return period.buckets_in(self.key, self.window)

    def axis_labels(self) -> list[str]:
        """The x-axis tick labels for the visible window.

        Returns:
            One German label per bucket, in order.
        """
        return [period.bucket_label(self.key, start) for start in self.bucket_starts()]


def render_time_axis(
    granularity_keys: Sequence[str],
    on_change: Callable[[], None],
    *,
    default: Optional[str] = None,
    anchor: Optional[datetime] = None,
) -> TimeAxisControl:
    """Render the time-axis row above a chart.

    Args:
        granularity_keys: The resolutions this chart offers, finest first.
            A chart of monthly bookings has no use for quarter-hours, so
            each page names its own subset rather than every chart
            carrying every option.
        on_change: Called whenever the resolution or the window changes.
        default: Which resolution to start on; the last of
            `granularity_keys` if not given, so a chart opens on its
            broadest view rather than on 96 points of one day.
        anchor: Moment to start at, defaulting to now.

    Returns:
        The `TimeAxisControl`. Its `refresh()` is **not** called here, so
        the caller can finish building its chart before the first draw.
    """
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
