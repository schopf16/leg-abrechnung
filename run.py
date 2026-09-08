"""Convenience launcher so the app can be started with `python run.py`.

Equivalent to running `python -m app.main` directly; both work.
"""

from app.main import main

if __name__ == "__main__":
    # Deliberately NOT `{"__main__", "__mp_main__"}`: native mode spawns its
    # window in a child process via `multiprocessing.Process(target=
    # nicegui.native.native_mode._open_window, ...)` -- an explicit target,
    # not a re-invocation of this script's `main()`. Windows' 'spawn' start
    # method still re-imports this file in that child process under the
    # name `__mp_main__` (needed to reconstruct picklable globals), which
    # used to also match this guard and run `main()` a second, redundant
    # time in the window process -- re-migrating the database and emitting
    # a spurious extra startup log entry for a "run" that never actually
    # serves anything (`ui.run()` itself already no-ops there via its own
    # `multiprocessing.current_process().name != 'MainProcess'` check).
    main()
