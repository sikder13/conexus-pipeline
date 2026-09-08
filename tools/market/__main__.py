"""Entry point so the market gatherer runs as `python -m tools.market`."""

from tools.market.main import main

if __name__ == "__main__":
    raise SystemExit(main())
