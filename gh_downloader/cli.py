"""
Command-line interface argument parsing and subcommand dispatch.

Defines the CLI grammar and maps parsed arguments to the
appropriate module functions.
"""

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Construct and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="gh-dl",
        description="Batch GitHub Release downloader.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="gh-dl 0.1.0",
    )
    return parser


def run(args: argparse.Namespace | None = None) -> int:
    """Dispatch parsed arguments and return an exit code."""
    return 0
