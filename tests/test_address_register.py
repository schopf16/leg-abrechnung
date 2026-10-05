"""Tests for building the local copy of swisstopo's address register."""

import sqlite3
import zipfile
from datetime import date, timedelta

import pytest

from app.importers.address_register import (
    STALE_AFTER_DAYS,
    finalise_register,
    AddressRegisterError,
    RegisterInfo,
    build_register,
    read_info,
    select_asset,
    split_zip_label,
)
from tests.conftest import build_test_register, write_register_zip


def _rows(register) -> list[tuple]:
    """Read every address back out, joined to its street."""
    connection = sqlite3.connect(register)
    try:
        return connection.execute(
            """
            SELECT s.street, s.postal_code, s.locality, s.municipality,
                   a.number, a.official, a.status
            FROM address a JOIN street s ON s.id = a.street_id
            ORDER BY s.street, a.number
            """
        ).fetchall()
    finally:
        connection.close()


# --- The format ------------------------------------------------------------


@pytest.mark.parametrize(
    "label, expected",
    [
        ("3048 Worblaufen", ("3048", "Worblaufen")),
        ("3065 Bolligen Dorf", ("3065", "Bolligen Dorf")),
        ("3072 Ostermundigen 1", ("3072", "Ostermundigen 1")),
        ("", ("", "")),
        ("kaputt", ("", "")),
        ("3048", ("", "")),
    ],
)
def test_zip_label_splits_on_the_first_space_only(label, expected):
    """A locality with several words must survive intact."""
    assert split_zip_label(label) == expected


def test_a_dotted_house_number_is_kept_as_written(address_register):
    """ "31.1" is a house number, not a decimal, and 331'401 of them exist."""
    numbers = {row[4] for row in _rows(address_register)}

    assert "31.1" in numbers


def test_an_empty_house_number_is_kept(address_register):
    """The register holds 7'078 of them; dropping them loses the street."""
    numbers = [row[4] for row in _rows(address_register)]

    assert "" in numbers


def test_non_official_and_planned_rows_are_kept(address_register):
    """The filter that looks right and is not."""
    rows = _rows(address_register)

    assert 0 in {row[5] for row in rows}, "official=false muss erhalten bleiben"
    assert "planned" in {row[6] for row in rows}, "status=planned muss erhalten bleiben"


def test_the_postal_locality_and_the_municipality_are_both_stored(address_register):
    """They differ, and only one of them belongs on an invoice."""
    row = next(r for r in _rows(address_register) if r[0] == "Erstweg")

    assert row[2] == "Musterdorf"
    assert row[3] == "Grossgemeinde"


def test_a_row_without_a_usable_postal_code_is_skipped(tmp_path):
    """No postal code means the address cannot be matched to anything."""
    zip_path = write_register_zip(
        tmp_path / "r.zip",
        rows=[
            ("Erstweg", "4", "3048 Musterdorf", "Gross", "real", "true"),
            ("Viertweg", "1", "kaputt", "Gross", "real", "true"),
        ],
    )
    target = tmp_path / "reg.sqlite3"

    build_test_register(zip_path, target)

    assert [row[0] for row in _rows(target)] == ["Erstweg"]


def test_one_street_row_per_street_and_locality(address_register):
    """The whole reason for two tables: 358 MB flat against 127 MB here."""
    connection = sqlite3.connect(address_register)
    try:
        streets = connection.execute("SELECT COUNT(*) FROM street").fetchone()[0]
        addresses = connection.execute("SELECT COUNT(*) FROM address").fetchone()[0]
    finally:
        connection.close()

    assert streets == 4, "Erstweg, Zweitweg, Ärniweg, Drittweg"
    assert addresses == 7


# --- Progress and atomicity ------------------------------------------------


def test_progress_rises_and_reaches_one(tmp_path):
    """The generator is what keeps the window alive, so it has to report."""
    zip_path = write_register_zip(tmp_path / "r.zip")

    steps = list(build_register(zip_path, tmp_path / "reg.sqlite3"))

    assert steps == sorted(steps), f"nicht monoton: {steps}"
    assert steps[0] == 0.0
    assert steps[-1] == 1.0


def test_a_broken_archive_leaves_the_previous_register_untouched(tmp_path):
    """An update must not be able to destroy a working register."""
    target = tmp_path / "reg.sqlite3"
    build_test_register(write_register_zip(tmp_path / "good.zip"), target)
    before = target.read_bytes()

    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"das ist kein Archiv")
    with pytest.raises(AddressRegisterError):
        list(build_register(broken, target))

    assert target.read_bytes() == before


