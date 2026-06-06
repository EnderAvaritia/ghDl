"""
Command-line interface argument parsing and subcommand dispatch.

Defines the CLI grammar and maps parsed arguments to the
appropriate module functions.

All user-facing output goes to stdout (info) or stderr (errors).
"""

from __future__ import annotations

import argparse
import sys

from gh_downloader import __version__
from gh_downloader.api import GitHubClient, GitHubError
from gh_downloader.config import ConfigError, create_example_config, load_config
from gh_downloader.downloader import DownloadManager, DownloadResult
from gh_downloader.utils import format_size, format_speed, parse_repo_string

# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct and return the top-level argument parser with all subcommands.

    Returns:
        A fully configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="gh-dl",
        description="Batch GitHub Release downloader.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # -- download -----------------------------------------------------------
    dl = subparsers.add_parser("download", help="Download assets from a single repository")
    dl.add_argument(
        "repo",
        help='Repository (owner/repo or full GitHub URL, e.g. "stedolan/jq")',
    )
    dl.add_argument(
        "--pattern",
        "-p",
        required=True,
        action="append",
        dest="patterns",
        help="Glob pattern to match asset filenames (repeatable, required)",
    )
    dl.add_argument(
        "--version",
        "-v",
        default="latest",
        help='Release version or tag (default: "latest")',
    )
    dl.add_argument(
        "--output",
        "-o",
        default="./downloads",
        help="Output directory (default: ./downloads)",
    )
    dl.add_argument(
        "--flat",
        action="store_true",
        help="Download flat, without owner/repo/version hierarchy",
    )
    dl.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate download without actually writing files",
    )
    dl.add_argument(
        "--concurrent",
        "-j",
        type=int,
        default=4,
        help="Number of concurrent downloads (default: 4)",
    )
    dl.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass local cache and re-download all assets",
    )
    dl.add_argument(
        "--regex",
        action="store_true",
        help="Treat --pattern as regular expressions instead of globs",
    )

    # -- config -------------------------------------------------------------
    cfg = subparsers.add_parser(
        "config", help="Download assets defined in a configuration file"
    )
    cfg.add_argument(
        "config_file",
        help="Path to configuration file (JSON or YAML)",
    )
    cfg.add_argument(
        "--output",
        "-o",
        help="Global output directory override for all repos",
    )
    cfg.add_argument(
        "--flat",
        action="store_true",
        help="Download flat, without owner/repo/version hierarchy",
    )
    cfg.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate download without actually writing files",
    )
    cfg.add_argument(
        "--concurrent",
        "-j",
        type=int,
        default=4,
        help="Number of concurrent downloads (default: 4)",
    )
    cfg.add_argument(
        "--regex",
        action="store_true",
        help="Treat patterns as regular expressions instead of globs",
    )

    # -- init ---------------------------------------------------------------
    init = subparsers.add_parser(
        "init", help="Generate an example configuration file"
    )
    init.add_argument(
        "output",
        nargs="?",
        default="gh-dl-config.json",
        help='Output path (default: gh-dl-config.json)',
    )

    # -- list ---------------------------------------------------------------
    lst = subparsers.add_parser(
        "list", help="List downloadable assets in a release"
    )
    lst.add_argument(
        "repo",
        help='Repository (owner/repo or full GitHub URL)',
    )
    lst.add_argument(
        "--version",
        "-v",
        default="latest",
        help='Release version or tag (default: "latest")',
    )

    return parser


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------


