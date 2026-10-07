"""LEG-wide settings page: sender address, QR-IBAN, price, admin fees (shared across all LEGs -- see
`app.models.leg` for the per-LEG name), MeteringPoint Land/identifier defaults, and demo data
generation."""

from nicegui import ui

from app.db.connection import connection_scope
from app.gui.address_input import SuggestionBox
from app.domain.demo_data import DemoDataAlreadyExists, create_demo_data
from app.domain.iban_validation import iban_entry_is_complete, normalize_iban, validate_qr_iban
from app.domain.metering_point_validation import validate_identifier, validate_country
from app.emailing import graph_client
from app.domain import auto_attachments
from app.format_size import format_size
from app.formatting import format_date
from app.gui.upload import read_uploaded_file
from app.models import leg_document as leg_document_repo
from app.gui.safe_notify import safe_notify
from app.gui.navigation import page_frame
from app.models import settings as settings_repo


@ui.page("/settings")
def settings_page() -> None:
    """Render the LEG-wide settings page."""
    with page_frame("/settings", "Allgemein"):
        with connection_scope() as connection:
            current = settings_repo.get_settings(connection)

        ui.label(
            "Diese Angaben gelten für alle LEGs. Der Name auf der Rechnung "
            "kommt von der jeweiligen LEG -- siehe „LEGs“."
        ).classes("text-body2 text-grey-8")

        ui.label("Absender und Abrechnung").classes("text-lg font-bold mt-4")
        ui.label(
            "Absender und Zahlungsempfänger jeder QR-Rechnung, interner Strompreis und Gebühren."
        ).classes("text-body2 text-grey-8")

        with ui.card().classes("w-full max-w-lg"):
            with ui.row().classes("w-full gap-2"):
                street = ui.input("Strasse", value=current.address_street).classes("flex-grow")
                house_number = ui.input("Hausnummer", value=current.address_house_number).classes("w-28")
            street_hint = ui.column().classes("w-full gap-0")
            with ui.row().classes("w-full gap-2"):
                zip_code = ui.input("PLZ", value=current.address_zip).classes("w-24")
                city = ui.input("Ort", value=current.address_city).classes("flex-grow")
            locality_hint = ui.column().classes("w-full gap-0")
            country = ui.input("Land", value=current.address_country or "CH").classes("w-full")
            # The most consequential address in the app: it is the creditor
            # on every QR-bill (app/pdf/qr_bill_render.py) and the letterhead
            # of every document, entered once and never looked at again.
            #
            # The house number is its own field since migration 54. With both
            # in one box the check compared "Im Feld 3" against street names,
            # found nothing, offered "Im Feld" -- and accepting that wrote it
            # over the whole value, so the number was gone. Passed to the
            # box now, exactly as the Standort and Person dialogs do.
            SuggestionBox(
                street,
                zip_code,
                city,
                house_number,
                street_hint=street_hint,
                locality_hint=locality_hint,
            )
            qr_iban = ui.input("QR-IBAN", value=current.qr_iban).classes("w-full")
            qr_iban_error = ui.label("").classes("text-negative text-caption")

            def check_qr_iban(finished: bool) -> None:
                """Validate on blur, or while typing once the country's full length is reached."""
                value = qr_iban.value or ""
                complete = finished or iban_entry_is_complete(value)
                qr_iban_error.text = (validate_qr_iban(value) or "") if complete else ""

            qr_iban.on_value_change(lambda _: check_qr_iban(finished=False))
            qr_iban.on("blur", lambda: check_qr_iban(finished=True))
            price = ui.number(
                "Interner Strompreis (Rp./kWh)",
                value=current.price_rp_per_kwh,
                min=0,
                step=0.1,
                format="%.2f",
            ).classes("w-full")
            admin_fee_consumption = ui.number(
                "Verwaltungsaufwand Bezug (Rp./kWh)",
                value=current.admin_fee_consumption_rp_per_kwh,
                min=0,
                step=0.01,
                format="%.4f",
            ).classes("w-full")
            admin_fee_feed_in = ui.number(
                "Verwaltungsaufwand Einspeisung (Rp./kWh)",
                value=current.admin_fee_feed_in_rp_per_kwh,
                min=0,
                step=0.01,
                format="%.4f",
            ).classes("w-full")
            paper_invoice_fee = ui.number(
                "Kosten Papierrechnung (CHF, pro Abrechnung)",
                value=current.paper_invoice_rappen / 100,
                min=0,
                step=0.5,
                format="%.2f",
            ).classes("w-full")
            error_label = ui.label("").classes("text-negative")

            def save() -> None:
                """Validate and persist the LEG-wide settings form."""
                if price.value is None or price.value < 0:
                    error_label.text = "Preis muss positiv sein."
                    return
                if admin_fee_consumption.value is None or admin_fee_consumption.value < 0:
                    error_label.text = "Verwaltungsaufwand Bezug muss positiv sein."
                    return
                if admin_fee_feed_in.value is None or admin_fee_feed_in.value < 0:
                    error_label.text = "Verwaltungsaufwand Einspeisung muss positiv sein."
                    return
                if paper_invoice_fee.value is None or paper_invoice_fee.value < 0:
                    error_label.text = "Kosten Papierrechnung müssen positiv sein."
                    return
                qr_iban_problem = validate_qr_iban(qr_iban.value)
                if qr_iban_problem:
                    qr_iban_error.text = qr_iban_problem
                    error_label.text = qr_iban_problem
                    return
                # Re-read rather than writing back `current`, which was
                # loaded when the page was built: every other form on this
                # page saves independently, so a stale snapshot silently
                # reverted whatever they had changed in the meantime. Only
                # the fields this form owns are touched.
                with connection_scope() as connection:
                    updated = settings_repo.get_settings(connection)
                    updated.address_street = street.value.strip()
                    updated.address_house_number = house_number.value.strip()
                    updated.address_zip = zip_code.value.strip()
                    updated.address_city = city.value.strip()
                    updated.address_country = country.value.strip() or "CH"
                    updated.qr_iban = normalize_iban(qr_iban.value)
                    updated.price_rp_per_kwh = float(price.value)
                    updated.admin_fee_consumption_rp_per_kwh = float(admin_fee_consumption.value)
                    updated.admin_fee_feed_in_rp_per_kwh = float(admin_fee_feed_in.value)
                    updated.paper_invoice_rappen = round(float(paper_invoice_fee.value) * 100)
                    settings_repo.update_settings(connection, updated)
                error_label.text = ""
                ui.notify("Einstellungen gespeichert.", type="positive")

            ui.button("Speichern", on_click=save).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("Messpunkt-Vorgaben").classes("text-lg font-bold")
        ui.label(
            "Land und VSE-Identifikator des Netzbetreibers sind bei allen "
            "Messpunkten dieser LEG identisch. Hier hinterlegt, werden sie "
            "beim Anlegen eines neuen Messpunkts als Vorschlag "
            "eingesetzt (dort weiterhin veränderbar)."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            with ui.row().classes("w-full gap-2"):
                metering_point_country = ui.input(
                    "Land", value=current.metering_point_country or "CH"
                ).classes("w-24")
                metering_point_identifier = ui.input(
                    "VSE-Identifikator (11-stellig)",
                    value=current.metering_point_identifier,
                ).classes("flex-grow")
            metering_point_defaults_error = ui.label("").classes("text-negative")

            def save_metering_point_defaults() -> None:
                """Validate and persist the MeteringPoint Land/identifier defaults."""
                country_value = metering_point_country.value.strip().upper()
                identifier_value = metering_point_identifier.value.strip().upper()
                country_problem = validate_country(country_value)
                if country_problem:
                    metering_point_defaults_error.text = country_problem
                    return
                identifier_problem = validate_identifier(identifier_value)
                if identifier_problem:
                    metering_point_defaults_error.text = identifier_problem
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.metering_point_country = country_value or "CH"
                    settings.metering_point_identifier = identifier_value
                    settings_repo.update_settings(connection, settings)
                metering_point_defaults_error.text = ""
                ui.notify("Messpunkt-Vorgaben gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_metering_point_defaults).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("Aufnahmeprozess").classes("text-lg font-bold")
        ui.label(
            "Wie lange ein Schritt offen sein darf, und die Formulare, die beim Aufnehmen mitgehen."
        ).classes("text-body2 text-grey-8")

        ui.label("Überfällige Schritte").classes("text-body1 font-bold mt-2")
        ui.label(
            "Ab wie vielen Tagen ohne Fortschritt beim aktuellen Schritt "
            "einer Aufnahme (siehe „Aufnahmen“) diese in den Auswertungen "
            "und auf der Übersicht als überfällig gemeldet wird."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            onboarding_overdue_days = ui.number(
                "Überfällig nach (Tagen)",
                value=current.onboarding_overdue_days,
                min=1,
                step=1,
                format="%.0f",
            ).classes("w-full")
            onboarding_error = ui.label("").classes("text-negative")

            def save_onboarding_threshold() -> None:
                """Validate and persist the onboarding overdue threshold."""
                if onboarding_overdue_days.value is None or onboarding_overdue_days.value < 1:
                    onboarding_error.text = "Muss mindestens 1 Tag sein."
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.onboarding_overdue_days = int(onboarding_overdue_days.value)
                    settings_repo.update_settings(connection, settings)
                onboarding_error.text = ""
                ui.notify("Aufnahmeprozess-Einstellung gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_onboarding_threshold).classes("mt-2")

        ui.label("LEG-Dokumente").classes("text-body1 font-bold mt-4")
        ui.label(
            "Formulare, die ein Textbaustein anfügen kann (siehe "
            "„Textbausteine“). Einmal hier hinterlegt, gilt für alle "
            "Bausteine -- eine neue Fassung ersetzt die alte an dieser "
            "einen Stelle. Die Datei liegt in der Datenbank und ist damit "
            "in jedem Backup."
        ).classes("text-body2 text-grey-8")

        documents_column = ui.column().classes("w-full gap-2")

        def render_documents() -> None:
            """Draw one row per form that a checkbox can attach."""
            with connection_scope() as connection:
                stored = {document.key: document for document in leg_document_repo.list_all(connection)}
            documents_column.clear()
            with documents_column:
                for entry in auto_attachments.AUTO_ATTACHMENTS:
                    if not entry.needs_source:
                        continue
                    document = stored.get(entry.key)
                    with ui.card().classes("w-full"):
                        with ui.row().classes("w-full items-center gap-3"):
                            ui.label(entry.label.replace(" anfügen", "")).classes("font-bold")
                            if document is None:
                                ui.label("Noch keine Datei hinterlegt.").classes("text-warning text-body2")
                            else:
                                ui.label(
                                    f"{document.filename} "
                                    f"({format_size(len(document.content))}, "
                                    f"{format_date(document.updated_at)})"
                                ).classes("text-body2")
                                ui.button(
                                    icon="delete",
                                    on_click=lambda _=None, key=entry.key: remove_document(key),
                                ).props("dense flat color=negative").classes("ml-auto")

                        async def handle_upload(event, key=entry.key) -> None:
                            """Store the picked file as this form."""
                            for file in event.files:
                                filename, content = await read_uploaded_file(file)
                                if len(content) > graph_client.MAX_INLINE_ATTACHMENT_BYTES:
                                    safe_notify(
                                        "Die Datei ist zu gross für eine E-Mail "
                                        f"({format_size(len(content))}).",
                                        type="negative",
                                    )
                                    continue
                                with connection_scope() as connection:
                                    leg_document_repo.put(connection, key, filename, content)
                            safe_notify("Datei hinterlegt.", type="positive")
                            render_documents()

                        upload = ui.upload(on_multi_upload=handle_upload, multiple=False, auto_upload=True)
                        upload.props('label="Datei wählen" accept=".pdf" flat bordered dense')
                        if entry.hint:
                            ui.label(entry.hint).classes("text-caption text-grey-6")

        def remove_document(key: str) -> None:
            """Remove one stored form."""
            with connection_scope() as connection:
                leg_document_repo.delete(connection, key)
            safe_notify("Datei entfernt.", type="warning")
            render_documents()

        render_documents()

        ui.separator().classes("my-6")

        ui.label("Produktionsleistung").classes("text-lg font-bold")
        ui.label(
            "Eine LEG braucht laut Art. 19e Abs. 1 StromVV eine "
            "Produktionsleistung von mindestens 5 % der Anschlussleistung "
            "aller Teilnehmenden. Diese 5 % sind gesetzlich und nicht "
            "änderbar. Hier legen Sie nur fest, ab welchem Wert die App "
            "schon vorher warnt, damit ein neuer Bezüger rechtzeitig einer "
            "anderen LEG zugewiesen werden kann, statt erst beim "
            "Unterschreiten. Den aktuellen Prozentwert tragen Sie pro LEG "
            "ein; er stammt aus dem BKW-LEG-Portal."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            production_capacity_warn_percent = ui.number(
                "Warnen unterhalb von (%)",
                value=current.production_capacity_warn_percent,
                min=5.5,
                step=0.5,
            ).classes("w-full")
            capacity_error = ui.label("").classes("text-negative")

            def save_production_capacity_warn_percent() -> None:
                """Validate and persist the production-capacity warning threshold."""
                value = production_capacity_warn_percent.value
                # Exactly 5 would empty the warning band entirely: a LEG
                # sitting on the legal floor would show a green tick.
                if value is None or value <= 5:
                    capacity_error.text = (
                        "Muss über 5 % liegen -- 5 % ist die gesetzliche Grenze selbst, keine Vorwarnung."
                    )
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.production_capacity_warn_percent = float(value)
                    settings_repo.update_settings(connection, settings)
                capacity_error.text = ""
                ui.notify("Warnschwelle gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_production_capacity_warn_percent).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("Mahnwesen").classes("text-lg font-bold")
        ui.label(
            "Zwei Stufen gemäss Reglement: die 1. Mahnung gewährt eine neue "
            "Frist, die 2. Mahnung löst die Ausschluss-Prüfung aus (siehe "
            "„Debitoren“/„Mahnwesen“) -- keine Mahngebühr auf irgendeiner "
            "Stufe. Die beiden Texte stehen unter „Textbausteine“."
        ).classes("text-body2 text-grey-8")
        with ui.card().classes("w-full max-w-lg"):
            dunning_new_deadline_days = ui.number(
                "Neue Zahlungsfrist nach 1. Mahnung (Tage)",
                value=current.dunning_new_deadline_days,
                min=1,
                step=1,
                format="%.0f",
            ).classes("w-full")
            dunning_minimum = ui.number(
                "Bagatellgrenze (CHF, darunter keine Mahnung)",
                value=current.dunning_minimum_rappen / 100,
                min=0,
                step=1,
                format="%.2f",
            ).classes("w-full")

            dunning_error = ui.label("").classes("text-negative")

            def save_dunning() -> None:
                """Validate and persist the dunning settings and templates."""
                if dunning_new_deadline_days.value is None or dunning_new_deadline_days.value < 1:
                    dunning_error.text = "Neue Zahlungsfrist muss mindestens 1 Tag sein."
                    return
                if dunning_minimum.value is None or dunning_minimum.value < 0:
                    dunning_error.text = "Bagatellgrenze muss positiv sein."
                    return
                with connection_scope() as connection:
                    settings = settings_repo.get_settings(connection)
                    settings.dunning_new_deadline_days = int(dunning_new_deadline_days.value)
                    settings.dunning_minimum_rappen = round(dunning_minimum.value * 100)
                    settings_repo.update_settings(connection, settings)
                dunning_error.text = ""
                ui.notify("Mahnwesen-Einstellungen gespeichert.", type="positive")

            ui.button("Speichern", on_click=save_dunning).classes("mt-2")

        ui.separator().classes("my-6")

        ui.label("Demo-Daten").classes("text-lg font-bold")
        ui.markdown(
            "Erzeugt eine Beispiel-LEG mit Standorten, fünf "
            "Beispiel-Personen (zwei Prosumer, drei reine Bezüger) mit "
            "Messpunkten und Zuordnungen inkl. eines Umzug-Beispiels, "
            "sowie synthetische 15-Minuten-Messwerte für ein Winter- und "
            "ein Sommer-Quartal 2025 zum Ausprobieren der App."
        ).classes("text-body2")

        def generate_demo_data() -> None:
            """Run the demo data generator and report the outcome via a toast."""
            try:
                with connection_scope() as connection:
                    summary = create_demo_data(connection)
            except DemoDataAlreadyExists as exc:
                ui.notify(str(exc), type="warning")
                return
            ui.notify(
                f"Demo-Daten erzeugt: {len(summary.person_ids)} Personen, "
                f"{len(summary.metering_point_ids)} Messpunkte, {summary.reading_count} Messwerte.",
                type="positive",
            )

        ui.button("Demo-Daten erzeugen", on_click=generate_demo_data, color="secondary").classes("mt-2")
