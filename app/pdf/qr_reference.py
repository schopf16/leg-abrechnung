"""Generates the QRR payment reference number printed on QR-invoices.

A QRR reference is 26 digits plus one Modulo-10-recursive check digit
(computed here via `stdnum.ch.esr`, the same algorithm library `qrbill`
itself uses to validate references). It uniquely encodes which billing run
item an incoming payment belongs to, so a bank statement can be matched
back to the right invoice without manual lookup.
"""

from stdnum.ch import esr

#: Width, in digits, of each encoded component before the check digit.
#: The three widths sum to 26, the QRR payload length (27 digits total
#: once the check digit computed below is appended).
_PERSON_DIGITS = 8
_BILLING_RUN_DIGITS = 6
_ITEM_DIGITS = 12


def generate_qrr_reference(person_id: int, billing_run_id: int, item_id: int) -> str:
    """Build a unique, valid 27-digit QRR reference for one invoice.

    Args:
        person_id: Database id of the billed person.
        billing_run_id: Database id of the billing run.
        item_id: Database id of the billing run line item (the invoice).

    Returns:
        A 27-digit numeric string (26 payload digits + 1 check digit)
        suitable for `qrbill.QRBill(reference_number=...)`.

    Raises:
        ValueError: If any id is too large to fit its allotted digit width.
    """
    payload = (
        f"{person_id:0{_PERSON_DIGITS}d}"
        f"{billing_run_id:0{_BILLING_RUN_DIGITS}d}"
        f"{item_id:0{_ITEM_DIGITS}d}"
    )
    if len(payload) != _PERSON_DIGITS + _BILLING_RUN_DIGITS + _ITEM_DIGITS:
        raise ValueError(
            "One of person_id, billing_run_id or item_id is too large "
            "to encode in a QRR reference."
        )
    check_digit = esr.calc_check_digit(payload)
    return payload + check_digit
