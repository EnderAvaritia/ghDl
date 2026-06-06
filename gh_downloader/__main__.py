"""
Entry point for the gh-dl CLI tool.

Executed when running ``python -m gh_downloader`` or via the ``gh-dl`` console script.
"""

import argparse
import sys

from gh_downloader import __version__


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand."""
    parser = argparse.ArgumentParser(
        prog="gh-dl",
        description="Batch GitHub Release downloader.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.parse_args(argv)
    # TODO: wire up cli entry when subcommands are implemented
    print("gh-dl: use --help for usage information")


if __name__ == "__main__":
    main()
