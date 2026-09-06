"""Local, gitignored secrets -- the leg-ittigen.ch API token and the
Microsoft Graph credentials used to send email.

Copy `config.example.json` (committed, placeholder only) to
`config.local.json` in the project root and fill in the real values;
`config.local.*` is reserved in `.gitignore`. Never put a real token/secret
in `config.example.json`, in code, in a test, or in a commit message.
"""

import json
from dataclasses import dataclass

from app.paths import PROJECT_ROOT

#: Path of the local, gitignored secrets file.
CONFIG_LOCAL_PATH = PROJECT_ROOT / "config.local.json"


class ConfigError(Exception):
    """Raised when a required local config value is missing or empty."""


def _load_local_config() -> dict:
    """Read and parse `config.local.json`.

    Returns:
        The parsed JSON as a dict.

    Raises:
        ConfigError: If the file does not exist or is not valid JSON.
    """
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
    """Read one required, non-empty string key from a parsed config dict.

    Args:
        data: Parsed `config.local.json` contents.
        key: Key to read.

    Returns:
        The value, stripped.

    Raises:
        ConfigError: If the key is missing or empty.
    """
    value = str(data.get(key, "")).strip()
    if not value:
        raise ConfigError(f"„{key}“ fehlt oder ist leer in {CONFIG_LOCAL_PATH.name}.")
    return value


def get_leg_api_token() -> str:
    """Read the leg-ittigen.ch registration API token from `config.local.json`.

    Returns:
        The configured token.

    Raises:
        ConfigError: If `config.local.json` does not exist, is not valid
            JSON, or has no non-empty `leg_api_token` key.
    """
    return _require(_load_local_config(), "leg_api_token")


@dataclass
class GraphConfig:
    """Microsoft Graph API credentials for sending email as one mailbox.

    Attributes:
        tenant_id: Entra ID tenant id.
        client_id: App registration (application) id.
        client_secret: App registration client secret.
        sender_address: Mailbox to send as (e.g. "leg@example.ch") -- the
            app registration's `Mail.Send` application permission allows
            sending as any mailbox in the tenant unless an admin has
            restricted it via an Application Access Policy, so this is
            simply a request parameter, not fixed by the credentials.
        sender_name: Display name shown for `sender_address`, or `""` to
            let Exchange Online use the mailbox's own configured name.
    """

    tenant_id: str
    client_id: str
    client_secret: str
    sender_address: str
    sender_name: str


def get_graph_config() -> GraphConfig:
    """Read the Microsoft Graph API credentials from `config.local.json`.

    Returns:
        The configured `GraphConfig`.

    Raises:
        ConfigError: If `config.local.json` does not exist, is not valid
            JSON, or is missing any required key (all except
            `graph_sender_name`, which is optional).
    """
    data = _load_local_config()
    return GraphConfig(
        tenant_id=_require(data, "graph_tenant_id"),
        client_id=_require(data, "graph_client_id"),
        client_secret=_require(data, "graph_client_secret"),
        sender_address=_require(data, "graph_sender_address"),
        sender_name=str(data.get("graph_sender_name", "")).strip(),
    )
