"""One keyboard for the whole window, and a stack of who owns it."""

from dataclasses import dataclass
from typing import Callable, Optional

from nicegui import context, ui
from nicegui.events import KeyEventArguments

#: Attribute the per-client stack is kept under.
_STACK_ATTRIBUTE = "leg_keyboard_layers"


# `eq=False` so a layer is recognised by identity: two layers that declare
# the same handlers are still two layers, and a plain dataclass would call
# them equal and let `remove` take the wrong one off the stack.
@dataclass(eq=False)
class KeyboardLayer:
    """What one dialog, list or question does with the keys."""

    on_escape: Optional[Callable[[], None]] = None
    on_enter: Optional[Callable[[], None]] = None
    on_move: Optional[Callable[[int], None]] = None
    on_typing: Optional[Callable[[], None]] = None


#: Used when there is no client to hang the stack on -- a unit test calling
#: a widget directly, or any code running outside a request. Kept rather
#: than raising: a dialog must not fail to open because nobody is listening
#: for keys.
_FALLBACK: list[KeyboardLayer] = []


def layers() -> list[KeyboardLayer]:
    """The current client's stack, innermost last."""
    try:
        client = context.client
    except RuntimeError:
        return _FALLBACK
    if not hasattr(client, _STACK_ATTRIBUTE):
        setattr(client, _STACK_ATTRIBUTE, [])
    return getattr(client, _STACK_ATTRIBUTE)


def push(layer: KeyboardLayer) -> None:
    """Give one layer the keys, above everything already open."""
    stack = layers()
    if layer not in stack:
        stack.append(layer)


def remove(layer: KeyboardLayer) -> None:
    """Take the keys back from one layer, wherever it sits."""
    stack = layers()
    if layer in stack:
        stack.remove(layer)


def handle_key(event: KeyEventArguments) -> None:
    """Route one key press to whoever is on top."""
    if not event.action.keydown:
        return
    stack = layers()
    if not stack:
        return
    layer = stack[-1]
    key = event.key

    if key.escape:
        if layer.on_escape is not None:
            layer.on_escape()
        return
    if key.enter:
        if layer.on_enter is not None:
            layer.on_enter()
        return
    if key.tab:
        # The browser's own order, never intercepted: it already walks the
        # fields in the order they are drawn in.
        return
    if layer.on_move is not None and key.is_cursorkey:
        if key.arrow_up or key.arrow_left:
            layer.on_move(-1)
        elif key.arrow_down or key.arrow_right:
            layer.on_move(1)
        return
    if layer.on_typing is not None and _changes_text(event):
        layer.on_typing()


def _changes_text(event: KeyEventArguments) -> bool:
    """Whether this key would alter what stands in a field."""
    if event.modifiers.ctrl or event.modifiers.meta or event.modifiers.alt:
        return False
    name = event.key.name
    return event.key.backspace or event.key.delete or event.key.space or len(str(name)) == 1


def install() -> ui.keyboard:
    """Create the page's one keyboard."""
    return ui.keyboard(handle_key, ignore=[])
