"""Builds the local copy of swisstopo's official building address register.

The register (`ch.swisstopo.amtliches-gebaeudeadressverzeichnis`) is the
binding list of Swiss building addresses. It is free to use, redistribute and
even use commercially; the one condition is naming the source, which
`app/gui/pages/address_register.py` does. **Swiss Post's address data was
considered and rejected**: its licence forbids passing the data on -- the
`swissmatch-location` project had to stop shipping it after a licence change
-- so it could never be part of a published application, however good the
data is.

Downloaded wholesale rather than queried per address, and that is a privacy
decision, not only an offline one. geo.admin.ch offers a free fuzzy-search
API over the same data, but using it would send fragments of a member's
address to a federal server on every keystroke in an address field. With the
register on disk, no address ever leaves the machine.

Three details of the build are deliberate:

- **Streamed, never extracted.** The ZIP member is read as a stream.
  Extracting by the names inside the archive is how zip-slip happens, and
  every path here is chosen by the application instead.
- **Built beside, then swapped.** The new file is assembled under a
  temporary name and only then moved into place, so a download that dies
  half-way leaves a working register alone rather than a broken one.
- **Normalised into two tables.** Street name, locality and municipality
  repeat across 3.3 million rows; keeping them in a `street` table takes the
  file from 358 MB to 127 MB. `PRAGMA synchronous = OFF` is used during the
  build, which is defensible here precisely because the file is derived and
  can be rebuilt at any time -- it must never be used on the member database.

Nothing is filtered out. `ADR_OFFICIAL = true` looks like the obvious filter
and **loses real addresses**: of 92 sites in one live deployment, 86
validated against the full register and only 83 against the official rows.
`official` and `status` are carried as flags and only ever influence the
*order* suggestions are offered in.
"""

import csv
import io
import json
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import urlsplit

import httpx

from app.paths import ADDRESS_REGISTER_PATH
from app.sort_keys import fold_for_sort

#: STAC collection describing the register. The download URL is read from
#: here rather than hard-coded: swisstopo versions the file name, and a
#: hard-coded one would break silently on the next release.
STAC_ITEMS_URL = (
    "https://data.geo.admin.ch/api/stac/v0.9/collections/"
    "ch.swisstopo.amtliches-gebaeudeadressverzeichnis/items"
)

#: Which asset to take: the Swiss CSV in LV95. The GeoPackage and INTERLIS
#: variants hold the same data in formats this app has no reader for.
_ASSET_SUFFIX = "_ch_2056.csv.zip"

#: Hosts the download may come from. The URL is read out of a remote JSON
#: document, so without this an altered catalogue could point the one
#: network-fetching, disk-writing path in this otherwise offline app at any
#: server. That needs a forged HTTPS response to begin with, so this is a
#: bound on the damage rather than a closed hole -- but it is two lines, and
#: the set of legitimate hosts really is this small.
_ALLOWED_HOSTS = frozenset({"data.geo.admin.ch", "swisstopo.admin.ch", "www.swisstopo.admin.ch"})

#: Rows per batch. The caller yields control between batches, so this is the
#: granularity at which the window stays responsive: about 0.05 s of work.
_BATCH_ROWS = 5_000

#: How long a register counts as current. Quarterly, because building
#: addresses change slowly and it matches the billing rhythm the
#: administrator is in the app for anyway.
STALE_AFTER_DAYS = 92


class AddressRegisterError(RuntimeError):
    """Raised when the register cannot be fetched or built."""


@dataclass(frozen=True)
class RegisterAsset:
    """The downloadable register file, as described by the STAC catalogue.

    Attributes:
        url: Direct download URL of the ZIP.
        data_date: The day swisstopo published this data, which is what the
            UI shows -- the download time is a different statement and says
            nothing about how current the addresses are.
        size_bytes: Compressed size, or `None` if the catalogue omits it.
    """

    url: str
    data_date: Optional[date]
    size_bytes: Optional[int]


