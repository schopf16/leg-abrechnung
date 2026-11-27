"""Local, gitignored secrets -- the leg-ittigen.ch API token and the email credentials (Microsoft
Graph or SMTP/IMAP)."""

import json
from dataclasses import dataclass
from typing import Union

from app.paths import PROJECT_ROOT

#: Path of the local, gitignored secrets file.
CONFIG_LOCAL_PATH = PROJECT_ROOT / "config.local.json"


class ConfigError(Exception):
    """Raised when a required local config value is missing or empty."""


def _load_local_config() -> dict:
    """Read and parse `config.local.json`."""
    if not CONFIG_LOCAL_PATH.exists():
        raise ConfigError(
            f"{CONFIG_LOCAL_PATH.name} fehlt. Kopieren Sie config.example.json zu "
            f"{CONFIG_LOCAL_PATH.name} und tragen Sie dort die Werte ein."
        )
    try:
        return json.loads(CONFIG_LOCAL_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{CONFIG_LOCAL_PATH.name} ist kein gültiges JSON: {exc}") from exc


def _require(data: dict, key: str) -> str:
    """Read one required, non-empty string key from a parsed config dict."""
    value = str(data.get(key, "")).strip()
    if not value:
        raise ConfigError(f"„{key}“ fehlt oder ist leer in {CONFIG_LOCAL_PATH.name}.")
    return value


def get_leg_api_token() -> str:
    """Read the leg-ittigen.ch registration API token from `config.local.json`."""
    return _require(_load_local_config(), "leg_api_token")


@dataclass
class GraphConfig:
    """Microsoft Graph API credentials for sending email as one mailbox."""

    tenant_id: str
    client_id: str
    client_secret: str
    sender_address: str
    sender_name: str


def get_graph_config() -> GraphConfig:
    """Read the Microsoft Graph API credentials from `config.local.json`."""
    return _graph_config_from(_load_local_config())


def _graph_config_from(data: dict) -> GraphConfig:
    """Build and validate a `GraphConfig` from a parsed config dict."""
    return GraphConfig(
        tenant_id=_require(data, "graph_tenant_id"),
        client_id=_require(data, "graph_client_id"),
        client_secret=_require(data, "graph_client_secret"),
        sender_address=_require(data, "graph_sender_address"),
        sender_name=str(data.get("graph_sender_name", "")).strip(),
    )


TRANSPORT_GRAPH = "graph"
TRANSPORT_SMTP = "smtp"
TRANSPORTS = (TRANSPORT_GRAPH, TRANSPORT_SMTP)

SECURITY_SSL = "ssl"
SECURITY_STARTTLS = "starttls"
#: No plain-text option on purpose: the password would travel unencrypted.
SECURITIES = (SECURITY_SSL, SECURITY_STARTTLS)
DEFAULT_SMTP_PORTS = {SECURITY_SSL: 465, SECURITY_STARTTLS: 587}
DEFAULT_IMAP_PORT = 993
DEFAULT_SEND_DELAY_SECONDS = 1.0
MAX_SEND_DELAY_SECONDS = 60.0


@dataclass(frozen=True)
class SmtpConfig:
    """SMTP credentials for sending, plus IMAP for the copy in the Sent folder (same login)."""

    host: str
    port: int
    security: str
    username: str
    password: str
    sender_address: str
    sender_name: str
    send_delay_seconds: float
    imap_enabled: bool
    imap_host: str
    imap_port: int
    #: Empty: found via the server's `\Sent` flag or a usual name.
    imap_sent_folder: str


MailConfig = Union[GraphConfig, SmtpConfig]


def get_mail_config() -> MailConfig:
    """Read the configured email transport -- Graph unless `mail_transport` says `smtp`."""
    return mail_config_from(_load_local_config())


def mail_config_from(data: dict) -> MailConfig:
    """Build and validate the transport a config dict selects -- also for a form not yet saved."""
    transport = str(data.get("mail_transport", TRANSPORT_GRAPH)).strip() or TRANSPORT_GRAPH
    if transport == TRANSPORT_GRAPH:
        return _graph_config_from(data)
    if transport == TRANSPORT_SMTP:
        return _smtp_config_from(data)
    raise ConfigError(f"„mail_transport“ muss „graph“ oder „smtp“ sein, nicht „{transport}“.")


def _smtp_config_from(data: dict) -> SmtpConfig:
    """Build and validate an `SmtpConfig` from a parsed config dict."""
    security = str(data.get("smtp_security", SECURITY_SSL)).strip().lower() or SECURITY_SSL
    if security not in SECURITIES:
        raise ConfigError(f"„smtp_security“ muss „ssl“ oder „starttls“ sein, nicht „{security}“.")
    host = _require(data, "smtp_host")
    sender_address = _require(data, "smtp_sender_address")
    if "@" not in sender_address:
        raise ConfigError(f"„smtp_sender_address“ ist keine E-Mail-Adresse: „{sender_address}“.")
    imap_sent_folder = str(data.get("imap_sent_folder", "")).strip()
    if not imap_sent_folder.isascii():
        raise ConfigError(
            "„imap_sent_folder“ bitte leer lassen (automatisch) oder so angeben, wie der Server den "
            "Ordner meldet (nur ASCII-Zeichen)."
        )
    return SmtpConfig(
        host=host,
        port=_port(data, "smtp_port", DEFAULT_SMTP_PORTS[security]),
        security=security,
        username=_require(data, "smtp_username"),
        password=_require(data, "smtp_password"),
        sender_address=sender_address,
        sender_name=str(data.get("smtp_sender_name", "")).strip(),
        send_delay_seconds=_send_delay(data),
        imap_enabled=_flag(data, "imap_enabled", True),
        imap_host=str(data.get("imap_host", "")).strip() or host,
        imap_port=_port(data, "imap_port", DEFAULT_IMAP_PORT),
        imap_sent_folder=imap_sent_folder,
    )


def _port(data: dict, key: str, default: int) -> int:
    """A TCP port; empty means the default for the chosen encryption."""
    raw = data.get(key)
    if raw in (None, ""):
        return default
    try:
        port = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"„{key}“ ist keine Zahl: „{raw}“.") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"„{key}“ muss zwischen 1 und 65535 liegen, nicht {port}.")
    return port


def _send_delay(data: dict) -> float:
    """Seconds between two messages of a batch, so the provider does not throttle or flag us."""
    raw = data.get("smtp_send_delay_seconds")
    if raw in (None, ""):
        return DEFAULT_SEND_DELAY_SECONDS
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"„smtp_send_delay_seconds“ ist keine Zahl: „{raw}“.") from None
    if not 0 <= delay <= MAX_SEND_DELAY_SECONDS:
        raise ConfigError(
            f"„smtp_send_delay_seconds“ muss zwischen 0 und {MAX_SEND_DELAY_SECONDS:g} liegen, nicht {delay:g}."
        )
    return delay


def _flag(data: dict, key: str, default: bool) -> bool:
    """A JSON boolean; the strings "true"/"false" from a hand-edited file count too."""
    raw = data.get(key, default)
    if isinstance(raw, str):
        return raw.strip().lower() not in ("false", "0", "no", "nein", "")
    return bool(raw)
