"""Allow running via `python -m manuscript_watch_cli`."""

from manuscript_watch_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