@dataclass(frozen=True)
class RegisterInfo:
    """What the local register file currently holds.

    Attributes:
        data_date: swisstopo's publication date of the data in the file.
        downloaded_at: When this machine built it.
        streets: Row count of the `street` table.
        addresses: Row count of the `address` table.
    """

    data_date: Optional[date]
    downloaded_at: Optional[datetime]
    streets: int
    addresses: int

    @property
    def age_days(self) -> Optional[int]:
        """How many days old the *data* is.

        Returns:
            The age in days, or `None` when no publication date is known.
        """
        if self.data_date is None:
            return None
        return (date.today() - self.data_date).days

    @property
    def is_stale(self) -> bool:
        """Whether the register should be refreshed.

        Returns:
            `True` past `STALE_AFTER_DAYS`. An unknown date counts as stale:
            not knowing how old the data is is not a reason to trust it.
        """
        age = self.age_days
        return True if age is None else age > STALE_AFTER_DAYS


def _parse_stac_date(value: Optional[str]) -> Optional[date]:
    """Read a STAC timestamp as a plain date.

    Args:
        value: An ISO-8601 timestamp, or `None`.

    Returns:
        The date, or `None` if absent or unparsable. A missing date is not
        an error -- the register is still usable, it just cannot say how old
        it is.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def select_asset(payload: dict) -> RegisterAsset:
    """Pick the Swiss CSV asset out of a STAC items response.

    Args:
        payload: The parsed JSON of `STAC_ITEMS_URL`.

    Returns:
        The asset to download.

    Raises:
        AddressRegisterError: If the catalogue holds no matching asset, or
            names one on a host this app does not download from.
    """
    for feature in payload.get("features") or []:
        assets = feature.get("assets") or {}
        for name, asset in assets.items():
            if not name.endswith(_ASSET_SUFFIX):
                continue
            href = asset.get("href")
            if not href:
                continue
            parsed = urlsplit(href)
            if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
                raise AddressRegisterError(f"Unerwartete Download-Adresse von swisstopo: {href}")
            properties = feature.get("properties") or {}
            return RegisterAsset(
                url=href,
                data_date=_parse_stac_date(properties.get("datetime") or properties.get("updated")),
                size_bytes=asset.get("file:size"),
            )
    raise AddressRegisterError("Im Verzeichnis von swisstopo wurde keine passende CSV-Datei gefunden.")


async def fetch_asset(client: Optional[httpx.AsyncClient] = None) -> RegisterAsset:
    """Ask swisstopo which file to download and when it was published.

    Args:
        client: An open client, or `None` to use a short-lived one. Passed in
            by tests so no test ever reaches the network.

    Returns:
        The asset to download.

    Raises:
        AddressRegisterError: On any network or protocol failure.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await client.get(STAC_ITEMS_URL)
        response.raise_for_status()
        return select_asset(response.json())
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        raise AddressRegisterError(f"Verzeichnis von swisstopo nicht erreichbar: {exc}") from exc
    finally:
        if own_client:
            await client.aclose()


