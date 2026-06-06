"""
Entry point for the gh-dl CLI tool.

Executed when running ``python -m gh_downloader`` or via the ``gh-dl`` console script.
"""

import sys

from gh_downloader.cli import run_cli


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand."""
    sys.exit(run_cli(argv))


if __name__ == "__main__":
    main()
