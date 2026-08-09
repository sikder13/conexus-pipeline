"""Entrypoint so the adapter runs as `python -m tools.grant_rounds`."""

import sys

from tools.grant_rounds.main import main

if __name__ == "__main__":
    sys.exit(main())
