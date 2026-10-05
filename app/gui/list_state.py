"""What a list was showing, kept across a page change."""

from typing import Any

#: `{route: {control name: value}}`.
_STATE: dict[str, dict[str, Any]] = {}


def recall(route: str, name: str, default: Any = None) -> Any:
    """What this control was last set to on this list."""
    return _STATE.get(route, {}).get(name, default)


def remember(route: str, name: str, value: Any) -> None:
    """Keep one control's value for the next visit."""
    _STATE.setdefault(route, {})[name] = value


def forget(route: str) -> None:
    """Drop everything remembered for one list."""
    _STATE.pop(route, None)


def forget_everything() -> None:
    """Drop the whole store."""
    _STATE.clear()
