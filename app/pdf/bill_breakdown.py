"""Groups a quarter's shared energy by site and metering point for the bill."""

from dataclasses import dataclass, field

from app.domain.distribution import PersonQuarterResult
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN
from app.sort_keys import address_key


@dataclass(frozen=True)
class MeteringPointInfo:
    """What the bill needs to know about one metering point."""

    designation: str
    label: str
    site_id: int
    site_address: str
    direction: str = DIRECTION_CONSUMPTION

    @property
    def is_feed_in(self) -> bool:
        """Whether this metering point measures feed-in."""
        return self.direction == DIRECTION_FEED_IN

    @property
    def display_name(self) -> str:
        """The metering point as it is printed on the bill."""
        return f"{self.designation} {self.label}".strip() if self.label else self.designation


@dataclass(frozen=True)
class BreakdownRow:
    """One metering point's contribution, on one side of the ledger."""

    name: str
    kwh: float
    amount_chf: float


@dataclass
class SiteBlock:
    """One site's section of the bill."""

    site_id: int
    address: str
    consumption: list[BreakdownRow] = field(default_factory=list)
    feed_in: list[BreakdownRow] = field(default_factory=list)

    @property
    def consumed_kwh(self) -> float:
        """Total locally-sourced consumption at this site."""
        return sum(row.kwh for row in self.consumption)

    @property
    def produced_kwh(self) -> float:
        """Total locally-delivered production at this site."""
        return sum(row.kwh for row in self.feed_in)

    @property
    def balance_chf(self) -> float:
        """What this site costs, in francs: consumption minus production."""
        return sum(row.amount_chf for row in self.consumption) - sum(row.amount_chf for row in self.feed_in)


@dataclass
class BillBreakdown:
    """A person's quarter, grouped by site."""

    sites: list[SiteBlock] = field(default_factory=list)

    @property
    def has_multiple_sites(self) -> bool:
        """Whether this bill covers more than one site."""
        return len(self.sites) > 1

    @property
    def consumption_rows(self) -> list[BreakdownRow]:
        """Every consumption row, across all sites."""
        return [row for site in self.sites for row in site.consumption]

    @property
    def feed_in_rows(self) -> list[BreakdownRow]:
        """Every feed-in row, across all sites."""
        return [row for site in self.sites for row in site.feed_in]

    @property
    def total_consumed_kwh(self) -> float:
        """Total locally-sourced consumption across all sites."""
        return sum(row.kwh for row in self.consumption_rows)

    @property
    def total_produced_kwh(self) -> float:
        """Total locally-delivered production across all sites."""
        return sum(row.kwh for row in self.feed_in_rows)

    @property
    def total_consumption_chf(self) -> float:
        """Value of all locally-sourced consumption, unrounded francs."""
        return sum(row.amount_chf for row in self.consumption_rows)

    @property
    def total_feed_in_chf(self) -> float:
        """Value of all locally-delivered production, unrounded francs."""
        return sum(row.amount_chf for row in self.feed_in_rows)


def build_bill_breakdown(
    person_result: PersonQuarterResult,
    metering_point_info: dict[int, MeteringPointInfo],
    price_rp_per_kwh: float,
) -> BillBreakdown:
    """Group one person's quarter by site and metering point."""
    blocks: dict[int, SiteBlock] = {}

    for metering_point_id, totals in person_result.by_metering_point.items():
        info = metering_point_info.get(metering_point_id)
        if info is None:
            continue

        block = blocks.setdefault(info.site_id, SiteBlock(site_id=info.site_id, address=info.site_address))
        # A metering point goes on the side its direction puts it on, even
        # at zero. Which side that is comes from `direction`, not from
        # which total happens to be non-zero -- otherwise a PV meter that
        # delivered nothing this quarter would silently vanish from the
        # Einspeisung section, and the recipient could not tell whether it
        # was considered at all.
        rows = block.feed_in if info.is_feed_in else block.consumption
        kwh = totals.produced_local_kwh if info.is_feed_in else totals.consumed_local_kwh
        rows.append(
            BreakdownRow(
                name=info.display_name,
                kwh=kwh,
                amount_chf=kwh * price_rp_per_kwh / 100,
            )
        )

    for block in blocks.values():
        block.consumption.sort(key=lambda row: row.name)
        block.feed_in.sort(key=lambda row: row.name)

    return BillBreakdown(sites=sorted(blocks.values(), key=lambda block: address_key(block.address)))
