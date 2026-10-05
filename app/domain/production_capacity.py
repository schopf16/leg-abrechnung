"""How much room a LEG still has before it breaks the 5% production rule."""

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
    """What a LEG's recorded production percentage means right now."""

    status: str
    percent: Optional[float]
    growth_factor: Optional[float]
    label: str


def format_percent(value: float) -> str:
    """Format a percentage the way a German reader writes it."""
    return f"{value:.1f} %".replace(".", ",")


def format_factor(value: float) -> str:
    """Format a growth factor the way a German reader writes it."""
    return f"{value:.1f}".replace(".", ",")


def status_classes(status: str) -> str:
    """Pick the colour for a production-capacity line."""
    if status == STATUS_BELOW:
        return "text-negative font-bold"
    if status == STATUS_TIGHT:
        return "text-warning"
    if status == STATUS_UNKNOWN:
        return "text-grey-6 italic"
    return ""


def compute_headroom(percent: Optional[float], *, warn_percent: float) -> CapacityHeadroom:
    """Judge a LEG's recorded production percentage."""
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
