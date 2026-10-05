"""Tests for the one filter bar every browsable list is built with."""

from pathlib import Path

from nicegui import Client, ui

from app.gui.filter_bar import FilterBar
from app.gui.problem_markers import FILTER_LABEL


def _bar(probe: str):
    """Render a bar into its own client."""
    client = Client(ui.page(probe)(lambda: None), request=None)
    with client:
        bar = FilterBar()
    return client, bar


def test_a_conditional_filter_lands_below_the_permanent_ones():
    """Asked for first, it still renders last."""
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
    """`ui.switch(text)` renders the text beside the switch and dead."""
    client, bar = _bar("/probe-filterbar-label")

    with client:
        switch = bar.filter("Nur Genossenschafter")
        problem = bar.problem_filter(lambda: None)

    assert switch._props.get("label") == "Nur Genossenschafter"
    assert not switch.text
    assert problem.switch._props.get("label") == FILTER_LABEL
    assert not problem.switch.text


def test_no_filter_carries_a_count():
    """Deliberately rejected, and the reason is the better one."""
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
    """The label used to be the list of fields searched."""
    client, bar = _bar("/probe-filterbar-search")

    with client:
        search = bar.search("Name, Firma, Adresse")

    assert search.label == "Suche"
    assert search._props.get("hint") == "Name, Firma, Adresse"


def test_no_list_page_lays_its_own_filter_row_out_any_more():
    """One mechanism, every list -- the same guard the sorting has."""
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
