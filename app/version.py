"""The running app's version, for display in the GUI.

Deliberately derived from git rather than a manually maintained version
string: every deployed copy of this app *is* a git working copy (cloned
and updated via `update.bat`, see its module docstring), so reading the
current commit here can never drift out of sync with what was actually
last pushed -- no separate "don't forget to bump the version" step to
forget. Shown as `"<commit date> (<short hash>)"`, e.g.
`"2026-09-05 (a3f9d21)"`: easy to read out over the phone (the date alone
is usually enough to tell whether someone is on the latest push), with
the hash as an exact tiebreaker if two commits landed the same day.

Computed once at import time (the running process's code cannot change
while it runs) and never raises -- if git cannot be found or this is not
a git checkout at all, `APP_VERSION` falls back to `"unbekannt"` rather
than blocking app startup over a version label.
"""

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
    """Locate a usable git executable, mirroring update.bat's own fallback.

    Returns:
        Path to a system-wide `git`, or this project's own portable copy
        (downloaded by `update.bat` into `.mingit/` when no system git is
        found), or `None` if neither exists.
    """
    system_git = shutil.which("git")
    if system_git:
        return system_git
    portable_git = PROJECT_ROOT / ".mingit" / "cmd" / "git.exe"
    if portable_git.exists():
        return str(portable_git)
    return None


def _read_app_version() -> str:
    """Read the current commit's date and short hash via git.

    Returns:
        `"<YYYY-MM-DD> (<short hash>)"` of the last commit, or
        `_UNKNOWN_VERSION` if git is unavailable, this is not a git
        checkout, or anything else goes wrong.
    """
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
