"""Generates the QRR payment reference number printed on QR-invoices."""

import re
from dataclasses import dataclass
from typing import Optional

from stdnum.ch import esr
from stdnum.exceptions import ValidationError

#: Width, in digits, of each encoded component before the check digit.
#: customer number is itself always a 6-digit number (see
#: `app.models.person.generate_customer_number`), so it fits this width
#: exactly; the three widths need not sum to any particular total since
#: `esr`/`qrbill` zero-pad the payload up to 26 digits automatically.
_CUSTOMER_NUMBER_DIGITS = 6
_BILLING_RUN_DIGITS = 6
_ITEM_DIGITS = 12
#: Length of `esr.validate()`'s return value for a reference that was
#: actually built by `generate_qrr_reference` -- the 24-digit payload plus
#: its 1-digit check digit. `esr.validate()` always strips any leading
#: zero-padding (confirmed empirically: validating either the raw
#: 25-character output of `generate_qrr_reference` or the full 27-digit
#: QRR form printed on the payment slip, with its 2 leading zero-padding
#: digits, returns this same 25-character string) -- since the payload's
#: own leading digit is always non-zero (`customer_number` starts at 100000),
#: this length check reliably distinguishes "one of ours" from some other,
#: differently-shaped but still checksum-valid ESR/QRR reference.
_VALIDATED_LENGTH = _CUSTOMER_NUMBER_DIGITS + _BILLING_RUN_DIGITS + _ITEM_DIGITS + 1


def generate_qrr_reference(customer_number: int, billing_run_id: int, item_id: int) -> str:
    """Build a unique, valid QRR reference for one invoice."""
    payload = (
        f"{customer_number:0{_CUSTOMER_NUMBER_DIGITS}d}"
        f"{billing_run_id:0{_BILLING_RUN_DIGITS}d}"
        f"{item_id:0{_ITEM_DIGITS}d}"
    )
    if len(payload) != _CUSTOMER_NUMBER_DIGITS + _BILLING_RUN_DIGITS + _ITEM_DIGITS:
        raise ValueError(
            "One of customer_number, billing_run_id or item_id is too large to encode in a QRR reference."
        )
    check_digit = esr.calc_check_digit(payload)
    return payload + check_digit


@dataclass
class DecodedQrrReference:
    """The three ids embedded in a QRR reference by `generate_qrr_reference`."""

    customer_number: int
    billing_run_id: int
    item_id: int


def parse_qrr_reference(raw_reference: str) -> Optional[DecodedQrrReference]:
    """Decode a QRR reference (e.g."""
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
    customer_number_digits = payload[:_CUSTOMER_NUMBER_DIGITS]
    billing_run_digits = payload[_CUSTOMER_NUMBER_DIGITS : _CUSTOMER_NUMBER_DIGITS + _BILLING_RUN_DIGITS]
    item_digits = payload[_CUSTOMER_NUMBER_DIGITS + _BILLING_RUN_DIGITS :]
    return DecodedQrrReference(
        customer_number=int(customer_number_digits),
        billing_run_id=int(billing_run_digits),
        item_id=int(item_digits),
    )
