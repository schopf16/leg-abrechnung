"""One keyboard for the whole window, and a stack of who owns it.

Keys were bound per element until the administrator tried to use them:
Escape reached one dialog and not the next, the discard question could only
be answered with the mouse, and the arrows did nothing anywhere except in
the address list. Binding `keydown.…` on an element only works while that
element has the focus and the event bubbles out of it -- and a Quasar dialog
renders its card in a portal, so neither is reliable.

So there is one `ui.keyboard` per page (created in `app.gui.navigation`), and
whoever is on top of the stack owns the keys:

| Key | Meaning |
|---|---|
| Enter | take the marked thing |
| ↑ ↓ ← → | move the mark |
| Escape | go back one step |
| Tab | next field (the browser's own, never intercepted) |

A layer declares only what it can answer; anything it leaves at `None`
happens as the browser would do it. That is what keeps the arrows working as
caret movement inside a form while the same keys walk the entries of an
address list or the buttons of a question -- the layer on top decides, and a
form dialog deliberately does not claim them.

`ignore=[]` is deliberate: NiceGUI ignores key events from inputs, selects
and buttons by default, which would mean Escape stops working the moment the
administrator is typing -- the one moment it is needed.

The stack lives on the client rather than in a module-level list, because
tests render many clients in one process and a leaked layer from one would
answer another's keys.
"""

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
    """What one dialog, list or question does with the keys.

    Attributes:
        on_escape: Go back one step -- dismiss, cancel, close.
        on_enter: Take whatever is marked.
        on_move: Move the mark by `-1` or `+1`. Left `None` by anything that
            holds text fields, so the arrows stay caret movement there.
        on_typing: Called for any key that changes text. Used to notice that
            something was typed at all, which is more reliable than comparing
            values: a debounced input has not told the server yet.
    """

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
    """The current client's stack, innermost last.

    Returns:
        The stack, created on first use.
    """
    try:
        client = context.client
    except RuntimeError:
        return _FALLBACK
    if not hasattr(client, _STACK_ATTRIBUTE):
        setattr(client, _STACK_ATTRIBUTE, [])
    return getattr(client, _STACK_ATTRIBUTE)


def push(layer: KeyboardLayer) -> None:
    """Give one layer the keys, above everything already open.

    Idempotent: a suggestion list redraws on every keystroke and must not
    stack a layer per character.

    Args:
        layer: The layer to put on top.

    Returns:
        None.
    """
    stack = layers()
    if layer not in stack:
        stack.append(layer)


def remove(layer: KeyboardLayer) -> None:
    """Take the keys back from one layer, wherever it sits.

    Removed by identity rather than popped, because a question opened on top
    of a form can be closed while the form stays open underneath.

    Args:
        layer: The layer to remove.

    Returns:
        None.
    """
    stack = layers()
    if layer in stack:
        stack.remove(layer)


def handle_key(event: KeyEventArguments) -> None:
    """Route one key press to whoever is on top.

    Args:
        event: NiceGUI's key event.

    Returns:
        None.
    """
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
    """Whether this key would alter what stands in a field.

    Args:
        event: NiceGUI's key event.

    Returns:
        `True` for a character, a space or a deletion.
    """
    if event.modifiers.ctrl or event.modifiers.meta or event.modifiers.alt:
        return False
    name = event.key.name
    return event.key.backspace or event.key.delete or event.key.space or len(str(name)) == 1


def install() -> ui.keyboard:
    """Create the page's one keyboard.

    Returns:
        The keyboard element.
    """
    return ui.keyboard(handle_key, ignore=[])
