"""Textbausteine page: the stored email texts and the files that go with them."""

from nicegui import ui

from app.db.connection import connection_scope
from app.domain import auto_attachments
from app.emailing import graph_client
from app.format_size import format_size
from app.formatting import format_date
from app.gui.contract_preview import open_contract_preview
from app.gui.filter_bar import FilterBar
from app.gui.form_dialog import form_guard
from app.gui.navigation import page_frame
from app.gui.placeholder_help import render_placeholder_help
from app.gui.print_list import render_print_button, table_columns
from app.gui.safe_notify import safe_notify
from app.gui.sorting import SortOption, apply_sort, sort_description, text_key
from app.gui.table_list import paged_table
from app.gui.upload import read_uploaded_file
from app.models import leg_document as leg_document_repo
from app.models import message_template as template_repo
from app.models import person_offboarding, person_onboarding
from app.models import signature as signature_repo
from app.models.message_template import (
    OCCASION_LABELS,
    OCCASION_OFFBOARDING,
    OCCASION_ONBOARDING,
    STEP_OCCASIONS,
    TRIGGER_LABELS,
    TRIGGER_STEP_DONE,
    TRIGGER_STEP_PENDING,
    MessageTemplate,
)

COLUMNS = [
    {"name": "name", "label": "Name", "field": "name", "align": "left"},
    {"name": "occasion", "label": "Anlass", "field": "occasion", "align": "left"},
    {"name": "trigger", "label": "Fällig", "field": "trigger", "align": "left"},
    {"name": "subject", "label": "Betreff", "field": "subject", "align": "left"},
    {"name": "attachments", "label": "Anhänge", "field": "attachments", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]

#: Orders this list offers, default first. **Anlass leads**, on the
#: administrator's own reasoning: "ich öffne das weil etwas mit dem
#: gesellschaftsvertrag nicht stimmt, also suche ich danach. den namen habe
#: ich dann vielleicht schon wieder vergessen." The name is what you give a
#: text; the occasion is what you remember about it.
SORT_OPTIONS = [
    SortOption("occasion", "Anlass", lambda row: text_key(row["occasion"], row["name"])),
    SortOption("name", "Name", lambda row: text_key(row["name"])),
]

#: `{attribute: label}` per step occasion, so the dialog can offer the steps
#: of whichever process is chosen. Taken from the trackers themselves rather
#: than copied, for the reason `app.gui.sorting` gives about one mechanism:
#: a step added to `person_onboarding.STEPS` has to appear here by itself.
STEPS_BY_OCCASION = {
    OCCASION_ONBOARDING: dict(person_onboarding.STEPS),
    OCCASION_OFFBOARDING: dict(person_offboarding.STEPS),
}


def _describe_trigger(template: MessageTemplate) -> str:
    """Say in one line when this template becomes due."""
    if template.occasion not in STEP_OCCASIONS:
        return "beim Versand"
    step_label = STEPS_BY_OCCASION.get(template.occasion, {}).get(template.step, template.step or "?")
    if template.trigger_kind == TRIGGER_STEP_DONE:
        return f"wenn „{step_label}“ erledigt ist"
    if template.trigger_kind == TRIGGER_STEP_PENDING:
        if template.deadline_days:
            return f"wenn „{step_label}“ {template.deadline_days} Tage offen ist"
        return f"solange „{step_label}“ offen ist"
    return "–"


def _to_row(template: MessageTemplate, attachment_names: list[str]) -> dict:
    """Convert a template into a table row."""
    # The ticked documents first, because those are the ones that make the
    # mail what it is -- an uploaded leaflet is the afterthought.
    named = [
        auto_attachments.label_for(key).replace(" anfügen", "") for key in template.auto_attachments
    ] + attachment_names
    return {
        "id": template.id,
        "name": template.name,
        "occasion": template.occasion_label,
        "trigger": _describe_trigger(template),
        "subject": template.subject,
        "attachments": ", ".join(named) if named else "–",
        "_search": " ".join(
            [template.name, template.occasion_label, template.subject, template.body]
        ).lower(),
    }


@ui.page("/message-templates")
def message_templates_page() -> None:
    """Render the Textbausteine list with its create/edit dialog."""
    with page_frame("/message-templates", "Textbausteine"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            ui.label(
                "Vorbereitete E-Mail-Texte. Ein Baustein wird nie von selbst "
                "versendet -- er legt fest, wann im Aufnahme- oder "
                "Austrittsprozess ein Knopf dafür erscheint."
            ).classes("text-body2 text-grey-8")
            with ui.row().classes("gap-2 shrink-0"):
                render_print_button(
                    heading="Textbausteine",
                    get_columns=lambda: table_columns(table),
                    get_rows=lambda: table.rows,
                    get_filter_description=lambda: (
                        f'Suche: "{search_input.value.strip()}"' if search_input.value else None
                    ),
                    get_sort_description=lambda: sort_description(SORT_OPTIONS, sort_select),
                )
                ui.button("+ Neuer Textbaustein", on_click=lambda: open_form(None))

        bar = FilterBar("/message-templates")
        search_input = bar.search("Name, Anlass, Betreff, Text")
        sort_select = bar.sort(SORT_OPTIONS, lambda: apply_filter())

        table = paged_table(route="/message-templates", columns=COLUMNS, rows=[], row_key="id").classes(
            "w-full mt-2"
        )
        table.add_slot(
            "body-cell-actions",
            r"""
            <q-td :props="props">
                <q-btn dense flat icon="edit" @click="() => $parent.$emit('edit', props.row)" />
                <q-btn dense flat icon="delete" color="negative"
                       @click="() => $parent.$emit('remove', props.row)" />
            </q-td>
            """,
        )

        all_rows: list[dict] = []

        def apply_filter() -> None:
            """Narrow the loaded rows by the search text and sort them."""
            needle = (search_input.value or "").strip().lower()
            rows = [row for row in all_rows if needle in row["_search"]] if needle else list(all_rows)
            table.rows = apply_sort(rows, SORT_OPTIONS, sort_select)
            table.update()

        def refresh() -> None:
            """Reload every template and re-apply the filter."""
            nonlocal all_rows
            with connection_scope() as connection:
                templates = template_repo.list_all(connection)
                attachments = {
                    template.id: [
                        attachment.filename
                        for attachment in template_repo.list_attachments(connection, template.id)
                    ]
                    for template in templates
                }
            all_rows = [_to_row(template, attachments[template.id]) for template in templates]
            apply_filter()

        search_input.on_value_change(lambda _=None: apply_filter())

        def open_form(existing: MessageTemplate | None) -> None:
            """Open the create/edit dialog for a template."""
            # Files picked in this dialog, kept until the template is saved:
            # a new one has no id to attach them to yet, the same reason
            # `app.gui.address_input`'s dismissals are stored after saving.
            pending: list[tuple[str, bytes]] = []

            with ui.dialog() as dialog, ui.card().classes("w-full max-w-3xl"):
                ui.label("Textbaustein bearbeiten" if existing else "Neuer Textbaustein").classes(
                    "text-lg font-bold"
                )

                name = (
                    ui.input("Name", value=existing.name if existing else "")
                    .classes("w-full")
                    .props("autofocus")
                )
                name.props('hint="Wie Sie ihn nennen -- steht auf dem Knopf, z. B. „Willkommen“"')

                # Built with the steps of the chosen occasion already in place:
                # `ui.select` refuses a value that is not among its options, so
                # an empty option list plus a stored step raises before
                # `follow_occasion` below ever runs -- and a stepless occasion
                # has `step = ""`, which is not a valid option either.
                initial_occasion = existing.occasion if existing else OCCASION_ONBOARDING
                initial_steps = STEPS_BY_OCCASION.get(initial_occasion, {})
                initial_step = existing.step if existing and existing.step in initial_steps else None

                with ui.row().classes("w-full gap-2"):
                    occasion = ui.select(
                        OCCASION_LABELS,
                        label="Anlass",
                        value=initial_occasion,
                    ).classes("flex-grow")
                    step = ui.select(initial_steps, label="Schritt", value=initial_step).classes("flex-grow")

                with ui.row().classes("w-full gap-2"):
                    trigger = ui.select(
                        {
                            TRIGGER_STEP_DONE: TRIGGER_LABELS[TRIGGER_STEP_DONE],
                            TRIGGER_STEP_PENDING: TRIGGER_LABELS[TRIGGER_STEP_PENDING],
                        },
                        label="Fällig",
                        value=(
                            existing.trigger_kind if existing and existing.trigger_kind else TRIGGER_STEP_DONE
                        ),
                    ).classes("flex-grow")
                    deadline = ui.number(
                        "Frist (Tage)",
                        value=existing.deadline_days if existing else None,
                        min=0,
                        step=1,
                        format="%.0f",
                    ).classes("w-40")
                deadline.props('hint="Nur bei „solange Schritt offen“ -- leer heisst sofort"')

                #: Filled further down, once the document checkboxes exist.
                #: They have to be created where they appear on screen, which
                #: is below this, so `follow_occasion` cannot name them
                #: directly -- it ran before they existed and raised.
                when_occasion_changes: list = []

                def follow_occasion() -> None:
                    """Offer the steps of the chosen process, and hide what does not apply."""
                    has_steps = occasion.value in STEP_OCCASIONS
                    step.set_options(STEPS_BY_OCCASION.get(occasion.value, {}))
                    step.visible = has_steps
                    trigger.visible = has_steps
                    deadline.visible = has_steps
                    for hook in when_occasion_changes:
                        hook()

                occasion.on_value_change(lambda _=None: follow_occasion())
                follow_occasion()

                subject = ui.input("Betreff", value=existing.subject if existing else "").classes("w-full")
                body = (
                    ui.textarea("Text", value=existing.body if existing else "")
                    .classes("w-full")
                    .props("rows=10")
                )
                # The occasion is read on the click, so the list follows the
                # select without this dialog having to re-render anything.
                render_placeholder_help(lambda: occasion.value)

                # The same named signatures the Rundmail offers, so a
                # Textbaustein can sign off exactly like one. Appended when
                # the mail is composed, not stored in the text -- a changed
                # signature has to change everywhere.
                with connection_scope() as connection:
                    signatures = signature_repo.list_all(connection)
                signature_options = {None: "Keine Signatur", **{s.id: s.name for s in signatures}}
                stored_signature = existing.signature_id if existing else None
                signature = ui.select(
                    signature_options,
                    label="Signatur",
                    value=stored_signature if stored_signature in signature_options else None,
                ).classes("w-full")
                if not signatures:
                    ui.label("Noch keine Signatur hinterlegt -- Kommunikation → Signaturen.").classes(
                        "text-caption text-grey-6"
                    )

                ui.separator().classes("my-2")
                ui.label("Automatisch anfügen").classes("text-body1 font-bold")
                # One switch per document, from the registry in
                # `app.domain.auto_attachments` -- so a document added later
                # is one entry there and this dialog looks the same. The
                # administrator asked for exactly that: "mache also etwas wie
                # bei den quickfilter das nicht bei jeder änderung das look &
                # feel anders aussieht".
                auto_column = ui.column().classes("w-full gap-1")
                auto_switches: dict[str, ui.switch] = {}
                missing_note = ui.label("").classes("text-warning text-body2")

                def render_auto_attachments() -> None:
                    """Draw the checkboxes that apply to the chosen occasion."""
                    ticked = {key for key, switch in auto_switches.items() if switch.value} or set(
                        existing.auto_attachments if existing else []
                    )
                    with connection_scope() as connection:
                        stored = {
                            document.key: document for document in leg_document_repo.list_all(connection)
                        }
                    auto_column.clear()
                    auto_switches.clear()
                    entries = auto_attachments.for_occasion(occasion.value)
                    with auto_column:
                        if not entries:
                            ui.label("Für diesen Anlass gibt es keine.").classes("text-caption text-grey-6")
                        for entry in entries:
                            switch = ui.switch(value=entry.key in ticked).props(
                                f'label="{entry.label}" dense'
                            )
                            switch.on_value_change(lambda _=None: note_missing_sources())
                            auto_switches[entry.key] = switch
                            if entry.hint:
                                ui.label(entry.hint).classes("text-caption text-grey-6 q-ml-lg")
                            # Which file is behind the tick, named here rather
                            # than only on the Einstellungen page: the
                            # administrator asked "wie weiss ich welches
                            # dokument angezeigt wird", and a checkbox that
                            # does not say what it attaches is a promise you
                            # have to go and look up.
                            document = stored.get(entry.key)
                            if entry.needs_source:
                                with ui.row().classes("w-full items-center gap-2 q-ml-lg"):
                                    if document is not None:
                                        ui.label(
                                            f"Vorlage: {document.filename} "
                                            f"({format_size(len(document.content))}, "
                                            f"{format_date(document.updated_at)})"
                                        ).classes("text-caption text-grey-7")
                                    # The answer to "wie kann ich prüfen dass
                                    # die erste seite korrekt ausgefüllt
                                    # wird": build it for a real person and
                                    # look. Nothing is sent.
                                    ui.button(
                                        "Ansehen",
                                        on_click=lambda _=None, key=entry.key: open_contract_preview(
                                            document_key=key
                                        ),
                                    ).props("flat dense color=primary")
                    note_missing_sources()

                def note_missing_sources() -> None:
                    """Say which ticked document has no form stored yet."""
                    ticked = [key for key, switch in auto_switches.items() if switch.value]
                    with connection_scope() as connection:
                        stored = leg_document_repo.stored_keys(connection)
                    missing = auto_attachments.missing_sources(ticked, stored)
                    if not missing:
                        missing_note.text = ""
                        return
                    names = ", ".join(entry.label.replace(" anfügen", "") for entry in missing)
                    missing_note.text = (
                        f"⚠ {names}: Es ist noch keine Vorlage hinterlegt. "
                        "Einstellungen → Allgemein → LEG-Dokumente."
                    )

                render_auto_attachments()
                when_occasion_changes.append(render_auto_attachments)

                ui.separator().classes("my-2")
                ui.label("Weitere Anhänge").classes("text-body1 font-bold")
                ui.label(
                    "Eigene Dokumente, die bei jedem Versand dieses Bausteins "
                    "mitgehen -- im Versanddialog einzeln abwählbar."
                ).classes("text-caption text-grey-6")
                attachment_list = ui.column().classes("w-full gap-1")

                def render_attachments() -> None:
                    """Redraw the attachment list, stored ones and new ones."""
                    attachment_list.clear()
                    with attachment_list:
                        stored = []
                        if existing is not None:
                            with connection_scope() as connection:
                                stored = template_repo.list_attachments(connection, existing.id)
                        for attachment in stored:
                            with ui.row().classes("w-full items-center gap-2"):
                                ui.label(
                                    f"{attachment.filename} ({format_size(len(attachment.content))})"
                                ).classes("text-body2")
                                ui.button(
                                    icon="delete",
                                    on_click=lambda _=None, a=attachment: remove_attachment(a.id),
                                ).props("dense flat color=negative").classes("ml-auto")
                        for filename, content in pending:
                            with ui.row().classes("w-full items-center gap-2"):
                                ui.label(f"{filename} ({format_size(len(content))})").classes(
                                    "text-body2 text-primary"
                                )
                                ui.label("wird beim Speichern angehängt").classes("text-caption text-grey-6")
                        if not stored and not pending:
                            ui.label("Keine Anhänge.").classes("text-caption text-grey-6")

                def remove_attachment(attachment_id: int) -> None:
                    """Delete one stored attachment at once."""
                    with connection_scope() as connection:
                        template_repo.delete_attachment(connection, attachment_id)
                    render_attachments()
                    refresh()

                async def handle_upload(event) -> None:
                    """Read the picked files into `pending`."""
                    for file in event.files:
                        filename, content = await read_uploaded_file(file)
                        pending.append((filename, content))
                    render_attachments()
                    upload.reset()

                upload = ui.upload(
                    on_multi_upload=handle_upload,
                    multiple=True,
                    auto_upload=True,
                    max_file_size=graph_client.MAX_INLINE_ATTACHMENT_BYTES,
                    max_total_size=graph_client.MAX_INLINE_ATTACHMENT_BYTES,
                )
                upload.props('label="Datei wählen" accept=".pdf,.docx,.txt" flat bordered')
                render_attachments()

                error_label = ui.label("").classes("text-negative")

                def save() -> None:
                    """Validate the form and persist the template."""
                    if not name.value.strip():
                        error_label.text = "Name darf nicht leer sein."
                        return
                    if not subject.value.strip():
                        error_label.text = "Betreff darf nicht leer sein."
                        return
                    has_steps = occasion.value in STEP_OCCASIONS
                    if has_steps and not step.value:
                        error_label.text = "Bitte den Schritt wählen, zu dem dieser Text gehört."
                        return
                    total = sum(len(content) for _, content in pending)
                    if total > graph_client.MAX_INLINE_ATTACHMENT_BYTES:
                        error_label.text = (
                            f"Die Anhänge sind zusammen zu gross für eine E-Mail ({format_size(total)})."
                        )
                        return

                    record = MessageTemplate(
                        id=existing.id if existing else None,
                        name=name.value.strip(),
                        occasion=occasion.value,
                        step=step.value if has_steps else "",
                        trigger_kind=trigger.value if has_steps else "",
                        deadline_days=(
                            int(deadline.value)
                            if has_steps and trigger.value == TRIGGER_STEP_PENDING and deadline.value
                            else None
                        ),
                        subject=subject.value.strip(),
                        body=body.value,
                        sort_order=existing.sort_order if existing else 100,
                        created_at=existing.created_at if existing else "",
                        # Keys this version does not know are kept: a
                        # template ticked by a later version must not lose
                        # its documents by being opened here.
                        auto_attachments=[key for key, switch in auto_switches.items() if switch.value]
                        + [
                            key
                            for key in (existing.auto_attachments if existing else [])
                            if key not in auto_attachments.BY_KEY
                        ],
                        signature_id=signature.value,
                    )
                    with connection_scope() as connection:
                        if existing:
                            template_repo.update(connection, record, commit=False)
                            template_id = existing.id
                        else:
                            template_id = template_repo.create(connection, record, commit=False)
                        for filename, content in pending:
                            template_repo.add_attachment(
                                connection, template_id, filename, content, commit=False
                            )
                    dialog.close()
                    # notify before refresh() -- see app.gui.safe_notify on why
                    # a plain ui.notify() can raise once the list is rebuilt.
                    safe_notify("Gespeichert.", type="positive")
                    refresh()

                with ui.row().classes("w-full items-center gap-2 mt-2"):
                    error_label.move(target_index=0)
                    ui.button("Abbrechen", on_click=dialog.close).props("flat").classes("ml-auto")
                    ui.button("Speichern", on_click=save)
            form_guard(dialog, on_save=save)
            dialog.open()

        def on_edit(event) -> None:
            """Table row-edit handler: open the dialog for the clicked row."""
            with connection_scope() as connection:
                existing = template_repo.get(connection, event.args["id"])
            if existing is None:
                safe_notify("Diesen Textbaustein gibt es nicht mehr.", type="warning")
                refresh()
                return
            open_form(existing)

        def on_remove(event) -> None:
            """Table row-delete handler: delete after confirmation."""
            row = event.args
            with ui.dialog() as confirm, ui.card():
                ui.label(f'Textbaustein "{row["name"]}" wirklich löschen?')
                ui.label(
                    "Bereits versendete E-Mails behalten ihren Text -- der "
                    "Versandverlauf hängt nicht an diesem Baustein."
                ).classes("text-caption text-grey-7")

                def do_delete() -> None:
                    """Delete the template and close the question."""
                    with connection_scope() as connection:
                        template_repo.delete(connection, row["id"])
                    confirm.close()
                    safe_notify("Gelöscht.", type="warning")
                    refresh()

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Abbrechen", on_click=confirm.close).props("flat")
                    ui.button("Löschen", on_click=do_delete, color="negative")
            confirm.open()

        table.on("edit", on_edit)
        table.on("remove", on_remove)

        refresh()
