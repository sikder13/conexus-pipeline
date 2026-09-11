"""Entry point so the re-validator runs as `python -m tools.revalidate`."""

from tools.revalidate.main import main

if __name__ == "__main__":
    raise SystemExit(main())
