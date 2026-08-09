"""Local, gitignored secrets -- currently just the leg-ittigen.ch API token.

Copy `config.example.json` (committed, placeholder only) to
`config.local.json` in the project root and fill in the real value;
`config.local.*` is reserved in `.gitignore`. Never put a real token in
`config.example.json`, in code, in a test, or in a commit message.
"""

import json

from app.paths import PROJECT_ROOT

#: Path of the local, gitignored secrets file.
CONFIG_LOCAL_PATH = PROJECT_ROOT / "config.local.json"


class ConfigError(Exception):
    """Raised when a required local config value is missing or empty."""


def get_leg_api_token() -> str:
    """Read the leg-ittigen.ch registration API token from `config.local.json`.

    Returns:
        The configured token.

    Raises:
        ConfigError: If `config.local.json` does not exist, is not valid
            JSON, or has no non-empty `leg_api_token` key.
    """
    if not CONFIG_LOCAL_PATH.exists():
        raise ConfigError(
            f"{CONFIG_LOCAL_PATH.name} fehlt. Kopieren Sie config.example.json zu "
            f"{CONFIG_LOCAL_PATH.name} und tragen Sie dort den API-Token ein."
        )
    try:
        data = json.loads(CONFIG_LOCAL_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{CONFIG_LOCAL_PATH.name} ist kein gültiges JSON: {exc}") from exc

    token = data.get("leg_api_token", "").strip()
    if not token:
        raise ConfigError(
            f"„leg_api_token“ fehlt oder ist leer in {CONFIG_LOCAL_PATH.name}."
        )
    return token
