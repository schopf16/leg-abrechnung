"""The one running address-register update, and its progress."""

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from app.importers import address_register
from app.importers.address_register import AddressRegisterError
from app.paths import ADDRESS_REGISTER_PATH

_LOGGER = logging.getLogger(__name__)

#: Phase names, also used as the German prefix of the header line.
PHASE_DOWNLOAD = "Herunterladen"
PHASE_PARSE = "Verarbeiten"
PHASE_FINISH = "Abschliessen"


@dataclass
class UpdateState:
    """How the running update is doing, or how the last one ended."""

    phase: str = ""
    progress: float = 0.0
    error: str = ""
    finished: bool = False

    @property
    def running(self) -> bool:
        """Whether an update is in progress."""
        return bool(self.phase)

    @property
    def label(self) -> str:
        """The one-line status for the page header."""
        if not self.running:
            return ""
        return f"Adressregister: {self.phase} {self.progress * 100:.0f} %"


#: The single state object. One update at a time; a second click must not
#: start a second download over the same target file.
STATE = UpdateState()


async def run_update(
    target: Optional[Path] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """Fetch and rebuild the address register, reporting progress as it goes."""
    # Claimed by setting the phase, with no await in between: asyncio is
    # cooperative, so nothing can interleave here and a lock would only add
    # a second thing to get wrong. A second click finds the phase set and
    # returns rather than downloading over the same file.
    if STATE.running:
        return False
    target = target if target is not None else ADDRESS_REGISTER_PATH
    STATE.phase = PHASE_DOWNLOAD
    STATE.error = ""
    STATE.finished = False
    STATE.progress = 0.0

    scratch = Path(tempfile.mkdtemp()) / "adressregister.csv.zip"
    try:
        asset = await address_register.fetch_asset(client=client)

        def report(fraction: float) -> None:
            """Record download progress for the header line."""
            STATE.progress = fraction

        await address_register.download_asset(asset, scratch, on_progress=report, client=client)

        STATE.phase = PHASE_PARSE
        STATE.progress = 0.0
        for fraction in address_register.build_register(scratch, target, data_date=asset.data_date):
            STATE.progress = fraction
            # The one line that keeps the window usable: hand control back
            # to the event loop between batches of rows.
            await asyncio.sleep(0)

        # In a thread, not here: indexing is a single 1.8-second SQLite
        # statement that no amount of yielding can break up, and it blocked
        # the loop long enough for NiceGUI's one-second browser round trips
        # to time out. SQLite releases the GIL while it works, so a thread
        # is enough.
        STATE.phase = PHASE_FINISH
        STATE.progress = 1.0
        await asyncio.to_thread(address_register.finalise_register, target, asset.data_date)
        STATE.finished = True
        return True
    except AddressRegisterError as exc:
        STATE.error = str(exc)
        _LOGGER.warning("Adressregister-Update fehlgeschlagen: %s", exc)
        return False
    except OSError as exc:
        STATE.error = f"Datei konnte nicht geschrieben werden: {exc}"
        _LOGGER.warning("Adressregister-Update fehlgeschlagen: %s", exc)
        return False
    finally:
        STATE.phase = ""
        STATE.progress = 0.0
        scratch.unlink(missing_ok=True)
        try:
            scratch.parent.rmdir()
        except OSError:
            # The directory is a throwaway; failing to remove it is not
            # worth surfacing to anybody.
            pass
