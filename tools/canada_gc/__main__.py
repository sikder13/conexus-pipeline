"""Entrypoint so the Canada loader runs as `python -m tools.canada_gc`."""

import sys

from tools.canada_gc.main import main

if __name__ == "__main__":
    sys.exit(main())
