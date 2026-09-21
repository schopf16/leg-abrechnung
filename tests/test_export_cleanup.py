"""Tests for exporting all LEGs at once and for not leaving stale documents.

Both exist because of the same incident: a quarter re-billed at a
corrected price wrote a second set of documents beside the first, and the
old ones -- indistinguishable in a file listing, wrong in the one figure
that matters -- stayed behind.
"""

from app.domain.billing import create_billing_runs_for_all_legs, create_or_replace_billing_run
from app.domain.demo_data import SUMMER_QUARTER, create_demo_data
from app.models import leg as leg_repo
from app.models import settings as settings_repo
from app.pdf.export_service import export_billing_run_documents


def _export_summer(db):
    """Bill and export the demo summer quarter for the first LEG.

    Args:
        db: Database connection fixture.

    Returns:
        The `ExportResult`.
    """
    leg = leg_repo.list_all(db)[0]
    run, _, _, _ = create_or_replace_billing_run(db, leg.id, *SUMMER_QUARTER)
    return export_billing_run_documents(db, run)


def test_re_exporting_a_quarter_leaves_exactly_one_set_of_documents(db, monkeypatch, tmp_path):
    """A corrected run must not leave the superseded documents behind.

    Regression test for the real incident: after changing the energy
    price, the folder held `Abrechnung_Muster_1.pdf` (old price) beside
    `Abrechnung_Muster_7.pdf` (new one), and nothing in the filename said
    which was current.
    """
    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    create_demo_data(db)

    first = _export_summer(db)
    assert first.document_paths
    assert first.removed_paths == [], "beim ersten Export gibt es nichts zu ersetzen"
    first_names = {path.name for path in first.output_dir.glob("Abrechnung_*.pdf")}

    # Re-bill at a different price: the items get fresh ids, so the new
    # documents land under different filenames than the old ones.
    settings = settings_repo.get_settings(db)
    settings.price_rp_per_kwh = settings.price_rp_per_kwh + 4.0
    settings_repo.update_settings(db, settings)
    second = _export_summer(db)

    second_names = {path.name for path in second.output_dir.glob("Abrechnung_*.pdf")}
    assert second_names, "der zweite Lauf muss Belege erzeugen"
    assert second_names.isdisjoint(first_names), "die Positionsnummern müssen sich unterscheiden"
    assert second_names == {path.name for path in second.document_paths}
    assert len(second.removed_paths) == len(first_names)
    assert not any(path.exists() for path in second.removed_paths)


def test_the_export_never_removes_files_it_did_not_write(db, monkeypatch, tmp_path):
    """Anything else in the folder belongs to the administrator."""
    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    create_demo_data(db)
    first = _export_summer(db)

    foreign = first.output_dir / "Notizen zur Kontrolle.txt"
    foreign.write_text("von Hand abgelegt", encoding="utf-8")
    signed = first.output_dir / "Abrechnung unterschrieben.pdf"
    signed.write_bytes(b"%PDF-1.4 handsigniert")

    _export_summer(db)

    assert foreign.exists(), "eine fremde Datei darf der Export nicht anfassen"
    assert signed.exists(), "auch eine PDF ohne unser Namensmuster bleibt liegen"


def test_all_legs_are_billed_and_exported_in_one_pass(db, monkeypatch, tmp_path):
    """One pass covers every LEG, so none can be forgotten."""
    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    create_demo_data(db)
    legs = leg_repo.list_all(db)

    outcomes = create_billing_runs_for_all_legs(db, *SUMMER_QUARTER)

    assert [o.leg.id for o in outcomes] == [leg.id for leg in legs]
    assert all(o.succeeded for o in outcomes)
    for outcome in outcomes:
        assert outcome.run is not None
        assert outcome.export is not None
        assert outcome.export.document_paths
        assert outcome.control_check.balanced


def test_one_failing_leg_does_not_stop_the_others(db, monkeypatch, tmp_path):
    """A LEG that cannot be exported must not cost the other LEGs their run."""
    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    create_demo_data(db)

    calls = {"n": 0}
    real_export = export_billing_run_documents

    def failing_export(connection, run):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Zielordner nicht beschreibbar")
        return real_export(connection, run)

    monkeypatch.setattr("app.pdf.export_service.export_billing_run_documents", failing_export)

    outcomes = create_billing_runs_for_all_legs(db, *SUMMER_QUARTER)

    assert outcomes[0].error is not None
    assert "Export fehlgeschlagen" in outcomes[0].error
    # The run itself is saved even when its export fails -- losing the
    # computation over a filesystem problem would be the worse outcome.
    assert outcomes[0].run is not None
    assert outcomes[0].items


