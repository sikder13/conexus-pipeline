"""Entrypoint so the extractor runs as `python -m tools.extractor`."""

import sys

from tools.extractor.main import main

if __name__ == "__main__":
    sys.exit(main())
