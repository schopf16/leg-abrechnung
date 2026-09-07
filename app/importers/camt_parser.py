"""Parser for Swiss ISO 20022 bank statement/notification files: camt.053
(the regular account statement) and camt.054 (the dedicated payment
notification, most relevant for QR-bill payments -- see the module
docstring of `app.domain.bank_reconciliation` for why both are needed:
some banks batch several QR payments into one lump-sum camt.053 entry and
only camt.054 breaks them out individually with their own reference).

Both formats share (for the elements this parser reads) the same
`Ntry`/`NtryDtls/TxDtls` structure -- only the root element differs
(`BkToCstmrStmt` vs `BkToCstmrDbtCdtNtfctn`) -- so both are handled by the
same extraction code.

=====================================================================
 IMPORTANT -- VERIFY AGAINST A REAL EXPORT FROM MICHAEL'S BANK
=====================================================================
No real camt.053/camt.054 export was available while this was written.
Both are fully standardized ISO 20022 schemas (unlike BKW's EBIX/SDAT
export, see `app.importers.ebix_parser`), so the structural risk is much
lower, but bank-specific quirks (an absent `AcctSvcrRef`, `DtTm` instead
of `Dt`, an unusual nesting for a batched entry) are only really
confirmed once a real file has been run through `_extract_entries`, which
is deliberately the only function that needs touching if so.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from xml.etree.ElementTree import Element

from lxml import etree

from app.domain.iban_validation import normalize_iban
from app.importers.base import ImportValidationError

#: Local (namespace-stripped) name of the root element for each supported
#: message type.
_CAMT053_ROOT = "BkToCstmrStmt"
_CAMT054_ROOT = "BkToCstmrDbtCdtNtfctn"

#: Local name of the per-statement/-notification container under the root.
_STATEMENT_TAGS = {"camt053": "Stmt", "camt054": "Ntfctn"}

#: Only CHF entries are ever booked -- this app has no foreign-currency
#: handling anywhere (billing, PDFs, QR-bills are all CHF-only).
_SUPPORTED_CURRENCY = "CHF"


@dataclass
class ParsedBankTransaction:
    """One bank statement entry (`TxDtls`, or an `Ntry` with no `TxDtls`
    breakdown), not yet matched to a person.

    Attributes:
        bank_reference: `AcctSvcrRef` if present, else `NtryRef`, else a
            synthesized fallback key unique within this one parsed file
            (see `_reference_for`) -- never empty.
        booking_date: ISO date string (`BookgDt/Dt`, or the date portion
            of `BookgDt/DtTm`).
        amount_rappen: Absolute amount in Rappen, always non-negative.
        currency: ISO 4217 code -- always `"CHF"` (non-CHF entries are
            dropped with a warning before a `ParsedBankTransaction` is
            ever created for them).
        credit_debit_indicator: `"CRDT"` or `"DBIT"`.
        counterparty_name: The *other* party's name -- `Dbtr` for a CRDT
            entry, `Cdtr` for a DBIT one.
        counterparty_iban: The counterparty's normalized IBAN, or `""` if
            unavailable (e.g. only a non-IBAN account id was given).
        structured_reference: Raw digits from `Strd/CdtrRefInf/Ref`, or
            `""` if the entry carries no structured reference.
        remittance_text: Raw unstructured remittance text (`Ustrd`,
            possibly several lines joined by a space), or `""`.
        is_reversal: Whether the bank flagged this entry as a reversal
            (`RvslInd`) -- never auto-matched regardless of its reference.
    """

    bank_reference: str
    booking_date: str
    amount_rappen: int
    currency: str
    credit_debit_indicator: str
    counterparty_name: str
    counterparty_iban: str
    structured_reference: str
    remittance_text: str
    is_reversal: bool


@dataclass
class CamtParseResult:
    """Outcome of parsing one camt.053/camt.054 file.

    Attributes:
        transactions: Successfully parsed, CHF-only transactions.
        warnings: Human-readable (German) messages about entries that
            were skipped (non-CHF) or look incomplete.
        source_format: `"camt053"` or `"camt054"`.
        account_iban: The statement's own account IBAN, normalized, or
            `""` if not present in the file.
        statement_from: Start of the covered period (`FrDtTm`), if stated.
        statement_to: End of the covered period (`ToDtTm`), if stated.
    """

    transactions: list[ParsedBankTransaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_format: str = ""
    account_iban: str = ""
    statement_from: Optional[str] = None
    statement_to: Optional[str] = None


def _make_parser() -> etree.XMLParser:
    """Build a hardened lxml parser that refuses external entities and DTDs.

    Same rationale as `app.importers.ebix_parser._make_parser`: a bank
    statement is still an externally-sourced file.

    Returns:
        A configured `lxml.etree.XMLParser`.
    """
    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        dtd_validation=False, huge_tree=False,
    )


def _local_name(element: Element) -> Optional[str]:
    """Return an element's tag name with any namespace prefix stripped.

    Args:
        element: Element to inspect.

    Returns:
        The local tag name, or `None` if `element.tag` is not a plain
        string (e.g. a comment/processing-instruction node).
    """
    return etree.QName(element.tag).localname if isinstance(element.tag, str) else None


def _find_ns(element: Optional[Element], tag: str) -> Optional[Element]:
    """Find a direct child element by local tag name, ignoring namespaces.

    Args:
        element: Parent element to search, or `None` (returns `None`).
        tag: Local tag name to look for.

    Returns:
        The first matching direct child, or `None`.
    """
    if element is None:
        return None
    for child in element:
        if _local_name(child) == tag:
            return child
    return None


def _find_all_ns(element: Optional[Element], tag: str) -> list[Element]:
    """Find all direct child elements matching a local tag name.

    Args:
        element: Parent element to search, or `None` (returns `[]`).
        tag: Local tag name to look for.

    Returns:
        All matching direct children, in document order.
    """
    if element is None:
        return []
    return [child for child in element if _local_name(child) == tag]


def _find_path(element: Optional[Element], *tags: str) -> Optional[Element]:
    """Walk a chain of direct-child lookups by local tag name.

    Args:
        element: Element to start from.
        *tags: Local tag names to descend through, in order.

    Returns:
        The element reached after following every tag, or `None` if any
        step along the way is missing.
    """
    current = element
    for tag in tags:
        current = _find_ns(current, tag)
        if current is None:
            return None
    return current


def _text(element: Optional[Element]) -> Optional[str]:
    """Return an element's stripped text content.

    Args:
        element: Element to read, or `None`.

    Returns:
        The stripped text, or `None` if `element` is `None` or has no text.
    """
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None


def _find_text(element: Optional[Element], *tags: str) -> Optional[str]:
    """Shorthand for `_text(_find_path(element, *tags))`.

    Args:
        element: Element to start from.
        *tags: Local tag names to descend through, in order.

    Returns:
        The stripped text content at that path, or `None`.
    """
    return _text(_find_path(element, *tags))


def _to_rappen(amount_text: str) -> Optional[int]:
    """Convert a decimal amount string (as found in the XML) to Rappen.

    Uses `Decimal`, never `float`, to avoid binary floating-point
    rounding on money -- same discipline as `app.pdf.person_bill_pdf`.

    Args:
        amount_text: The amount as written in the XML, e.g. `"123.45"`.

    Returns:
        The amount in Rappen, or `None` if it cannot be parsed as a number.
    """
    try:
        return int((Decimal(amount_text) * 100).to_integral_value())
    except (InvalidOperation, TypeError):
        return None


def _iso_date(raw: Optional[str]) -> Optional[str]:
    """Normalize a `Dt`/`DtTm` value to a plain ISO date (`YYYY-MM-DD`).

    Args:
        raw: The raw text, either a plain date or a full timestamp.

    Returns:
        The first 10 characters (the date portion), or `None`.
    """
    return raw[:10] if raw else None


def parse_camt_file(path: Path) -> CamtParseResult:
    """Parse a camt.053 or camt.054 file into `ParsedBankTransaction` objects.

    Args:
        path: Filesystem path of the `.xml` file to parse.

    Returns:
        The parsed transactions plus any non-fatal warnings.

    Raises:
        ImportValidationError: If the file is not well-formed XML, or its
            root element is neither a camt.053 nor a camt.054 message.
    """
    try:
        tree = etree.parse(str(path), parser=_make_parser())
    except etree.XMLSyntaxError as exc:
        raise ImportValidationError(f"Ungültiges XML: {exc}") from exc

    document = tree.getroot()
    message_root = _find_ns(document, _CAMT053_ROOT)
    source_format = "camt053"
    if message_root is None:
        message_root = _find_ns(document, _CAMT054_ROOT)
        source_format = "camt054"
    if message_root is None:
        raise ImportValidationError(
            "Weder camt.053 (BkToCstmrStmt) noch camt.054 "
            "(BkToCstmrDbtCdtNtfctn) gefunden -- unerwartetes Dateiformat. "
            "Siehe Hinweis in app/importers/camt_parser.py."
        )

    return _extract_entries(message_root, source_format)


def _extract_entries(message_root: Element, source_format: str) -> CamtParseResult:
    """Walk every `Stmt`/`Ntfctn` in the message and extract its entries.

    This is the function to revisit once a real export is available --
    see the module docstring.

    Args:
        message_root: The `BkToCstmrStmt`/`BkToCstmrDbtCdtNtfctn` element.
        source_format: `"camt053"` or `"camt054"`.

    Returns:
        The parsed transactions plus any non-fatal warnings.
    """
    result = CamtParseResult(source_format=source_format)
    statement_tag = _STATEMENT_TAGS[source_format]
    statements = _find_all_ns(message_root, statement_tag)
    if not statements:
        result.warnings.append(
            f"Keine {statement_tag}-Elemente in der Datei gefunden -- nichts zu importieren."
        )
        return result

    fallback_index = 0
    for statement in statements:
        iban = _find_text(statement, "Acct", "Id", "IBAN")
        if iban and not result.account_iban:
            result.account_iban = normalize_iban(iban)
        if result.statement_from is None:
            result.statement_from = _iso_date(_find_text(statement, "FrToDt", "FrDtTm"))
        if result.statement_to is None:
            result.statement_to = _iso_date(_find_text(statement, "FrToDt", "ToDtTm"))

        for entry in _find_all_ns(statement, "Ntry"):
            fallback_index = _extract_entry(entry, result, fallback_index)

    return result


def _extract_entry(entry: Element, result: CamtParseResult, fallback_index: int) -> int:
    """Extract every transaction from one `Ntry`, appending to `result`.

    Args:
        entry: One `Ntry` element.
        result: Result to append parsed transactions/warnings to, in place.
        fallback_index: Running counter for synthesized fallback
            references, carried across the whole file (see
            `_reference_for`).

    Returns:
        The updated `fallback_index` after processing this entry.
    """
    entry_ccy = _find_ns(entry, "Amt")
    entry_currency = entry_ccy.get("Ccy") if entry_ccy is not None else None
    entry_cdt_dbt_ind = _find_text(entry, "CdtDbtInd")
    entry_booking_date = _iso_date(
        _find_text(entry, "BookgDt", "Dt") or _find_text(entry, "BookgDt", "DtTm")
    )
    entry_ntry_ref = _find_text(entry, "NtryRef") or ""
    is_reversal = (_find_text(entry, "RvslInd") or "").strip().lower() == "true"

    tx_details_list = [
        tx_details
        for ntry_details in _find_all_ns(entry, "NtryDtls")
        for tx_details in _find_all_ns(ntry_details, "TxDtls")
    ]
    if not tx_details_list:
        # No breakdown at all -- treat the Ntry itself as one implicit
        # transaction (common for a simple, non-batched booking).
        fallback_index = _extract_transaction(
            tx_details=None, entry=entry, entry_currency=entry_currency,
            entry_cdt_dbt_ind=entry_cdt_dbt_ind, entry_booking_date=entry_booking_date,
            entry_ntry_ref=entry_ntry_ref, is_reversal=is_reversal,
            result=result, fallback_index=fallback_index,
        )
        return fallback_index

    for tx_details in tx_details_list:
        fallback_index = _extract_transaction(
            tx_details=tx_details, entry=entry, entry_currency=entry_currency,
            entry_cdt_dbt_ind=entry_cdt_dbt_ind, entry_booking_date=entry_booking_date,
            entry_ntry_ref=entry_ntry_ref, is_reversal=is_reversal,
            result=result, fallback_index=fallback_index,
        )
    return fallback_index


def _extract_transaction(
    *,
    tx_details: Optional[Element],
    entry: Element,
    entry_currency: Optional[str],
    entry_cdt_dbt_ind: Optional[str],
    entry_booking_date: Optional[str],
    entry_ntry_ref: str,
    is_reversal: bool,
    result: CamtParseResult,
    fallback_index: int,
) -> int:
    """Build one `ParsedBankTransaction` from a `TxDtls` (or its parent
    `Ntry`, if there is no `TxDtls` breakdown), appending it to `result`.

    Args:
        tx_details: The `TxDtls` element, or `None` if extracting directly
            from an `Ntry` with no breakdown.
        entry: The parent `Ntry` element (amount/currency/direction/date
            fall back to this level if `tx_details` does not override them).
        entry_currency: Currency from the `Ntry`-level `Amt/@Ccy`.
        entry_cdt_dbt_ind: Direction from the `Ntry`-level `CdtDbtInd`.
        entry_booking_date: Booking date from the `Ntry`-level `BookgDt`.
        entry_ntry_ref: The `Ntry`-level `NtryRef`, used as a fallback
            reference if `tx_details` carries none of its own.
        is_reversal: Whether the parent `Ntry` was flagged `RvslInd`.
        result: Result to append the parsed transaction/warnings to.
        fallback_index: Running counter for synthesized fallback references.

    Returns:
        The updated `fallback_index`.
    """
    tx_amt_element = _find_ns(tx_details, "Amt") if tx_details is not None else None
    if tx_amt_element is None:
        # No transaction-level amount (the normal case for a
        # non-batched Ntry, and for the "no TxDtls at all" fallback
        # path) -- use the parent Ntry's amount instead.
        tx_amt_element = _find_ns(entry, "Amt")
    if tx_amt_element is None:
        result.warnings.append("Buchung ohne Betrag übersprungen.")
        return fallback_index
    currency = tx_amt_element.get("Ccy") or entry_currency
    if currency != _SUPPORTED_CURRENCY:
        booking_date = entry_booking_date or "?"
        result.warnings.append(
            f"Fremdwährung {currency} bei Buchung vom {booking_date} übersprungen -- "
            f"nur {_SUPPORTED_CURRENCY} wird unterstützt."
        )
        return fallback_index

    amount_rappen = _to_rappen(tx_amt_element.text or "")
    if amount_rappen is None:
        result.warnings.append(f"Buchung mit ungültigem Betrag {tx_amt_element.text!r} übersprungen.")
        return fallback_index

    cdt_dbt_ind = (_find_text(tx_details, "CdtDbtInd") if tx_details is not None else None) or entry_cdt_dbt_ind
    if cdt_dbt_ind not in ("CRDT", "DBIT"):
        result.warnings.append("Buchung ohne gültige Richtung (CdtDbtInd) übersprungen.")
        return fallback_index

    related_parties = _find_ns(tx_details, "RltdPties") if tx_details is not None else None
    if cdt_dbt_ind == "CRDT":
        counterparty = _find_ns(related_parties, "Dbtr")
        counterparty_acct = _find_ns(related_parties, "DbtrAcct")
    else:
        counterparty = _find_ns(related_parties, "Cdtr")
        counterparty_acct = _find_ns(related_parties, "CdtrAcct")
    counterparty_name = _find_text(counterparty, "Nm") or ""
    raw_counterparty_iban = _find_text(counterparty_acct, "Id", "IBAN")
    counterparty_iban = normalize_iban(raw_counterparty_iban) if raw_counterparty_iban else ""

    rmt_inf = _find_ns(tx_details, "RmtInf") if tx_details is not None else None
    structured_reference = _find_text(rmt_inf, "Strd", "CdtrRefInf", "Ref") or ""
    remittance_text = " ".join(
        text for element in _find_all_ns(rmt_inf, "Ustrd") if (text := _text(element))
    )

    refs = _find_ns(tx_details, "Refs") if tx_details is not None else None
    tx_level_ref = _find_text(refs, "AcctSvcrRef") or _find_text(refs, "NtryRef")
    bank_reference = tx_level_ref or entry_ntry_ref
    if not bank_reference:
        fallback_index += 1
        bank_reference = _synthesize_reference(
            booking_date=entry_booking_date, amount_rappen=amount_rappen,
            counterparty_key=counterparty_iban or counterparty_name,
            index=fallback_index,
        )

    result.transactions.append(
        ParsedBankTransaction(
            bank_reference=bank_reference,
            booking_date=entry_booking_date or "",
            amount_rappen=amount_rappen,
            currency=currency,
            credit_debit_indicator=cdt_dbt_ind,
            counterparty_name=counterparty_name,
            counterparty_iban=counterparty_iban,
            structured_reference=structured_reference,
            remittance_text=remittance_text,
            is_reversal=is_reversal,
        )
    )
    return fallback_index


def _synthesize_reference(
    *, booking_date: Optional[str], amount_rappen: int, counterparty_key: str, index: int
) -> str:
    """Build a fallback idempotency key for an entry with no bank reference.

    Deliberately includes a running `index` (unique within one parsed
    file): without it, two genuinely different transactions sharing the
    same date/amount/counterparty (e.g. a person paying the same amount
    twice on the same day) would synthesize the *same* key and the second,
    real payment would be wrongly rejected as a duplicate on storage (see
    `app.models.bank_transaction.insert_transaction`'s idempotency
    constraint).

    Args:
        booking_date: The entry's booking date, or `None`.
        amount_rappen: The entry's amount in Rappen.
        counterparty_key: The counterparty's IBAN if known, else name.
        index: Running counter of fallback references used so far in
            this file, made part of the key so two same-day/same-amount/
            same-counterparty entries never collide.

    Returns:
        A synthesized reference string, clearly not a real bank reference
        (prefixed `FALLBACK-`) for easy recognition in logs/the database.
    """
    return f"FALLBACK-{booking_date or '?'}-{amount_rappen}-{counterparty_key or '?'}-{index}"
