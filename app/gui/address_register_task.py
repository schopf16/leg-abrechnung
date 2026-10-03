"""The one running address-register update, and its progress.

State lives in a **module-level object**, not on a client, on purpose. The
update takes about a minute, and the administrator is meant to keep working
during it -- so they will navigate away, and a progress bar that belonged to
the page they left would vanish and leave them unable to tell whether the
update had finished. `app.gui.navigation.page_frame` reads this state and
shows a line in the header of every page instead.

Why not `nicegui.run.cpu_bound`: the parse would then sit in another process
and reporting progress out of it needs a queue, pickled arguments and a
second failure mode. Here the parse is a generator that hands control back
every few thousand rows (see `app.importers.address_register.build_register`),
which keeps the window responsive with no concurrency machinery at all --
the download half genuinely awaits network I/O and blocks nothing.

The pattern in `app.gui.pages.import_page` yields *between* files and lets
each file's work block; that is fine for many small files and useless for
one long operation, which is why this module exists.
"""

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


@dataclass
class UpdateState:
    """How the running update is doing, or how the last one ended.

    Attributes:
        phase: `PHASE_DOWNLOAD`, `PHASE_PARSE`, or `""` when idle.
        progress: 0.0..1.0 within the current phase.
        error: German message of the last failure, `""` otherwise. Kept
            after the run so the administrator can still read it on the
            register page, rather than losing it with a toast they missed.
        finished: Set once a run has completed successfully, so the page can
            say so without having to guess from `phase`.
    """

    phase: str = ""
    progress: float = 0.0
    error: str = ""
    finished: bool = False

    @property
    def running(self) -> bool:
        """Whether an update is in progress.

        Returns:
            `True` while a phase is set.
        """
        return bool(self.phase)

    @property
    def label(self) -> str:
        """The one-line status for the page header.

        Returns:
            E.g. `"Adressregister: Verarbeiten 68 %"`, or `""` when idle.
        """
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
    """Fetch and rebuild the address register, reporting progress as it goes.

    Args:
        target: Where the finished register goes.
        client: An open HTTP client, or `None` for a short-lived one. Tests
            pass one in so nothing reaches the network.

    Returns:
        `True` on success, `False` if it failed or was already running. The
        German reason for a failure is left in `STATE.error`.
    """
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
            """Record download progress for the header line.

            Args:
                fraction: How much of the file has arrived, 0.0..1.0.

            Returns:
                None.
            """
            STATE.progress = fraction

        await address_register.download_asset(asset, scratch, on_progress=report, client=client)

        STATE.phase = PHASE_PARSE
        STATE.progress = 0.0
        for fraction in address_register.build_register(scratch, target, data_date=asset.data_date):
            STATE.progress = fraction
            # The one line that keeps the window usable: hand control back
            # to the event loop between batches of rows.
            await asyncio.sleep(0)
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
