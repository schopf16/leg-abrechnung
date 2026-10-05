"""The running app's version, for display in the GUI."""

import shutil
import subprocess
import sys
from typing import Optional

from app.paths import PROJECT_ROOT

#: Fallback shown when the version genuinely cannot be determined.
_UNKNOWN_VERSION = "unbekannt"

#: Avoid a briefly flashing console window when spawning git.exe from
#: this GUI app on Windows.
_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _find_git_executable() -> Optional[str]:
    """Locate a usable git executable, mirroring update.bat's own fallback."""
    system_git = shutil.which("git")
    if system_git:
        return system_git
    portable_git = PROJECT_ROOT / ".mingit" / "cmd" / "git.exe"
    if portable_git.exists():
        return str(portable_git)
    return None


def _read_app_version() -> str:
    """Read the current commit's date and short hash via git."""
    git_exe = _find_git_executable()
    if git_exe is None:
        return _UNKNOWN_VERSION
    try:
        result = subprocess.run(
            [
                git_exe,
                "-C",
                str(PROJECT_ROOT),
                "log",
                "-1",
                "--date=format:%Y-%m-%d",
                "--format=%cd (%h)",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            creationflags=_CREATION_FLAGS,
        )
    except (subprocess.SubprocessError, OSError):
        return _UNKNOWN_VERSION
    return result.stdout.strip() or _UNKNOWN_VERSION


#: The running app's version, ready to display -- see module docstring.
APP_VERSION = _read_app_version()
