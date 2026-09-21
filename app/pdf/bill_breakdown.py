"""Groups a quarter's shared energy by site and metering point for the bill.

A participant appears to the LEG as exactly one customer with exactly one
netted amount -- that is the vZEV model the community is billed under,
and it does not change here: no second document, no second reference
number, no second payment. What changes is that the document says where
the energy came from, so a participant holding several metering points --
a property management with more than one building -- can allocate that
one amount internally. Working out who owes what inside the building is
the management's own job; this module's output is the raw material for it.

Deliberately pure and free of both SQL and reportlab: the caller (see
`app.pdf.export_service`) resolves metering points and sites into
`MeteringPointInfo`, so this grouping can be tested without a database
and without generating a PDF.

Every franc figure here is a *display* value, computed at full precision
and only formatted. Rounding still happens exactly once, on the net
amount in `app.domain.billing` -- so the displayed lines can add up to
a few tenths of a Rappen beside the net total, which is precisely the
compounding the "round only once" rule exists to prevent.
"""

from dataclasses import dataclass, field

from app.domain.distribution import PersonQuarterResult
from app.models.metering_point import DIRECTION_CONSUMPTION, DIRECTION_FEED_IN
from app.sort_keys import address_key


@dataclass(frozen=True)
class MeteringPointInfo:
    """What the bill needs to know about one metering point.

    Attributes:
        designation: The grid operator's metering point id, the business
            key (see `app.models.metering_point`).
        label: The administrator's own name for it ("Allgemeinstrom",
            "Whg. 3. OG"), or `""`.
        site_id: The site it is installed at.
        site_address: That site's full postal address, for the heading.
        direction: `DIRECTION_CONSUMPTION` or `DIRECTION_FEED_IN` -- which
            section of its site's block the metering point is listed
            under. Taken from the metering point itself rather than
            inferred from its figures, so a meter that moved no energy
            this quarter still appears on the right side, at zero.
    """

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
        """The metering point as it is printed on the bill.

        Returns:
            `"CH1018… Allgemeinstrom"`, or just the designation when no
            label has been entered.
        """
        return f"{self.designation} {self.label}".strip() if self.label else self.designation


@dataclass(frozen=True)
class BreakdownRow:
    """One metering point's contribution, on one side of the ledger.

    Attributes:
        name: The metering point as printed (`MeteringPointInfo.display_name`).
        kwh: Locally shared energy through it this quarter.
        amount_chf: `kwh` at the run's frozen price, in francs, unrounded.
    """

    name: str
    kwh: float
    amount_chf: float


@dataclass
class SiteBlock:
    """One site's section of the bill.

    Attributes:
        site_id: The site this block covers.
        address: Its full postal address, used as the heading.
        consumption: One row per consumption metering point, in printing order.
        feed_in: One row per feed-in metering point, in printing order.
    """

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
        """What this site costs, in francs: consumption minus production.

        Positive means this site owes the LEG, negative means the LEG
        owes for it. This is the figure a property management carries
        into its own Nebenkostenabrechnung -- unrounded, for display.
        """
        return sum(row.amount_chf for row in self.consumption) - sum(row.amount_chf for row in self.feed_in)


@dataclass
class BillBreakdown:
    """A person's quarter, grouped by site.

    Attributes:
        sites: One block per site the person had shared energy at, ordered
            by address exactly as the Standorte page orders it.
    """

    sites: list[SiteBlock] = field(default_factory=list)

    @property
    def has_multiple_sites(self) -> bool:
        """Whether this bill covers more than one site.

        The document's grand totals per direction are only printed when
        it does -- with a single site they would merely repeat the site's
        own subtotal one line further down.
        """
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
    """Group one person's quarter by site and metering point.

    **Every** metering point the person held appears, including one that
    shared nothing -- then at 0.000 kWh. The line-up of a bill must not
    change from one quarter to the next: a recipient who finds a meter
    missing cannot tell whether it was deliberately excluded or simply
    forgotten, and showing it at zero says plainly "we looked, there was
    nothing to share here". Which metering points those are comes from
    `person_result.by_metering_point`, which the distribution seeds from
    the assignments (see `app.domain.distribution._seed_participants`).

    Args:
        person_result: The person's distribution result for the quarter
            (`app.domain.distribution`), whose `by_metering_point` this
            reads.
        metering_point_info: Resolved metering point and site data, keyed
            by metering point id. A metering point missing from here is
            skipped rather than raising -- a bill must still render if
            master data was deleted underneath it.
        price_rp_per_kwh: The run's frozen price, in Rappen per kWh.

    Returns:
        The grouped `BillBreakdown`, sites ordered by address.
    """
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
