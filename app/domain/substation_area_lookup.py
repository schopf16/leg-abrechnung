"""Which Trafokreis a Person sits in, for the `{trafokreis}` placeholder."""

import sqlite3
from datetime import date

from app.models import substation_area as substation_area_repo
from app.models.substation_area import SubstationArea
from app.sort_keys import text_key


def areas_for_person(connection: sqlite3.Connection, person_id: int) -> list[SubstationArea]:
    """The Trafokreise behind a Person's current or upcoming assignments."""
    areas = substation_area_repo.list_for_person(connection, person_id, date.today())
    return sorted(areas, key=lambda area: text_key(area.bkw_designation, area.name))


def bkw_designations_for_person(connection: sqlite3.Connection, person_id: int) -> str:
    """BKW's own designation(s) of those Trafokreise, as one line of text.

    `bkw_designation` is what BKW writes ("TRA9365"); `name` is only the
    administrator's label for the same thing, so it stands in where BKW's is
    missing. Nothing is invented: a Person without a running assignment gets
    an empty value, like any other unfillable placeholder.
    """
    designations = [
        (area.bkw_designation or area.name).strip() for area in areas_for_person(connection, person_id)
    ]
    return ", ".join(designation for designation in designations if designation)