def test_billing_without_export_touches_no_files(db, monkeypatch, tmp_path):
    """`export=False` is what tests and dry runs use."""
    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    create_demo_data(db)

    outcomes = create_billing_runs_for_all_legs(db, *SUMMER_QUARTER, export=False)

    assert all(o.succeeded for o in outcomes)
    assert all(o.export is None for o in outcomes)
    assert not list(tmp_path.rglob("*.pdf"))


# --- The click path -----------------------------------------------------
#
# Everything above drives the domain directly and would still pass with
# the new button wired to nothing at all. This renders the real page and
# presses it, which is the step "the page renders" never reaches (see
# CLAUDE.md on the nicegui 3.16 upload handler).


def test_the_all_legs_button_actually_bills_every_leg(monkeypatch, tmp_path):
    """Pressing "Alle LEGs abrechnen und exportieren" runs the whole pass.

    Sets its data up through `connection_scope()`, because that is the
    connection the page itself opens -- the `db` fixture is a separate
    in-memory database the GUI never sees.
    """
    from nicegui import Client, ui

    from app.db.connection import connection_scope
    from app.gui.pages import billing as billing_page_module
    from app.models import billing_cycle as billing_cycle_repo
    from app.models import billing_run as billing_run_repo

    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    with connection_scope() as connection:
        create_demo_data(connection)
        # The page takes its quarter from the selected billing run, so
        # one has to exist before anything can be computed at all.
        billing_cycle_repo.start_for_period(connection, *SUMMER_QUARTER)
        legs = leg_repo.list_all(connection)
        assert not billing_run_repo.list_runs(connection)

    client = Client(ui.page("/probe-billing")(lambda: None), request=None)
    with client:
        billing_page_module.billing_page()

    buttons = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Button"
        and element._props.get("label") == "Alle LEGs abrechnen und exportieren"
    ]
    assert len(buttons) == 1, "genau einen Alle-LEGs-Knopf erwartet"

    # The button opens the rate-confirmation dialog; pressing it is only
    # half the path, so the dialog's own confirm button is pressed too --
    # that gate is the last place a wrong price can be caught.
    _press(buttons[0])
    confirm = [
        element
        for element in client.elements.values()
        if element.__class__.__name__ == "Button"
        and (element._props.get("label") or "").endswith("alle LEGs abrechnen")
    ]
    assert len(confirm) == 1, "der Bestätigungsdialog muss einen eigenen Knopf haben"
    _press(confirm[0])

    with connection_scope() as connection:
        runs = billing_run_repo.list_runs(connection)
    assert {run.leg_id for run in runs} == {leg.id for leg in legs}
    assert list(tmp_path.rglob("Abrechnung_*.pdf")), "der Durchgang muss Belege geschrieben haben"


def _press(button) -> None:
    """Invoke a button's registered click handler directly.

    Args:
        button: The NiceGUI button element to press.

    Returns:
        None.
    """
    for listener in button._event_listeners.values():
        if listener.type == "click":
            listener.handler(None)
            return
    raise AssertionError(f"Knopf {button._props.get('label')!r} hat keinen Click-Handler")


def test_a_document_that_cannot_be_deleted_does_not_fail_the_export(db, monkeypatch, tmp_path):
    """An open PDF viewer must not turn a finished export into a failure.

    On Windows a file held open cannot be unlinked. By the time the
    cleanup runs the new documents are already on disk, so the export
    succeeded -- reporting it as failed would be worse than the stale
    file it is warning about, and would make the all-LEGs pass mark a
    perfectly good run as broken.
    """
    monkeypatch.setattr("app.pdf.export_service.OUTPUT_DIR", tmp_path)
    create_demo_data(db)
    first = _export_summer(db)
    doomed = sorted(first.output_dir.glob("Abrechnung_*.pdf"))[0]

    real_unlink = type(doomed).unlink

    def refuse_one(self, *args, **kwargs):
        if self.name == doomed.name:
            raise PermissionError(13, "Zugriff verweigert")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(type(doomed), "unlink", refuse_one)

    settings = settings_repo.get_settings(db)
    settings.price_rp_per_kwh = settings.price_rp_per_kwh + 1.0
    settings_repo.update_settings(db, settings)
    second = _export_summer(db)

    assert second.document_paths, "der Export muss trotzdem Belege geschrieben haben"
    assert doomed.exists(), "die gesperrte Datei bleibt liegen"
    assert doomed not in second.removed_paths
    assert any(doomed.name in message for message in second.errors), (
        "der Anwender muss erfahren, dass ein überholter Beleg liegen blieb"
    )
