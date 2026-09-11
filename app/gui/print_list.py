"""Shared "print this list" building block for the list pages under
`app.gui.pages`.

Deliberately renders a separate, plain HTML table for printing rather than
printing the on-screen cards/table 1:1 -- the on-screen layout (wrapping
card columns, icon buttons) makes a poor printout, whereas a compact table
is easy to scan on paper. Always shows which list ("heading") was printed
and the print date/time; the currently active filter is optional and
supplied by the calling page (`filter_description`), since not every page
has one worth mentioning.

Uses the browser's own print dialog (`window.print()`) rather than
generating a PDF file ourselves: that dialog already offers "Als PDF
speichern" if that's what someone wants, so there is no separate PDF
pipeline to build or keep in sync with this table's layout.
"""

import html
from datetime import datetime
from typing import Callable, Optional

from nicegui import ui

#: Injected once per page (see `app.gui.navigation.page_frame`). Hides the
#: entire page except the (normally invisible) `.leg-print-area` while
#: the browser's print dialog is open.
#:
#: `visibility: hidden` is used for the blanket hide (not `display: none`)
#: so NiceGUI/Quasar's layout doesn't reflow. The print area itself uses
#: `position: absolute`, NOT `fixed`: a `position: fixed` element is
#: (correctly, per the CSS paged-media model -- browsers use this for
#: running headers/footers) redrawn on *every* printed page, which for a
#: multi-page table meant the entire table -- every row -- was reprinted
#: in full on each page, looking like every person appeared many times
#: over. `absolute` is drawn once and simply continues (is "cut") across
#: page boundaries like normal content.
#:
#: Using `absolute` again reintroduces the bug it originally replaced,
#: though: Quasar's own CSS sets `.q-layout`/`.q-page` (ancestors of our
#: print area) to `position: relative`, which makes an `absolute` child
#: size itself against that inner, still-narrowed-by-the-drawer page box
#: instead of the actual printed page. Fixed here at that root cause
#: instead: neutralize those ancestors' `position` during print so our
#: `absolute` print area resolves against the real page.
#:
#: Printed landscape by default since these tables tend to be wide (many
#: columns); the user's print dialog can still override it.
PRINT_STYLE = """
<style>
.leg-print-area { display: none; }
@media print {
    @page { size: landscape; margin: 12mm; }
    body * { visibility: hidden; }
    .q-layout, .q-header, .q-footer, .q-toolbar, .q-page {
        position: static !important;
    }
    .leg-print-area, .leg-print-area * { visibility: visible; }
    .leg-print-area {
        display: block !important;
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        margin: 0;
        padding: 0;
    }
    .leg-print-heading { font-size: 16px; font-weight: bold; margin-bottom: 4px; }
    .leg-print-meta { font-size: 11px; color: #444; margin: 0 0 2px; }
    .leg-print-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 8px;
        table-layout: fixed;
    }
    .leg-print-table th, .leg-print-table td {
        border: 1px solid #999;
        padding: 4px 8px;
        text-align: left;
        font-size: 11px;
        overflow-wrap: break-word;
        word-break: break-word;
    }
    /* Keep a row on one page: otherwise a row straddling a page break can
       show only its first wrapped line on one page and the rest on the
       next, which looks exactly like a cut-off word. */
    .leg-print-table tr {
        page-break-inside: avoid;
        break-inside: avoid;
    }
}
</style>
"""


def table_columns(table: ui.table) -> list[tuple[str, str]]:
    """Extract `(label, field)` pairs from a `ui.table`'s column definitions.

    Skips the (labelless) "actions" column that every `ui.table`-based
    list page uses for its row icon buttons -- nothing to print there.

    Args:
        table: The table to read column definitions from.

    Returns:
        `[(label, field), ...]` for every printable column, in the
        table's own column order.
    """
    return [
        (col["label"], col["field"])
        for col in table.columns
        if col.get("label") and col.get("field") != "actions"
    ]


def render_print_button(
    *,
    heading: str,
    get_columns: Callable[[], list[tuple[str, str]]],
    get_rows: Callable[[], list[dict]],
    get_filter_description: Callable[[], Optional[str]] = lambda: None,
) -> ui.button:
    """Render a "Drucken" button that prints the currently displayed rows.

    Args:
        heading: Human-readable name of the list, printed as the page
            heading (e.g. "persons", "Aufnahmen").
        get_columns: Callback returning the current `[(label, field), ...]`
            column definitions. A callback (not a plain list) so callers
            whose columns depend on runtime state can stay accurate; most
            pages can just return a fixed list.
        get_rows: Callback returning the rows currently shown on screen --
            i.e. *after* any active search/filter has been applied, not
            the full unfiltered dataset -- as a list of dicts keyed by
            each column's `field`.
        get_filter_description: Callback returning a short, human-readable
            description of the currently active filter(s), or `None` if
            none is active / it shouldn't be shown on the printout.

    Returns:
        The rendered button, in case a caller wants to further style it.
    """
    print_area = ui.column().classes("leg-print-area")

    def do_print() -> None:
        """Populate the hidden print area from the current filter state and
        trigger the browser's print dialog.

        Returns:
            None.
        """
        columns = get_columns()
        rows = get_rows()
        description = get_filter_description()

        parts = [
            f"<div class='leg-print-heading'>{html.escape(heading)}</div>",
            "<div class='leg-print-meta'>Gedruckt am: "
            f"{html.escape(datetime.now().strftime('%d.%m.%Y %H:%M'))}</div>",
        ]
        if description:
            parts.append(f"<div class='leg-print-meta'>Filter: {html.escape(description)}</div>")

        header_html = "".join(f"<th>{html.escape(label)}</th>" for label, _ in columns)
        if rows:
            body_html = "".join(
                "<tr>"
                + "".join(f"<td>{html.escape(str(row.get(field, '') or ''))}</td>" for _, field in columns)
                + "</tr>"
                for row in rows
            )
        else:
            body_html = f"<tr><td colspan='{len(columns)}'>Keine Einträge.</td></tr>"
        parts.append(
            f"<table class='leg-print-table'><thead><tr>{header_html}</tr></thead>"
            f"<tbody>{body_html}</tbody></table>"
        )

        print_area.clear()
        with print_area:
            ui.html("".join(parts))
        ui.run_javascript("window.print()")

    return ui.button("Drucken", icon="print", on_click=do_print).props("outline").classes("shrink-0")
