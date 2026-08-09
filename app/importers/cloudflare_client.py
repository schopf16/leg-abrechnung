"""Client for the leg-ittigen.ch registration inbox API.

Fixed API contract (project brief, not to be changed here):

    GET https://leg-ittigen-api.leg-ittigen.workers.dev/submissions?since=<id>
    Authorization: Bearer <token>

Returns a JSON array ascending by id, capped at 500 entries per call. The
website's separate "Rückfragen" (question) form is emailed directly and
never stored server-side, so `form_type` is currently always
`"registration"` -- filtered defensively here in case that ever changes.

The registration form's `payload` fields are deliberately named to mirror
`app.models.person.Person` almost 1:1, plus a `meters` list (zero, one or
several `{meter_number, note}` entries -- a registration can report
several meters, e.g. separately for a PV system, the main house, and a
switched heat pump).
"""

from dataclasses import dataclass, field

import httpx

#: Base URL of the leg-ittigen.ch Cloudflare Worker API. Fixed contract,
#: not configurable.
API_BASE_URL = "https://leg-ittigen-api.leg-ittigen.workers.dev"
_REGISTRATION_FORM_TYPE = "registration"
_REQUEST_TIMEOUT_SECONDS = 15.0


@dataclass
class RegistrationSubmission:
    """One raw registration submission from the leg-ittigen.ch API.

    Attributes:
        cloudflare_id: The submission's id in the source system.
        firma: Submitted company name, or `""`.
        anrede: Submitted salutation (`""`/`"Herr"`/`"Frau"`/`"Familie"`).
        vorname: Submitted first name.
        nachname: Submitted last name.
        strasse: Submitted street name (without house number).
        hausnummer: Submitted house number.
        plz: Submitted postal code.
        ort: Submitted city.
        email: Submitted email address -- possibly empty, callers must
            handle that case (see `app.importers.registration_sync`).
        telefon: Optional submitted phone number.
        bkw_kundennummer: Submitted BKW customer number, free text.
        iban: Optional submitted IBAN, free text.
        message: Optional free-text remark from the submitter.
        submitted_at: Submission timestamp as reported by the API.
        meters: Reported Zählernummern as `(meter_number, note)` tuples,
            zero, one or several. Entries with an empty `meter_number`
            are dropped -- they carry no usable information.
    """

    cloudflare_id: int
    firma: str
    anrede: str
    vorname: str
    nachname: str
    strasse: str
    hausnummer: str
    plz: str
    ort: str
    email: str
    telefon: str
    bkw_kundennummer: str
    iban: str
    message: str
    submitted_at: str
    meters: list[tuple[str, str]] = field(default_factory=list)


class CloudflareAuthError(Exception):
    """Raised when the API rejects the request due to a missing/invalid token (401)."""


class CloudflareApiError(Exception):
    """Raised for any other failure talking to the registration API
    (network error, non-200/401 status, or an unparseable response).
    """


def fetch_new_registrations(since: int, token: str) -> list[RegistrationSubmission]:
    """Fetch up to 500 registration submissions newer than `since`.

    Args:
        since: Only submissions with `id > since` are returned.
        token: Bearer token for the API (see `app.config.get_leg_api_token`).

    Returns:
        Submissions ascending by id. `form_type != "registration"` entries
        are filtered out defensively (the API is not currently expected
        to ever return any).

    Raises:
        CloudflareAuthError: If the token is missing or invalid (HTTP 401).
        CloudflareApiError: For any other network or HTTP failure, or an
            unparseable response body.
    """
    try:
        response = httpx.get(
            f"{API_BASE_URL}/submissions",
            params={"since": since},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.RequestError as exc:
        raise CloudflareApiError(f"Registrierungs-API nicht erreichbar: {exc}") from exc

    if response.status_code == 401:
        raise CloudflareAuthError(
            "Registrierungs-API hat den Zugriff verweigert (401) -- "
            "API-Token in config.local.json prüfen."
        )
    if response.status_code != 200:
        raise CloudflareApiError(
            f"Registrierungs-API antwortete mit Status {response.status_code}: "
            f"{response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudflareApiError(
            f"Registrierungs-API lieferte kein gültiges JSON: {exc}"
        ) from exc

    return [
        _to_submission(entry)
        for entry in payload
        if entry.get("form_type", _REGISTRATION_FORM_TYPE) == _REGISTRATION_FORM_TYPE
    ]


def _to_meters(raw_meters: object) -> list[tuple[str, str]]:
    """Parse the `meters` list of one submission's payload.

    Args:
        raw_meters: The raw `payload["meters"]` value -- expected to be a
            list of `{"meter_number": ..., "note": ...}` objects, but
            handled defensively if missing or malformed.

    Returns:
        `(meter_number, note)` tuples, stripped, entries with an empty
        `meter_number` dropped.
    """
    if not isinstance(raw_meters, list):
        return []
    meters = []
    for entry in raw_meters:
        if not isinstance(entry, dict):
            continue
        meter_number = (entry.get("meter_number") or "").strip()
        if not meter_number:
            continue
        note = (entry.get("note") or "").strip()
        meters.append((meter_number, note))
    return meters


def _to_submission(entry: dict) -> RegistrationSubmission:
    """Convert one raw API entry into a `RegistrationSubmission`.

    Args:
        entry: One JSON object from the API response (`payload` already
            parsed into a nested object by the API itself).

    Returns:
        The corresponding `RegistrationSubmission`.
    """
    payload = entry.get("payload") or {}
    return RegistrationSubmission(
        cloudflare_id=entry["id"],
        firma=(payload.get("firma") or "").strip(),
        anrede=(payload.get("anrede") or "").strip(),
        vorname=(payload.get("vorname") or "").strip(),
        nachname=(payload.get("nachname") or "").strip(),
        strasse=(payload.get("strasse") or "").strip(),
        hausnummer=(payload.get("hausnummer") or "").strip(),
        plz=(payload.get("plz") or "").strip(),
        ort=(payload.get("ort") or "").strip(),
        email=(payload.get("email") or "").strip(),
        telefon=(payload.get("telefon") or "").strip(),
        bkw_kundennummer=(payload.get("bkw_kundennummer") or "").strip(),
        iban=(payload.get("iban") or "").strip(),
        message=(payload.get("message") or "").strip(),
        submitted_at=entry.get("created_at", ""),
        meters=_to_meters(payload.get("meters")),
    )
