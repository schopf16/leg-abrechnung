"""Tests for the update task and the Adressregister page."""

import asyncio
from pathlib import Path

import httpx
import pytest
from nicegui import Client, ui

from app.gui import address_register_task as task_module
from app.gui.address_register_task import PHASE_DOWNLOAD, PHASE_PARSE, STATE, run_update
from app.importers.address_register import read_info
from tests.conftest import write_register_zip

#: An allowed host (see `_ALLOWED_HOSTS`), because the importer refuses
#: anything else. `MockTransport` intercepts the call regardless, so no
#: request leaves the machine.
_ASSET_URL = (
    "https://data.geo.admin.ch/ch.swisstopo.amtliches-gebaeudeadressverzeichnis/"
    "amtliches-gebaeudeadressverzeichnis_ch_2056.csv.zip"
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Leave the module-level state clean for the next test."""
    yield
    STATE.phase = ""
    STATE.progress = 0.0
    STATE.error = ""
    STATE.finished = False


def _client(zip_bytes: bytes, *, stac_status: int = 200, asset_status: int = 200):
    """Build an httpx client that answers both requests from memory."""
    payload = {
        "features": [
            {
                "properties": {"datetime": "2026-10-01T04:43:15Z"},
                "assets": {
                    "amtliches-gebaeudeadressverzeichnis_ch_2056.csv.zip": {
                        "href": _ASSET_URL,
                        "file:size": len(zip_bytes),
                    }
                },
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer the catalogue and the download, nothing else."""
        if "stac" in str(request.url):
            return httpx.Response(stac_status, json=payload)
        if str(request.url) == _ASSET_URL:
            return httpx.Response(asset_status, content=zip_bytes)
        raise AssertionError(f"unerwarteter Aufruf: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(target: Path, client) -> bool:
    """Drive `run_update` from a synchronous test."""

    async def go() -> bool:
        async with client:
            return await run_update(target=target, client=client)

    return asyncio.run(go())


def test_an_update_downloads_and_builds_the_register(tmp_path):
    """The whole path, end to end, with nothing on the network."""
    zip_bytes = write_register_zip(tmp_path / "src.zip").read_bytes()
    target = tmp_path / "adressregister.sqlite3"

    assert _run(target, _client(zip_bytes)) is True

    info = read_info(target)
    assert info is not None
    assert (info.streets, info.addresses) == (4, 7)
    assert str(info.data_date) == "2026-10-01"


def test_the_data_date_comes_from_the_catalogue_not_from_today(tmp_path):
    """Two different statements: when swisstopo published, and when we got it."""
    zip_bytes = write_register_zip(tmp_path / "src.zip").read_bytes()
    target = tmp_path / "reg.sqlite3"

    _run(target, _client(zip_bytes))

    info = read_info(target)
    assert str(info.data_date) == "2026-10-01"
    assert info.downloaded_at is not None


def test_progress_passes_through_both_phases(tmp_path):
    """Download then parse, each reported, so the bar is never just a spinner."""
    zip_bytes = write_register_zip(tmp_path / "src.zip").read_bytes()
    seen: list[tuple[str, float]] = []
    real_build = task_module.address_register.build_register

    def spy(*args, **kwargs):
        """Record the phase while the parse generator runs."""
        for value in real_build(*args, **kwargs):
            seen.append((STATE.phase, value))
            yield value

    original = task_module.address_register.build_register
    task_module.address_register.build_register = spy
    try:
        _run(tmp_path / "reg.sqlite3", _client(zip_bytes))
    finally:
        task_module.address_register.build_register = original

    assert seen, "der Parser hat keinen Fortschritt gemeldet"
    assert {phase for phase, _ in seen} == {PHASE_PARSE}
    assert seen[-1][1] == 1.0


def test_the_state_is_idle_again_afterwards(tmp_path):
    """Otherwise the header would keep claiming an update is running."""
    zip_bytes = write_register_zip(tmp_path / "src.zip").read_bytes()

    _run(tmp_path / "reg.sqlite3", _client(zip_bytes))

    assert STATE.running is False
    assert STATE.label == ""
    assert STATE.finished is True


def test_a_second_run_is_refused_while_one_is_in_flight(tmp_path):
    """One download at a time, or two writers meet over the same file."""
    STATE.phase = PHASE_DOWNLOAD

    async def go() -> bool:
        return await run_update(target=tmp_path / "reg.sqlite3")

    assert asyncio.run(go()) is False


def test_a_failed_download_reports_a_german_reason_and_keeps_the_old_file(tmp_path):
    """A failure must cost the message, not the working register."""
    zip_bytes = write_register_zip(tmp_path / "src.zip").read_bytes()
    target = tmp_path / "reg.sqlite3"
    _run(target, _client(zip_bytes))
    before = target.read_bytes()

    assert _run(target, _client(zip_bytes, asset_status=500)) is False

    assert STATE.error
    assert target.read_bytes() == before


def test_an_unreachable_catalogue_is_reported(tmp_path):
    """The URL is read from STAC, so that request can fail on its own."""
    zip_bytes = write_register_zip(tmp_path / "src.zip").read_bytes()

    assert _run(tmp_path / "reg.sqlite3", _client(zip_bytes, stac_status=503)) is False

    assert "swisstopo" in STATE.error


def test_a_corrupt_download_is_reported_and_removed(tmp_path):
    """Says what happened instead of leaving an unreadable register behind."""
    target = tmp_path / "reg.sqlite3"

    assert _run(target, _client(b"das ist kein Archiv")) is False

    assert STATE.error
    assert not target.exists()


# --- The page -------------------------------------------------------------


def _render() -> Client:
    """Render the Adressregister page."""
    from app.gui.pages import address_register as page_module

    client = Client(ui.page("/probe-address-register")(lambda: None), request=None)
    with client:
        page_module.address_register_page()
    return client


def _texts(client: Client) -> list[str]:
    """Every label text on the page."""
    return [
        element.text
        for element in client.elements.values()
        if element.__class__.__name__ == "Label" and getattr(element, "text", None)
    ]


def test_the_page_says_when_nothing_has_been_downloaded():
    """The one place the absence of a register is stated."""
    assert "Noch nicht heruntergeladen." in _texts(_render())


def test_the_page_names_the_data_date_and_the_counts(address_register):
    """A date and two figures, so "is this current" is answerable."""
    texts = " ".join(_texts(_render()))

    assert "Datenstand" in texts
    assert "4 Strassen" in texts
    assert "7 Adressen" in texts


def test_the_page_carries_the_required_source_reference(address_register):
    """swisstopo's terms allow everything except dropping the attribution."""
    assert any("©swisstopo" in text for text in _texts(_render()))


def test_the_page_states_that_no_address_leaves_the_machine(address_register):
    """The reason the whole register is downloaded instead of queried."""
    assert any("keine Adresse abgefragt" in text for text in _texts(_render()))
