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
participants' total Anschlussleistung may reach `percent / 5` times its
current value before the LEG drops below the legal floor -- a multiple to
grow *to*, not *by*, which is why every label says "auf das X-Fache
steigen" rather than "um X wachsen". That single
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
        growth_factor: The multiple the consumers' total Anschlussleistung
            may reach before hitting the 5% floor -- a value *to* grow to,
            not *by*: `1.2` means it may become 1.2 times its current
            total, i.e. 20% more, not 120% more. `None` when nothing was
            recorded, `1.0` when there is no room left at all, below `1.0`
            when the LEG is already under the floor.
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


def status_classes(status: str) -> str:
    """Pick the colour for a production-capacity line.

    Args:
        status: One of the status constants above.

    Returns:
        Quasar text classes. Below the legal floor is the only red:
        "tight" is a heads-up, and an unrecorded figure is greyed, since
        nobody having looked yet is not the same as being in trouble.
    """
    if status == STATUS_BELOW:
        return "text-negative font-bold"
    if status == STATUS_TIGHT:
        return "text-warning"
    if status == STATUS_UNKNOWN:
        return "text-grey-6 italic"
    return ""


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

    growth_factor = percent / REQUIRED_PERCENT
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
            f"⚠ Produktionsleistung {shown} -- wird eng: die gesamte Anschlussleistung "
            f"der Bezüger darf noch auf das ~{format_factor(growth_factor)}-Fache steigen "
            "(bei unveränderter Produktion)",
        )
    return CapacityHeadroom(
        STATUS_COMFORTABLE,
        percent,
        growth_factor,
        f"✓ Produktionsleistung {shown} -- die gesamte Anschlussleistung der Bezüger "
        f"darf noch auf das ~{format_factor(growth_factor)}-Fache steigen "
        "(bei unveränderter Produktion)",
    )
