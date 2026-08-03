"""Convenience launcher so the app can be started with `python run.py`.

Equivalent to running `python -m app.main` directly; both work.
"""

from app.main import main

if __name__ in {"__main__", "__mp_main__"}:
    main()
