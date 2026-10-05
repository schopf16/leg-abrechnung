"""Client for the leg-ittigen.ch registration inbox API."""

from dataclasses import dataclass, field

import httpx

#: Base URL of the leg-ittigen.ch Cloudflare Worker API. Fixed contract,
#: not configurable.
API_BASE_URL = "https://leg-ittigen-api.leg-ittigen.workers.dev"
_REGISTRATION_FORM_TYPE = "registration"
_REQUEST_TIMEOUT_SECONDS = 15.0


@dataclass
class RegistrationSubmission:
    """One raw registration submission from the leg-ittigen.ch API."""

    cloudflare_id: int
    company: str
    salutation: str
    first_name: str
    last_name: str
    street: str
    house_number: str
    postal_code: str
    city: str
    email: str
    phone: str
    bkw_customer_number: str
    iban: str
    message: str
    submitted_at: str
    meters: list[tuple[str, str]] = field(default_factory=list)


class CloudflareAuthError(Exception):
    """Raised when the API rejects the request due to a missing/invalid token (401)."""


class CloudflareApiError(Exception):
    """Raised for any other failure talking to the registration API (network error, non-200/401 status..."""


def fetch_new_registrations(since: int, token: str) -> list[RegistrationSubmission]:
    """Fetch up to 500 registration submissions newer than `since`."""
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
            "Registrierungs-API hat den Zugriff verweigert (401) -- API-Token in config.local.json prüfen."
        )
    if response.status_code != 200:
        raise CloudflareApiError(
            f"Registrierungs-API antwortete mit Status {response.status_code}: {response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudflareApiError(f"Registrierungs-API lieferte kein gültiges JSON: {exc}") from exc

    return [
        _to_submission(entry)
        for entry in payload
        if entry.get("form_type", _REGISTRATION_FORM_TYPE) == _REGISTRATION_FORM_TYPE
    ]


def delete_submissions(ids: list[int], token: str) -> int:
    """Delete submissions from the leg-ittigen.ch Worker database by id."""
    if not ids:
        return 0

    try:
        response = httpx.request(
            "DELETE",
            f"{API_BASE_URL}/submissions",
            json={"ids": ids},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.RequestError as exc:
        raise CloudflareApiError(f"Registrierungs-API nicht erreichbar: {exc}") from exc

    if response.status_code == 401:
        raise CloudflareAuthError(
            "Registrierungs-API hat den Zugriff verweigert (401) -- API-Token in config.local.json prüfen."
        )
    if response.status_code != 200:
        raise CloudflareApiError(
            f"Registrierungs-API antwortete mit Status {response.status_code}: {response.text[:200]}"
        )

    try:
        return response.json()["deleted"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CloudflareApiError(
            f"Registrierungs-API lieferte keine gültige Löschbestätigung: {exc}"
        ) from exc


def _to_meters(raw_meters: object) -> list[tuple[str, str]]:
    """Parse the `meters` list of one submission's payload."""
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
    """Convert one raw API entry into a `RegistrationSubmission`."""
    # NOTE: the payload keys below are the German field names the
    # leg-ittigen.ch form posts -- an external contract we do not
    # control. They must stay German even though everything they are
    # mapped onto is English.
    payload = entry.get("payload") or {}
    return RegistrationSubmission(
        cloudflare_id=entry["id"],
        company=(payload.get("firma") or "").strip(),
        salutation=(payload.get("anrede") or "").strip(),
        first_name=(payload.get("vorname") or "").strip(),
        last_name=(payload.get("nachname") or "").strip(),
        street=(payload.get("strasse") or "").strip(),
        house_number=(payload.get("hausnummer") or "").strip(),
        postal_code=(payload.get("plz") or "").strip(),
        city=(payload.get("ort") or "").strip(),
        email=(payload.get("email") or "").strip(),
        phone=(payload.get("telefon") or "").strip(),
        bkw_customer_number=(payload.get("bkw_kundennummer") or "").strip(),
        iban=(payload.get("iban") or "").strip(),
        message=(payload.get("message") or "").strip(),
        submitted_at=entry.get("created_at", ""),
        meters=_to_meters(payload.get("meters")),
    )
