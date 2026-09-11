"""Entry point so the sweep runs as `python -m tools.release`."""

from tools.release.main import main

if __name__ == "__main__":
    raise SystemExit(main())