def _progress_callback(name: str, current: int, total: int, speed: float) -> None:
    """Print a single-line progress bar for asset download.

    Uses carriage return to overwrite the current line, avoiding
    terminal spam.  Final status (100%) prints a newline.

    Parameters
    ----------
    name:
        Asset filename.
    current:
        Bytes downloaded so far.
    total:
        Total bytes of the asset.
    speed:
        Transfer rate in bytes/second.
    """
    if total <= 0:
        return

    pct = current / total
    bar_width = 20
    filled = int(bar_width * pct)
    bar = "#" * filled + "-" * (bar_width - filled)
    pct_display = f"{pct * 100:.0f}"
    current_str = format_size(current)
    total_str = format_size(total)
    speed_str = format_speed(speed)

    print(
        f"  {name}: [{bar}] {pct_display:>3}% {current_str}/{total_str} {speed_str}",
        end="\r" if current < total else "\n",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_download(args: argparse.Namespace) -> int:
    """Execute the ``download`` subcommand.

    Returns:
        0 on success, 1 if some downloads failed.
    """
    owner, repo = parse_repo_string(args.repo)
    repo_full = f"{owner}/{repo}"

    client = GitHubClient()
    manager = DownloadManager(client=client)

    result: DownloadResult = manager.download_release(
        repo=repo_full,
        pattern=args.patterns,
        version=args.version,
        output_dir=args.output,
        flat=args.flat,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        max_workers=args.concurrent,
        use_regex=args.regex,
        progress_callback=_progress_callback,
    )

    # Print summary
    if args.dry_run:
        _print_dry_run_summary(owner, repo, result)
    else:
        _print_download_summary(owner, repo, result)

    if result.failed > 0:
        # Print error messages so user knows what went wrong
        for err in result.errors:
            print(f"  Error: {err}", file=sys.stderr)
            err_lower = str(err).lower()
            if "not found" in err_lower or "404" in err_lower or "rate limit" in err_lower:
                return 2
        return 1
    return 0


def _handle_config(args: argparse.Namespace) -> int:
    """Execute the ``config`` subcommand.

    Returns:
        0 if all downloads succeeded, 1 if any failed.
    """
    config = load_config(args.config_file)

    total_downloaded = 0
    total_failed = 0

    for repo_cfg in config.repos:
        output_dir = args.output or repo_cfg.output or "./downloads"
        repo_full = f"{repo_cfg.owner}/{repo_cfg.repo}"
        label = repo_full

        if args.dry_run:
            print(f"[{label}] Simulating download...")
        else:
            print(f"[{label}] Downloading...")

        client = GitHubClient()
        manager = DownloadManager(client=client)

        result: DownloadResult = manager.download_release(
            repo=repo_full,
            pattern=repo_cfg.pattern,
            version=repo_cfg.version,
            output_dir=output_dir,
            flat=args.flat,
            dry_run=args.dry_run,
            max_workers=args.concurrent,
            use_regex=args.regex,
            progress_callback=_progress_callback,
        )

        if args.dry_run:
            _print_dry_run_summary(repo_cfg.owner, repo_cfg.repo, result)
        else:
            _print_download_summary(repo_cfg.owner, repo_cfg.repo, result)

        total_downloaded += result.downloaded
        total_failed += result.failed

    print(f"Summary: {total_downloaded} downloaded, {total_failed} failed")

    if total_failed > 0:
        return 1
    return 0


def _handle_init(args: argparse.Namespace) -> int:
    """Execute the ``init`` subcommand.

    Returns:
        0 on success.
    """
    create_example_config(args.output)
    print(f"Example configuration written to {args.output}")
    return 0


def _handle_list(args: argparse.Namespace) -> int:
    """Execute the ``list`` subcommand.

    Returns:
        0 on success.
    """
    owner, repo = parse_repo_string(args.repo)
    repo_full = f"{owner}/{repo}"

    client = GitHubClient()
    release = client.get_release(repo_full, args.version)
    assets = client.get_assets(repo_full, release["id"])

    tag_name = release.get("tag_name", args.version)
    print(f"Assets for {owner}/{repo} ({tag_name}):")

    if not assets:
        print("  (no assets)")
        return 0

    for asset in assets:
        name = asset["name"]
        size = asset.get("size", 0)
        url = GitHubClient.get_asset_download_url(asset)
        print(f"  {name:50s} {format_size(size):>10s}")
        print(f"  {'→':>2s} {url}")

    return 0


# ---------------------------------------------------------------------------
# Pretty-printing helpers
# ---------------------------------------------------------------------------


def _print_download_summary(owner: str, repo: str, result: DownloadResult) -> None:
    """Print a human-readable download summary line."""
    label = f"{owner}/{repo}"
    good = result.downloaded
    bad = result.failed
    total = result.total

    if bad:
        print(f"  [{label}] {good}/{total} assets downloaded, {bad} failed")
    elif total == 0:
        print(f"  [{label}] No matching assets found")
    else:
        print(f"  [{label}] {good}/{total} assets downloaded successfully")


def _print_dry_run_summary(owner: str, repo: str, result: DownloadResult) -> None:
    """Print a human-readable dry-run summary line."""
    label = f"{owner}/{repo}"
    total = result.total
    print(f"  [{label}] Would download {total} asset(s) (dry run)")


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


def run_cli(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, dispatch to the appropriate subcommand handler.

    This function **does not** call :func:`sys.exit`.  The caller (typically
    ``__main__.py`` or a console-script entry point) should use the return
    value as the process exit code.

    Args:
        argv: Argument list (defaults to :data:`sys.argv[1:]`).

    Returns:
        Exit code:
        - ``0``: all operations completed successfully.
        - ``1``: partial failure (some downloads failed).
        - ``2``: fatal error (bad config, API error, missing file, interrupt).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        from gh_downloader.interactive import run_interactive

        run_interactive(output_dir="./downloads")
        return 0

    try:
        if args.subcommand == "download":
            return _handle_download(args)
        elif args.subcommand == "config":
            return _handle_config(args)
        elif args.subcommand == "init":
            return _handle_init(args)
        elif args.subcommand == "list":
            return _handle_list(args)
        else:
            # Should not happen with argparse, but be defensive
            print(f"Unknown subcommand: {args.subcommand}", file=sys.stderr)
            return 2
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except GitHubError as exc:
        print(f"GitHub API error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"File not found: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 2
