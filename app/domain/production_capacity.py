"""How much room a LEG still has before it breaks the 5% production rule.

Art. 19e Abs. 1 StromVV requires a LEG's installed production capacity to
be at least 5% of the participating end consumers' total
Anschlussleistung. BKW's LEG portal shows the current figure on every
metering point registration ("37.6 % tatsächlich / 5 % erforderlich"),
and that is the number recorded on the LEG
(`Leg.production_capacity_percent`).

The app cannot compute it. A site's Anschlussleistung is not in this
database and cannot be obtained from anywhere the app can reach, so the
percentage is copied from the portal and nothing here second-guesses it.

What the app *can* do with it is the arithmetic the administrator
actually needs. Adding a consumer raises the denominator, so the
percentage falls: with the production capacity unchanged, the
participants' total Anschlussleistung may grow by a factor of
`percent / 5` before the LEG drops below the legal floor. That single
number answers the question this feature exists for -- does another
consumer still fit here, or does the next one have to wait in the pooled
LEG until a producer signs up.

The 5% floor is law and fixed. The point at which it "gets tight" is
judgement, so it lives in `LegSettings.production_capacity_warn_percent`
rather than here -- same reasoning as `leg_founding_min_persons`.
"""

from dataclasses import dataclass
from typing import Optional

#: The legal minimum, Art. 19e Abs. 1 StromVV. Not configurable: it is
#: not this app's number to choose.
REQUIRED_PERCENT = 5.0

STATUS_UNKNOWN = "unknown"
STATUS_BELOW = "below"
STATUS_TIGHT = "tight"
STATUS_COMFORTABLE = "comfortable"


@dataclass(frozen=True)
class CapacityHeadroom:
    """What a LEG's recorded production percentage means right now.

    Attributes:
        status: One of `STATUS_UNKNOWN`/`_BELOW`/`_TIGHT`/`_COMFORTABLE`.
        percent: The recorded percentage, or `None` if never recorded.
        growth_factor: How many times the participants' total
            Anschlussleistung could grow before hitting the 5% floor, or
            `None` when nothing was recorded. `1.0` means no room at all;
            below `1.0` the LEG is already under the floor.
        label: German one-liner for the overview and the printout.
    """

    status: str
    percent: Optional[float]
    growth_factor: Optional[float]
    label: str


def format_percent(value: float) -> str:
    """Format a percentage the way a German reader writes it.

    Args:
        value: The percentage.

    Returns:
        One decimal place with a comma, e.g. `"37,6 %"`. Used by every
        caller so the same figure never appears as "37,6 %" in one place
        and "37.6 %" in the next.
    """
    return f"{value:.1f} %".replace(".", ",")


def format_factor(value: float) -> str:
    """Format a growth factor the way a German reader writes it.

    Args:
        value: The factor.

    Returns:
        One decimal place with a comma, e.g. `"7,5"`.
    """
    return f"{value:.1f}".replace(".", ",")


def compute_headroom(percent: Optional[float], *, warn_percent: float) -> CapacityHeadroom:
    """Judge a LEG's recorded production percentage.

    Args:
        percent: `Leg.production_capacity_percent`, or `None` if the
            figure has never been read off the BKW portal.
        warn_percent: `LegSettings.production_capacity_warn_percent` --
            the point below which the administrator wants a heads-up,
            above the legal 5% floor.

    Returns:
        The `CapacityHeadroom`. An unrecorded percentage is reported as
        unknown, never as a problem: nobody has looked yet, which is not
        the same as being in trouble.
    """
    if percent is None:
        return CapacityHeadroom(STATUS_UNKNOWN, None, None, "Produktionsleistung nicht erfasst")

    growth_factor = percent / REQUIRED_PERCENT if REQUIRED_PERCENT else None
    shown = format_percent(percent)

    if percent < REQUIRED_PERCENT:
        return CapacityHeadroom(
            STATUS_BELOW,
            percent,
            growth_factor,
            f"⛔ Produktionsleistung {shown} -- unter den gesetzlich nötigen 5 %",
        )
    if percent < warn_percent:
        return CapacityHeadroom(
            STATUS_TIGHT,
            percent,
            growth_factor,
            f"⚠ Produktionsleistung {shown} -- wird eng, "
            f"Anschlussleistung darf noch ~{format_factor(growth_factor)}× wachsen",
        )
    return CapacityHeadroom(
        STATUS_COMFORTABLE,
        percent,
        growth_factor,
        f"✓ Produktionsleistung {shown} -- Anschlussleistung darf noch "
        f"~{format_factor(growth_factor)}× wachsen",
    )
