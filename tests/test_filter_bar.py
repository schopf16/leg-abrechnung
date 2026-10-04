"""Tests for the one filter bar every browsable list is built with.

Three properties are load-bearing and each one looks fine on screen while
another is broken: a conditional filter that lands between the permanent
ones, a caption that is not the half Quasar wires up, and a page that still
lays its own row out.
"""

from pathlib import Path

from nicegui import Client, ui

from app.gui.filter_bar import FilterBar
from app.gui.problem_markers import FILTER_LABEL


def _bar(probe: str):
    """Render a bar into its own client.

    Args:
        probe: A unique probe route -- every `ui.page` registers itself.

    Returns:
        `(client, bar)`.
    """
    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        bar = FilterBar()
    return client, bar


def test_a_conditional_filter_lands_below_the_permanent_ones():
    """Asked for first, it still renders last.

    This is why the bar exists rather than a convention about the order
    controls are written in: the problem filter comes and goes with the
    findings, and a page that happened to create it first used to put it
    above the filters that are always there.
    """
    client, bar = _bar("/probe-filterbar-order")

    with client:
        conditional = bar.problem_filter(lambda: None)
        permanent = bar.filter("Nur Beispiel")

    holder_conditional = conditional.switch.parent_slot.parent
    holder_permanent = permanent.parent_slot.parent
    assert holder_conditional is not holder_permanent

    siblings = holder_permanent.parent_slot.parent.default_slot.children
    assert siblings.index(holder_permanent) < siblings.index(holder_conditional)


def test_a_filter_puts_its_caption_where_quasar_makes_it_clickable():
    """`ui.switch(text)` renders the text beside the switch and dead.

    The administrator clicked the word and nothing happened, which is the
    whole of this change -- so the caption goes in the `label` prop.
    """
    client, bar = _bar("/probe-filterbar-label")

    with client:
        switch = bar.filter("Nur Genossenschafter")
        problem = bar.problem_filter(lambda: None)

    assert switch._props.get("label") == "Nur Genossenschafter"
    assert not switch.text
    assert problem.switch._props.get("label") == FILTER_LABEL
    assert not problem.switch.text


def test_no_filter_carries_a_count():
    """Deliberately rejected, and the reason is the better one.

    A number on one filter and not the others reads as though the others had
    nothing to count; a number on all four makes the column unreadable. The
    administrator said so when choosing this layout, so it is pinned here --
    the next person to read "Zahl am Fehlerfilter" would reasonably add it.
    """
    client, bar = _bar("/probe-filterbar-count")

    with client:
        bar.filter("Nur offene Forderungen")
        bar.problem_filter(lambda: None)

    captions = [
        element._props.get("label", "")
        for element in client.elements.values()
        if element.__class__.__name__ == "Switch"
    ]
    assert captions
    for caption in captions:
        assert not any(character.isdigit() for character in caption), caption


def test_the_search_field_keeps_its_field_list_visible():
    """The label used to be the list of fields searched.

    Quasar shrinks a label to caption size above the input as soon as
    anything is typed, so it became unreadable exactly while it was being
    used. The label is "Suche" and the list is the hint underneath.
    """
    client, bar = _bar("/probe-filterbar-search")

    with client:
        search = bar.search("Name, Firma, Adresse")

    assert search.label == "Suche"
    assert search._props.get("hint") == "Name, Firma, Adresse"


def test_no_list_page_lays_its_own_filter_row_out_any_more():
    """One mechanism, every list -- the same guard the sorting has.

    A page that reaches for `render_sort_select` itself is a page that will
    grow its own layout again, which is the complaint this answers: *"bei
    jeder neuen idee packen wir einfach nach etwas hinten an"*.
    """
    #: `legs.py` sorts the metering-point sub-table on the LEG *detail*
    #: page, which is not a browsable list and carries no filters -- the
    #: exemption CLAUDE.md already names for detail sub-tables.
    allowed = {"legs.py"}

    offenders = []
    for path in sorted(Path("app/gui/pages").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if path.name in allowed:
            continue
        if "render_sort_select" in source or "ProblemFilter(" in source:
            offenders.append(path.name)

    assert offenders == [], offenders