def test_an_archive_without_a_csv_is_refused(tmp_path):
    """Says so rather than building an empty register that looks fine."""
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("liesmich.txt", "nichts")

    with pytest.raises(AddressRegisterError):
        list(build_register(archive, tmp_path / "reg.sqlite3"))


def test_no_half_built_file_is_left_behind(tmp_path):
    """The scratch file must not survive a failure and be mistaken for one."""
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"kein Archiv")
    target = tmp_path / "reg.sqlite3"

    with pytest.raises(AddressRegisterError):
        list(build_register(broken, target))

    assert not target.exists()
    assert not target.with_suffix(".building").exists()


# --- What the local copy says about itself ---------------------------------


def test_read_info_reports_counts_and_the_data_date(address_register):
    """The *data* date, not the download time -- they say different things."""
    info = read_info(address_register)

    assert info is not None
    assert (info.streets, info.addresses) == (4, 7)
    assert info.data_date == date.today()
    assert info.downloaded_at is not None


def test_a_missing_register_is_not_an_error(tmp_path):
    """Everything about this feature stays quiet without a register."""
    assert read_info(tmp_path / "gibt-es-nicht.sqlite3") is None


@pytest.mark.parametrize(
    "age_days, stale",
    [(0, False), (STALE_AFTER_DAYS - 1, False), (STALE_AFTER_DAYS, False), (STALE_AFTER_DAYS + 1, True)],
)
def test_staleness_turns_over_at_the_configured_age(age_days, stale):
    """Quarterly: addresses change slowly and the update is one click."""
    info = RegisterInfo(
        data_date=date.today() - timedelta(days=age_days),
        downloaded_at=None,
        streets=1,
        addresses=1,
    )

    assert info.is_stale is stale


def test_an_unknown_data_date_counts_as_stale():
    """Not knowing how old data is, is not a reason to trust it."""
    info = RegisterInfo(data_date=None, downloaded_at=None, streets=1, addresses=1)

    assert info.is_stale is True
    assert info.age_days is None


# --- Choosing what to download --------------------------------------------


def test_the_swiss_csv_asset_is_picked_out_of_the_catalogue():
    """The URL is read from STAC rather than hard-coded."""
    payload = {
        "features": [
            {
                "properties": {"datetime": "2026-10-01T04:43:15Z"},
                "assets": {
                    "amtliches-gebaeudeadressverzeichnis_ch_2056.gdb.zip": {"href": "falsch"},
                    "amtliches-gebaeudeadressverzeichnis_ch_2056.csv.zip": {
                        "href": "https://data.geo.admin.ch/ch.csv.zip",
                        "file:size": 143241571,
                    },
                },
            }
        ]
    }

    asset = select_asset(payload)

    assert asset.url == "https://data.geo.admin.ch/ch.csv.zip"
    assert asset.data_date == date(2026, 10, 1)
    assert asset.size_bytes == 143241571


def test_a_catalogue_without_the_csv_is_an_error():
    """Better than downloading a format this app cannot read."""
    with pytest.raises(AddressRegisterError):
        select_asset({"features": [{"assets": {"x_li_2056.csv.zip": {"href": "u"}}}]})


@pytest.mark.parametrize(
    "href",
    [
        "https://boeser-host.invalid/x_ch_2056.csv.zip",
        "http://data.geo.admin.ch/x_ch_2056.csv.zip",
        "file:///C:/Windows/x_ch_2056.csv.zip",
    ],
)
def test_a_download_address_outside_swisstopo_is_refused(href):
    """The URL comes out of a remote JSON document."""
    payload = {"features": [{"properties": {}, "assets": {"x_ch_2056.csv.zip": {"href": href}}}]}

    with pytest.raises(AddressRegisterError):
        select_asset(payload)


# --- Reading and finishing are two steps ----------------------------------


def test_reading_alone_leaves_the_old_register_in_place(tmp_path):
    """The swap happens in `finalise_register`, not when the rows are read."""
    target = tmp_path / "reg.sqlite3"
    build_test_register(write_register_zip(tmp_path / "first.zip"), target)
    before = target.read_bytes()

    list(build_register(write_register_zip(tmp_path / "second.zip"), target))

    assert target.read_bytes() == before, "das alte Register muss bis zum Tausch gelten"
    assert target.with_suffix(".building").exists()


def test_finishing_without_a_read_file_is_refused(tmp_path):
    """Says so rather than leaving the administrator with no register."""
    with pytest.raises(AddressRegisterError):
        finalise_register(tmp_path / "reg.sqlite3")


def test_the_lookup_index_exists_after_finishing(address_register):
    """The one that costs the 1.8 seconds, and the one every lookup uses."""
    connection = sqlite3.connect(address_register)
    try:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        connection.close()

    assert "idx_address_lookup" in names
