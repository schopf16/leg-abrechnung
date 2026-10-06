"""The one list of placeholders with a resolved example, linked from every mail form.

Built once here and referenced everywhere a mail is written or edited (Rundmail,
Textbaustein, Rechnungsmail, der Versanddialog), so the administrator reads the
same list wherever they are and no page keeps its own copy of the names.
"""

from typing import Callable, Optional, Sequence, Union

from nicegui import ui

from app.db.connection import connection_scope
from app.domain.dunning import DUNNING_EXTRA_PLACEHOLDERS
from app.emailing.bulk_send import INVOICE_EXTRA_PLACEHOLDERS
from app.emailing.templates import ALL_PLACEHOLDERS, placeholder_examples, placeholder_values
from app.formatting import MISSING
from app.models import person as person_repo
from app.models.message_template import (
    OCCASION_DUNNING1,
    OCCASION_DUNNING2,
    OCCASION_INVOICE,
)
from app.models.person import Person
from app.sort_keys import person_name_key

#: Which extra placeholders an occasion adds to the ones available everywhere.
#: An invoice knows its quarter and its amount, a dunning notice knows the new
#: deadline, a welcome mail knows neither.
EXTRA_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    OCCASION_INVOICE: INVOICE_EXTRA_PLACEHOLDERS,
    OCCASION_DUNNING1: DUNNING_EXTRA_PLACEHOLDERS,
    OCCASION_DUNNING2: DUNNING_EXTRA_PLACEHOLDERS,
}

#: Shown above the example column for the invented default.
EXAMPLE_NOTE = "Beispielwerte, aufgelöst für eine erfundene Person."

#: What the first entry of the person select is called.
EXAMPLE_LABEL = "Erfundene Beispielperson"

#: Shown while a real person is chosen -- the one thing the list cannot answer
#: for them, so that an empty cell is not mistaken for one of these.
AT_THE_SEND_NOTE = "Betrag, Quartal, Jahr und Frist entstehen erst beim Versand und bleiben Beispielwerte."

COLUMNS = [
    {"name": "placeholder", "label": "Platzhalter", "field": "placeholder", "align": "left"},
    {"name": "example", "label": "Beispiel", "field": "example", "align": "left"},
]


def placeholders_for(occasion: Optional[str] = None) -> tuple[str, ...]:
    """Every placeholder valid in a text for this occasion, in display order."""
    extra = EXTRA_PLACEHOLDERS.get(occasion or "", ())
    return (*ALL_PLACEHOLDERS, *(name for name in extra if name not in ALL_PLACEHOLDERS))


def example_rows(names: Sequence[str], person: Optional[Person] = None) -> list[dict]:
    """The table rows: one placeholder per row, with what it would produce.

    Without a person the invented default answers; with one, the real record
    does -- `MISSING` where it has nothing to say, which is the whole point of
    being able to pick a person (a Messpunkt without a Trafokreis, say).
    """
    if person is None:
        examples = placeholder_examples(names)
    else:
        with connection_scope() as connection:
            examples = placeholder_examples(names, placeholder_values(connection, person))
    return [{"placeholder": "{" + name + "}", "example": example or MISSING} for name, example in examples]


def open_placeholder_dialog(names: Sequence[str]) -> None:
    """Show the placeholder list with its resolved examples.

    Read-only, so deliberately without `form_guard`: there is nothing to lose
    and clicking beside the card is the fastest way out.
    """
    with connection_scope() as connection:
        # A dropdown has no sort control, so this order is the only one there
        # is. Deactivated persons are left out: nothing is mailed to them.
        persons = sorted(
            (person for person in person_repo.list_all(connection) if person.active),
            key=person_name_key,
        )
    persons_by_id = {person.id: person for person in persons}
    options = {None: EXAMPLE_LABEL, **{person.id: person.display_name for person in persons}}
    invented_names = [name for name in names if name not in ALL_PLACEHOLDERS]

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-3xl"):
        ui.label("Platzhalter").classes("text-lg font-bold")
        person_select = ui.select(options, label="Beispiel für", value=None, with_input=True).classes(
            "w-full max-w-md"
        )
        note = ui.label(EXAMPLE_NOTE).classes("text-caption text-grey-7")
        table = ui.table(columns=COLUMNS, rows=example_rows(names), row_key="placeholder").classes("w-full")
        table.props("flat bordered dense wrap-cells hide-pagination")
        table._props["rows-per-page-options"] = [0]

        def show_chosen() -> None:
            """Resolve the list again, for whoever is chosen now."""
            person = persons_by_id.get(person_select.value)
            table.rows = example_rows(names, person)
            table.update()
            if person is None:
                note.text = EXAMPLE_NOTE
            elif invented_names:
                note.text = f"Aufgelöst für {person.display_name}. {AT_THE_SEND_NOTE}"
            else:
                note.text = f"Aufgelöst für {person.display_name}."

        person_select.on_value_change(show_chosen)
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("Schliessen", on_click=dialog.close).props("flat")
    dialog.open()


def render_placeholder_help(occasion: Union[None, str, Callable[[], Optional[str]]] = None) -> ui.button:
    """Render the link to the placeholder list, beside the subject/body fields.

    `occasion` may be a callable, because the Textbaustein dialog lets the
    occasion be changed while the dialog is open -- it is read on the click,
    never once at build time.
    """

    def show() -> None:
        chosen = occasion() if callable(occasion) else occasion
        open_placeholder_dialog(placeholders_for(chosen))

    return ui.button("Platzhalter ansehen", icon="help_outline", on_click=show).props("flat dense no-caps")
