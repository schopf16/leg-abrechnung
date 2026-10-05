"""A linked year/quarter dropdown pair that only ever offers periods for which readings actually exist."""

from typing import NamedTuple, Optional

from nicegui import ui

from app.domain.period import latest_available_period

QUARTER_LABELS = {1: "Q1 (Jan-Mär)", 2: "Q2 (Apr-Jun)", 3: "Q3 (Jul-Sep)", 4: "Q4 (Okt-Dez)"}


class PeriodSelector(NamedTuple):
    """The two linked dropdown elements making up a period selector."""

    year_select: ui.select
    quarter_select: ui.select
    has_data: bool

    @property
    def selected_period(self) -> Optional[tuple[int, int]]:
        """The currently selected `(year, quarter)`, if any."""
        if self.year_select.value is None or self.quarter_select.value is None:
            return None
        return int(self.year_select.value), int(self.quarter_select.value)


def build_period_selector(available: dict[int, set[int]]) -> PeriodSelector:
    """Create a year dropdown and a dependent quarter dropdown."""
    if not available:
        year_select = ui.select({}, label="Jahr").classes("w-28")
        quarter_select = ui.select({}, label="Quartal").classes("w-48")
        year_select.disable()
        quarter_select.disable()
        return PeriodSelector(year_select, quarter_select, has_data=False)

    default_year, default_quarter = latest_available_period(available)
    year_options = {year: str(year) for year in sorted(available)}

    def quarter_options_for_year(year: int) -> dict[int, str]:
        return {q: QUARTER_LABELS[q] for q in sorted(available.get(year, ()))}

    year_select = ui.select(year_options, label="Jahr", value=default_year).classes("w-28")
    quarter_select = ui.select(
        quarter_options_for_year(default_year), label="Quartal", value=default_quarter
    ).classes("w-48")

    def on_year_change() -> None:
        """Refresh the quarter dropdown to match the newly selected year."""
        options = quarter_options_for_year(int(year_select.value))
        new_value = max(options) if options else None
        quarter_select.set_options(options, value=new_value)

    year_select.on_value_change(lambda _: on_year_change())

    return PeriodSelector(year_select, quarter_select, has_data=True)
