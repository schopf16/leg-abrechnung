"""Generates the QRR payment reference number printed on QR-invoices.

A QRR reference is up to 26 digits plus one Modulo-10-recursive check digit
(computed here via `stdnum.ch.esr`, the same algorithm library `qrbill`
itself uses to validate references; both left-pad the payload with zeros
to the full 26 digits when formatting, so a shorter payload is not a
problem). It encodes the person's Kundennummer (rather than the internal
database person id) as its first digits -- deliberately, so that reading
the reference straight off a bank statement already tells you which
customer it belongs to, without a lookup -- plus the billing run and line
item ids, so a bank statement can be matched back to the exact invoice
without manual lookup.
"""

import re
from dataclasses import dataclass
from typing import Optional

from stdnum.ch import esr
from stdnum.exceptions import ValidationError

#: Width, in digits, of each encoded component before the check digit.
#: Kundennummer is itself always a 6-digit number (see
#: `app.models.person.generate_kundennummer`), so it fits this width
#: exactly; the three widths need not sum to any particular total since
#: `esr`/`qrbill` zero-pad the payload up to 26 digits automatically.
_KUNDENNUMMER_DIGITS = 6
_BILLING_RUN_DIGITS = 6
_ITEM_DIGITS = 12
#: Length of `esr.validate()`'s return value for a reference that was
#: actually built by `generate_qrr_reference` -- the 24-digit payload plus
#: its 1-digit check digit. `esr.validate()` always strips any leading
#: zero-padding (confirmed empirically: validating either the raw
#: 25-character output of `generate_qrr_reference` or the full 27-digit
#: QRR form printed on the payment slip, with its 2 leading zero-padding
#: digits, returns this same 25-character string) -- since the payload's
#: own leading digit is always non-zero (`kundennummer` starts at 100000),
#: this length check reliably distinguishes "one of ours" from some other,
#: differently-shaped but still checksum-valid ESR/QRR reference.
_VALIDATED_LENGTH = _KUNDENNUMMER_DIGITS + _BILLING_RUN_DIGITS + _ITEM_DIGITS + 1


def generate_qrr_reference(kundennummer: int, billing_run_id: int, item_id: int) -> str:
    """Build a unique, valid QRR reference for one invoice.

    Args:
        kundennummer: The billed person's 6-digit Kundennummer (see
            `app.models.person.Person.kundennummer`) -- embedded first so
            the customer is identifiable directly from the reference.
        billing_run_id: Database id of the billing run.
        item_id: Database id of the billing run line item (the invoice).

    Returns:
        A numeric string (payload digits + 1 check digit, zero-padded to
        27 digits total once passed through `qrbill`) suitable for
        `qrbill.QRBill(reference_number=...)`.

    Raises:
        ValueError: If any id is too large to fit its allotted digit width.
    """
    payload = (
        f"{kundennummer:0{_KUNDENNUMMER_DIGITS}d}"
        f"{billing_run_id:0{_BILLING_RUN_DIGITS}d}"
        f"{item_id:0{_ITEM_DIGITS}d}"
    )
    if len(payload) != _KUNDENNUMMER_DIGITS + _BILLING_RUN_DIGITS + _ITEM_DIGITS:
        raise ValueError(
            "One of kundennummer, billing_run_id or item_id is too large "
            "to encode in a QRR reference."
        )
    check_digit = esr.calc_check_digit(payload)
    return payload + check_digit


@dataclass
class DecodedQrrReference:
    """The three ids embedded in a QRR reference by `generate_qrr_reference`.

    Attributes:
        kundennummer: The billed person's Kundennummer, as embedded.
        billing_run_id: Database id of the billing run.
        item_id: Database id of the billing run line item (the invoice).
    """

    kundennummer: int
    billing_run_id: int
    item_id: int


def parse_qrr_reference(raw_reference: str) -> Optional[DecodedQrrReference]:
    """Decode a QRR reference (e.g. read off a bank statement) back into
    the ids `generate_qrr_reference` originally encoded into it.

    This is the reconciliation counterpart to `generate_qrr_reference`: a
    bank statement's structured reference field can be decoded directly,
    without any lookup table, because the reference already contains
    everything needed to find the exact invoice it belongs to.

    Args:
        raw_reference: The reference as it appears on a bank statement --
            any spacing/grouping is tolerated, and it does not matter
            whether it is zero-padded to the full 27-digit QRR form or
            given as the shorter 25-character value `generate_qrr_reference`
            itself returns; both decode identically.

    Returns:
        The decoded `DecodedQrrReference`, or `None` if `raw_reference` is
        not a checksum-valid ESR/QRR reference, or is checksum-valid but
        not shaped like one this function generated (e.g. a genuine but
        differently-structured reference) -- both are normal, expected
        outcomes for a payment that was not made by scanning one of this
        app's QR-bills, not exceptional errors.
    """
    digits = re.sub(r"\D", "", raw_reference or "")
    if not digits:
        return None
    try:
        validated = esr.validate(digits)
    except ValidationError:
        return None
    if len(validated) != _VALIDATED_LENGTH:
        return None

    payload = validated[:-1]
    kundennummer_digits = payload[:_KUNDENNUMMER_DIGITS]
    billing_run_digits = payload[_KUNDENNUMMER_DIGITS : _KUNDENNUMMER_DIGITS + _BILLING_RUN_DIGITS]
    item_digits = payload[_KUNDENNUMMER_DIGITS + _BILLING_RUN_DIGITS :]
    return DecodedQrrReference(
        kundennummer=int(kundennummer_digits),
        billing_run_id=int(billing_run_digits),
        item_id=int(item_digits),
    )