async def download_asset(
    asset: RegisterAsset,
    target: Path,
    on_progress: Optional[Callable[[float], None]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> None:
    """Stream the register ZIP to disk, reporting progress as it goes.

    Awaits network I/O, so the window stays responsive throughout -- this is
    the half of the update that genuinely does not block.

    Args:
        asset: What to download.
        target: Where to write it. Overwritten if present.
        on_progress: Called with 0.0..1.0 as bytes arrive. Progress is only
            meaningful when the server states a length; without one it is
            never called.
        client: An open client, or `None` to use a short-lived one.

    Raises:
        AddressRegisterError: On any network failure.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=120.0, follow_redirects=True)
    try:
        async with client.stream("GET", asset.url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0) or asset.size_bytes or 0
            written = 0
            with target.open("wb") as handle:
                async for chunk in response.aiter_bytes(1 << 16):
                    handle.write(chunk)
                    written += len(chunk)
                    if on_progress and total:
                        on_progress(min(written / total, 1.0))
    except httpx.HTTPError as exc:
        target.unlink(missing_ok=True)
        raise AddressRegisterError(f"Download fehlgeschlagen: {exc}") from exc
    finally:
        if own_client:
            await client.aclose()


def split_zip_label(label: str) -> tuple[str, str]:
    """Split swisstopo's combined `ZIP_LABEL` into code and locality.

    `ZIP_LABEL` arrives as `"3048 Worblaufen"`: the postal code and the
    **postal locality**, which is the name this app uses for an address --
    never `COM_NAME`, the political municipality. Split on the *first* space
    only, because localities have several words ("3065 Bolligen Dorf").

    Args:
        label: The raw field.

    Returns:
        `(postal_code, locality)`, both empty when the field is unusable.
    """
    code, _, locality = label.strip().partition(" ")
    if not code.isdigit() or not locality.strip():
        return ("", "")
    return (code, locality.strip())


_SCHEMA = """
CREATE TABLE street (
    id           INTEGER PRIMARY KEY,
    street        TEXT NOT NULL,
    street_fold   TEXT NOT NULL,
    postal_code   TEXT NOT NULL,
    locality      TEXT NOT NULL,
    locality_fold TEXT NOT NULL,
    municipality  TEXT NOT NULL,
    bfs_number   INTEGER,
    canton       TEXT NOT NULL
);
CREATE TABLE address (
    street_id   INTEGER NOT NULL REFERENCES street(id),
    number      TEXT NOT NULL,
    number_fold TEXT NOT NULL,
    official    INTEGER NOT NULL,
    status      TEXT NOT NULL
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_INDEXES = """
CREATE INDEX idx_street_fold ON street(street_fold);
CREATE INDEX idx_street_plz ON street(postal_code);
CREATE INDEX idx_street_locality ON street(locality_fold);
CREATE INDEX idx_address_lookup ON address(street_id, number_fold);
"""


def build_register(
    zip_path: Path,
    target: Optional[Path] = None,
    data_date: Optional[date] = None,
) -> Iterator[float]:
    """Build the register database from a downloaded ZIP, yielding progress.

    A generator rather than a plain function so the caller can hand control
    back to the event loop between batches: the parse is 35 seconds of local
    work, and doing it in one call would freeze the window for all of it.
    Everything heavy lives here and nothing here is async, which is also
    what makes it testable without a network or an event loop.

    Args:
        zip_path: The downloaded ZIP.
        target: Where the finished database goes. An existing file is
            replaced only once the new one is complete.
        data_date: swisstopo's publication date, stored for display.

    Leaves the finished rows in a scratch file **without indexes**;
    `finalise_register` adds those and swaps the file into place. The split
    exists because `CREATE INDEX` over 3.3 million rows is one uninterruptible
    1.8-second statement -- long enough to miss the one second NiceGUI allows
    the browser for a state query, which showed up as TimeoutErrors in the
    log while an update ran.

    Yields:
        Progress from 0.0 to 1.0, based on bytes read from the ZIP member.

    Raises:
        AddressRegisterError: If the archive holds no usable CSV.
    """
    target = target if target is not None else ADDRESS_REGISTER_PATH
    scratch = target.with_suffix(".building")
    scratch.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [i for i in archive.infolist() if i.filename.lower().endswith(".csv")]
            if not members:
                raise AddressRegisterError("Das Archiv von swisstopo enthält keine CSV-Datei.")
            member = max(members, key=lambda info: info.file_size)
            total_bytes = member.file_size or 1

            db = sqlite3.connect(scratch)
            try:
                # Safe here and nowhere else: this file is derived from a
                # download and can be rebuilt at any time, so trading crash
                # durability for speed costs nothing that matters.
                db.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;")
                db.executescript(_SCHEMA)

                street_ids: dict[tuple[str, str, str], int] = {}
                pending: list[tuple] = []
                yield 0.0

                with archive.open(member) as raw:
                    stream = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                    for index, row in enumerate(csv.DictReader(stream, delimiter=";"), start=1):
                        postal_code, locality = split_zip_label(row.get("ZIP_LABEL") or "")
                        if not postal_code:
                            continue
                        street = (row.get("STN_LABEL") or "").strip()
                        key = (fold_for_sort(street), postal_code, fold_for_sort(locality))
                        street_id = street_ids.get(key)
                        if street_id is None:
                            # Length-checked before int(): since Python
                            # 3.11 converting more than 4300 digits raises,
                            # and this field comes from a downloaded file.
                            # The same trap `app.sort_keys.numeric_part`
                            # already had to work around.
                            bfs = (row.get("COM_FOSNR") or "").strip()[:9]
                            cursor = db.execute(
                                "INSERT INTO street (street, street_fold, postal_code, locality,"
                                " locality_fold, municipality, bfs_number, canton)"
                                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    street,
                                    key[0],
                                    postal_code,
                                    locality,
                                    key[2],
                                    (row.get("COM_NAME") or "").strip(),
                                    int(bfs) if bfs.isdigit() else None,
                                    (row.get("COM_CANTON") or "").strip(),
                                ),
                            )
                            street_id = int(cursor.lastrowid)
                            street_ids[key] = street_id

                        number = (row.get("ADR_NUMBER") or "").strip()
                        pending.append(
                            (
                                street_id,
                                number,
                                fold_for_sort(number),
                                1 if (row.get("ADR_OFFICIAL") or "").lower() == "true" else 0,
                                (row.get("ADR_STATUS") or "").strip(),
                            )
                        )
                        if index % _BATCH_ROWS == 0:
                            db.executemany("INSERT INTO address VALUES (?, ?, ?, ?, ?)", pending)
                            pending.clear()
                            yield min(raw.tell() / total_bytes, 0.99)

                if pending:
                    db.executemany("INSERT INTO address VALUES (?, ?, ?, ?, ?)", pending)
                db.commit()
            finally:
                db.close()
        yield 1.0
    except zipfile.BadZipFile as exc:
        scratch.unlink(missing_ok=True)
        raise AddressRegisterError(f"Die heruntergeladene Datei ist kein Archiv: {exc}") from exc
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise


def finalise_register(
    target: Optional[Path] = None,
    data_date: Optional[date] = None,
) -> None:
    """Index the freshly built register and swap it into place.

    Kept out of `build_register` so a caller can run it in a thread: the
    address index is one 1.8-second SQLite statement, and SQLite releases
    the GIL while it works, so a thread is all it takes to keep the window
    responsive through it. Everything here opens its own connection, because
    a `sqlite3.Connection` belongs to the thread that created it.

    Args:
        target: Where the finished register goes.
        data_date: swisstopo's publication date, stored for display.

    Raises:
        AddressRegisterError: If no scratch file is waiting.
    """
    target = target if target is not None else ADDRESS_REGISTER_PATH
    scratch = target.with_suffix(".building")
    if not scratch.exists():
        raise AddressRegisterError("Es liegt kein fertig gelesenes Register bereit.")
    try:
        db = sqlite3.connect(scratch)
        try:
            db.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;")
            db.executescript(_INDEXES)
            db.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [
                    ("data_date", data_date.isoformat() if data_date else ""),
                    ("downloaded_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                ],
            )
            db.commit()
        finally:
            db.close()
        # Only now is the old register touched: a failure anywhere above
        # leaves whatever was working in place.
        target.unlink(missing_ok=True)
        scratch.replace(target)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise


def read_info(path: Optional[Path] = None) -> Optional[RegisterInfo]:
    """Describe the local register, or report that there is none.

    Args:
        path: The register file.

    Returns:
        Its `RegisterInfo`, or `None` when no usable register exists. A
        missing or unreadable file is a normal state, not an error: every
        feature that uses the register simply goes quiet without it.
    """
    path = path if path is not None else ADDRESS_REGISTER_PATH
    if not path.exists():
        return None
    try:
        # Read-only: nothing in the app writes this file except a rebuild,
        # and a reader must never hold a lock that could break one.
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
        streets = connection.execute("SELECT COUNT(*) FROM street").fetchone()[0]
        addresses = connection.execute("SELECT COUNT(*) FROM address").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        connection.close()

    downloaded = meta.get("downloaded_at") or ""
    try:
        downloaded_at = datetime.fromisoformat(downloaded) if downloaded else None
    except ValueError:
        downloaded_at = None
    return RegisterInfo(
        data_date=_parse_stac_date(meta.get("data_date")),
        downloaded_at=downloaded_at,
        streets=streets,
        addresses=addresses,
    )
