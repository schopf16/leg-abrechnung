"""IBAN validation: structural checks plus the ISO 7064 MOD-97-10 checksum
that is actually built into every IBAN's check digits.

Uses `python-stdnum` (already a transitive dependency via `qrbill`) rather
than reimplementing the checksum -- it is the same well-tested
implementation `qrbill` itself relies on when building a QR-bill.
"""

from typing import Optional

from qrbill.bill import QR_IID
from stdnum import iban as iban_stdnum
from stdnum.exceptions import ValidationError

#: German translations for the stdnum exception classes IBAN validation can
#: raise, keyed by class name (stdnum has no stable public error-code enum).
_ERROR_MESSAGES = {
    "InvalidFormat": "enthält ungültige Zeichen oder ist unvollständig",
    "InvalidLength": "hat für das jeweilige Land die falsche Länge",
    "InvalidChecksum": "hat eine falsche Prüfziffer (Zahlendreher oder Tippfehler?)",
    "InvalidComponent": "entspricht nicht dem Aufbau des jeweiligen Landes",
}


def normalize_iban(value: str) -> str:
    """Strip spaces/dashes and uppercase an IBAN for storage or comparison.

    Args:
        value: Raw user input.

    Returns:
        The compact, uppercase IBAN (e.g. `"CH9300762011623852957"`).
    """
    return value.replace(" ", "").replace("-", "").strip().upper()


def format_iban(value: str) -> str:
    """Group an IBAN into 4-character blocks for display.

    Purely cosmetic (no validation) -- grouping works the same whether or
    not `value` is actually a valid IBAN, so this is safe to use on
    unvalidated input too.

    Args:
        value: IBAN in any spacing.

    Returns:
        The IBAN grouped as `"CH93 0076 2011 6238 5295 7"`.
    """
    candidate = normalize_iban(value)
    if not candidate:
        return value
    return iban_stdnum.format(candidate)


def validate_iban(value: str) -> Optional[str]:
    """Check an IBAN's structure and MOD-97-10 checksum.

    An empty value is treated as valid (the field is optional in this
    app) -- callers that require a value must check for emptiness
    themselves.

    Args:
        value: Raw user input.

    Returns:
        `None` if `value` is empty or a valid IBAN, otherwise a
        human-readable German error message.
    """
    candidate = normalize_iban(value)
    if not candidate:
        return None
    try:
        iban_stdnum.validate(candidate)
    except ValidationError as exc:
        reason = _ERROR_MESSAGES.get(type(exc).__name__, str(exc))
        return f"IBAN {reason}."
    return None


def validate_qr_iban(value: str) -> Optional[str]:
    """Like `validate_iban`, but additionally requires a Swiss/Liechtenstein
    QR-IBAN (institution id in the QR-IID range), since this is used as the
    creditor account on the QR-bill payment slip, which requires a QRR
    reference to work at all.

    Args:
        value: Raw user input.

    Returns:
        `None` if `value` is empty or a valid QR-IBAN, otherwise a
        human-readable German error message.
    """
    error = validate_iban(value)
    if error:
        return error
    candidate = normalize_iban(value)
    if not candidate:
        return None
    if candidate[:2] not in ("CH", "LI"):
        return "QR-IBAN muss mit CH oder LI beginnen."
    institution_id = int(candidate[4:9])
    if not (QR_IID["start"] <= institution_id <= QR_IID["end"]):
        return (
            "Das ist eine normale IBAN, keine QR-IBAN (die Bank-ID an "
            f"Stelle 5–9 liegt nicht im Bereich {QR_IID['start']}–"
            f"{QR_IID['end']}). Für den Einzahlungsschein wird eine "
            "echte QR-IBAN benötigt -- bei der Bank erfragen."
        )
    return None
