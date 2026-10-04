"""What a list was showing, kept across a page change.

The loop the problem markers were built for is: see the triangle, open the
record, fix it, come back. Until now the last step threw everything away --
the filter was off again, the search box empty, the sort back to default and,
since the lists page, the position back on page one. With ninety-two rows that
is the difference between correcting a handful and hunting for them twice.

**A module-level store, not `app.storage`.** This app is one native window for
one administrator (`app.main.main` runs `ui.run(native=True)`), the same
premise `app.gui.address_register_task` already builds on. `app.storage.user`
needs a `storage_secret` and writes a file; `app.storage.tab` needs a
connected client and an await. Neither buys anything here, and a dict keyed by
route is readable in one sitting.

What that costs is honest to state: the state lives as long as the process,
so it is gone after a restart, and two windows onto the same app would share
it. The first is right -- a filter from last week is not what anybody wants to
come back to -- and the second cannot happen in a native window.

Only the **controls** are remembered, never the rows. A list always re-reads
its records, so a correction shows up; what is restored is the question that
was being asked of them.
"""

from typing import Any

#: `{route: {control name: value}}`.
_STATE: dict[str, dict[str, Any]] = {}


def recall(route: str, name: str, default: Any = None) -> Any:
    """What this control was last set to on this list.

    Args:
        route: The list's route, e.g. `"/persons"`.
        name: The control's name within that list.
        default: Returned when the list has not been visited yet.

    Returns:
        The remembered value, or `default`.
    """
    return _STATE.get(route, {}).get(name, default)


def remember(route: str, name: str, value: Any) -> None:
    """Keep one control's value for the next visit.

    Args:
        route: The list's route.
        name: The control's name within that list.
        value: What to keep.

    Returns:
        None.
    """
    _STATE.setdefault(route, {})[name] = value


def forget(route: str) -> None:
    """Drop everything remembered for one list.

    Only the tests use this, and they need it: the store outlives a test the
    way it outlives a page, and xdist hands each worker an arbitrary slice.

    Args:
        route: The list's route.

    Returns:
        None.
    """
    _STATE.pop(route, None)


def forget_everything() -> None:
    """Drop the whole store.

    Returns:
        None.
    """
    _STATE.clear()
