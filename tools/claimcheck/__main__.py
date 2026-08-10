"""Entry point so the checker runs as `python -m tools.claimcheck`."""

from tools.claimcheck.main import main

if __name__ == "__main__":
    raise SystemExit(main())
