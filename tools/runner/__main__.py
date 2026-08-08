"""Entrypoint so the runner runs as `python -m tools.runner`."""

import sys

from tools.runner.main import main

if __name__ == "__main__":
    sys.exit(main())
