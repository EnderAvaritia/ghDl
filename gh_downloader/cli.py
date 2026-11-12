"""
Command-line interface argument parsing and subcommand dispatch.

Defines the CLI grammar and maps parsed arguments to the
appropriate module functions.

All user-facing output goes to stdout (info) or stderr (errors).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from gh_downloader import __version__
from gh_downloader.api import GitHubClient, GitHubError
from gh_downloader.config import (
    ConfigError,
    create_example_config,
    create_user_config_example,
    load_config,
    load_user_config,
)
from gh_downloader.downloader import DownloadManager, DownloadResult
from gh_downloader.providers import GitLabClient, create_client
from gh_downloader.utils import (
    display_width,
    fit_line,
    format_size,
    format_speed,
    parse_full_repo_url,
    terminal_width,
)

# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def _add_provider_options(p: argparse.ArgumentParser) -> None:
    """Add shared provider-selection options to a subparser."""
    p.add_argument(
        "--provider",
        choices=("github", "gitlab"),
        default=None,
        help="Release provider (default: auto-detect from URL; github)",
    )
    p.add_argument(
        "--gitlab-url",
        default=None,
        help="GitLab base URL for self-hosted instances (default: https://gitlab.com)",
    )
    p.add_argument(
        "--github-api-url",
        default=None,
        help="GitHub API base URL, e.g. GitHub Enterprise (default: https://api.github.com)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct and return the top-level argument parser with all subcommands.

    Returns:
        A fully configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="gh-dl",
        description=(
            "Batch GitHub/GitLab Release downloader. "
            "gh-dl <owner/repo> is a shortcut for gh-dl download <owner/repo>."
        ),
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
        help='Repository (owner/repo or full GitHub/GitLab URL, e.g. "stedolan/jq")',
    )
    dl.add_argument(
        "--pattern",
        "-p",
        action="append",
        dest="patterns",
        help="Glob pattern to match asset filenames (repeatable; default: download all)",
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
    dl.add_argument(
        "--no-sources",
        action="store_false",
        dest="sources",
        help="Skip source code archives",
    )
    _add_provider_options(dl)

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
    cfg.add_argument(
        "--no-sources",
        action="store_false",
        dest="sources",
        help="Skip source code archives",
    )
    _add_provider_options(cfg)

    # -- init ---------------------------------------------------------------
    init = subparsers.add_parser(
        "init", help="Generate an example configuration file"
    )
    init.add_argument(
        "output",
        nargs="?",
        default=None,
        help='Output path (default: gh-dl-config.json)',
    )
    init.add_argument(
        "--user", "-u",
        action="store_true",
        help="Generate a user config file for persistent settings (token, proxy)",
    )
    init.add_argument(
        "--global",
        dest="global_config",
        action="store_true",
        help=(
            "Write the global user config "
            "(%APPDATA%\\gh-dl\\config.json or ~/.config/gh-dl/config.json)"
        ),
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file",
    )

    # -- list ---------------------------------------------------------------
    lst = subparsers.add_parser(
        "list", help="List downloadable assets in a release"
    )
    lst.add_argument(
        "repo",
        help='Repository (owner/repo or full GitHub/GitLab URL)',
    )
    lst.add_argument(
        "--version",
        "-v",
        default="latest",
        help='Release version or tag (default: "latest")',
    )
    lst.add_argument(
        "--no-sources",
        action="store_false",
        dest="sources",
        help="Hide source code archives from listing",
    )
    _add_provider_options(lst)

    return parser


# ---------------------------------------------------------------------------
# Progress display (thread-safe, no ANSI codes for Windows CMD compat)
# ---------------------------------------------------------------------------


class ProgressTracker:
    """Progress display for concurrent downloads.

    Prints a progress bar (``\\r`` single-line) for the most recently
    updated file, plus a permanent completion line when each file finishes.
    No ANSI escape codes — works on all terminals.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._finished: set[str] = set()
        self._shown_first = False
        self.total_assets = 0
        self._skipped: list[str] = []
        self._last_print_time = 0.0
        self._min_interval = 0.1  # 100ms throttle between screen writes
        self._is_tty = sys.stdout.isatty()

    @property
    def completed_count(self) -> int:
        return len(self._finished)

    def set_total_assets(self, total: int) -> None:
        self.total_assets = total

    def skip(self, name: str) -> None:
        """Record one asset as skipped (cached on disk)."""
        with self._lock:
            self._skipped.append(name)

    def update(self, name: str, current: int, total: int, speed: float) -> None:
        """Called by the downloader for every chunk received."""
        with self._lock:
            if current >= total > 0 and name not in self._finished:
                self._finished.add(name)
                self._print_completion(name, total, speed)
                return

            if not self._is_tty:
                # Redirected output: emit only the permanent completion lines.
                return

            if not self._shown_first:
                print()  # blank line before first progress
                self._shown_first = True
                self._last_print_time = 0.0  # force first progress write

            now = time.time()
            if now - self._last_print_time >= self._min_interval:
                self._last_print_time = now
                self._print_progress(name, current, total, speed)

    def _counter_prefix(self) -> str:
        if self.total_assets > 0:
            done = self.completed_count + len(self._skipped)
            return f"[{done}/{self.total_assets}] "
        return ""

    def _print_progress(self, name: str, current: int, total: int, speed: float) -> None:
        """Draw a single-line progress bar that overwrites itself with ``\\r``.

        The rendered line is truncated to one column less than the terminal
        width so it can never wrap to a second row.  Wrapping would leave the
        overflow on screen and make every update scroll the terminal.
        """
        width = terminal_width() - 1
        prefix = f"  {self._counter_prefix()}"

        if total <= 0:
            line = fit_line(prefix, name, ": [?]", width)
        else:
            pct = current / total
            bar_width = 20
            filled = int(bar_width * pct)
            bar = "#" * filled + "-" * (bar_width - filled)
            pct_display = f"{pct * 100:.0f}"
            suffix = (
                f": [{bar}] {pct_display:>3}%"
                f" {format_size(current)}/{format_size(total)}"
                f" {format_speed(speed)}"
            )
            line = fit_line(prefix, name, suffix, width)

        padded = line + " " * max(0, width - display_width(line))
        sys.stdout.write("\r" + padded)
        sys.stdout.flush()

    def _print_completion(self, name: str, total: int, speed: float) -> None:
        """Print a permanent completion line."""
        prefix = f"  {self._counter_prefix()}"
        bar = "#" * 20
        total_str = format_size(total)
        suffix = f": [{bar}] 100% {total_str}/{total_str} {format_speed(speed)}"
        line = f"{prefix}{name}{suffix}"

        if self._is_tty:
            width = terminal_width() - 1
            line = fit_line(prefix, name, suffix, width)
            # Erase the in-place progress line before printing the final one.
            sys.stdout.write("\r" + " " * width + "\r")
            sys.stdout.flush()

        print(line)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

# Tracks which provider handled the last API call, so top-level errors can be
# labelled "GitLab" without changing GitHub's existing message.
_ACTIVE_PROVIDER = "github"
_USER_CONFIG = None


def _set_active_provider(client: object) -> None:
    """Record the provider of *client* for error-message labelling."""
    global _ACTIVE_PROVIDER
    _ACTIVE_PROVIDER = "gitlab" if isinstance(client, GitLabClient) else "github"


def _handle_download(args: argparse.Namespace) -> int:
    """Execute the ``download`` subcommand.

    Returns:
        0 on success, 1 if some downloads failed.
    """
    provider = getattr(args, "provider", None)
    gitlab_url = getattr(args, "gitlab_url", None)
    github_api_url = getattr(args, "github_api_url", None)

    owner, repo, url_version = parse_full_repo_url(
        args.repo, provider=provider, gitlab_url=gitlab_url
    )
    version = url_version if url_version else args.version
    repo_full = f"{owner}/{repo}"
    patterns = args.patterns if args.patterns is not None else ["*"]

    client = create_client(
        args.repo,
        provider=provider,
        gitlab_url=gitlab_url,
        github_api_url=github_api_url,
        user_config=_USER_CONFIG,
    )
    _set_active_provider(client)
    manager = DownloadManager(client=client)
    tracker = ProgressTracker()

    result: DownloadResult = manager.download_release(
        repo=repo_full,
        pattern=patterns,
        version=version,
        output_dir=args.output,
        flat=args.flat,
        dry_run=args.dry_run,
        no_cache=args.no_cache,
        max_workers=args.concurrent,
        use_regex=args.regex,
        sources=args.sources,
        progress_callback=tracker.update,
        on_total_known=tracker.set_total_assets,
        skip_callback=tracker.skip,
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
    total_skipped = 0

    cli_provider = getattr(args, "provider", None)
    cli_gitlab_url = getattr(args, "gitlab_url", None)
    cli_github_api_url = getattr(args, "github_api_url", None)

    for repo_cfg in config.repos:
        output_dir = args.output or repo_cfg.output or "./downloads"
        repo_full = f"{repo_cfg.owner}/{repo_cfg.repo}"
        label = repo_full

        if args.dry_run:
            print(f"[{label}] Simulating download...")
        else:
            print(f"[{label}] Downloading...")

        provider = repo_cfg.provider or cli_provider
        client = create_client(
            repo_full,
            provider=provider,
            gitlab_url=cli_gitlab_url,
            github_api_url=cli_github_api_url,
            user_config=_USER_CONFIG,
        )
        _set_active_provider(client)
        manager = DownloadManager(client=client)
        tracker = ProgressTracker()

        result: DownloadResult = manager.download_release(
                repo=repo_full,
                pattern=repo_cfg.pattern,
                version=repo_cfg.version,
                output_dir=output_dir,
                flat=args.flat,
                dry_run=args.dry_run,
                max_workers=args.concurrent,
                use_regex=args.regex,
                sources=args.sources if not args.sources else repo_cfg.sources,
                progress_callback=tracker.update,
                on_total_known=tracker.set_total_assets,
                skip_callback=tracker.skip,
            )

        if args.dry_run:
            _print_dry_run_summary(repo_cfg.owner, repo_cfg.repo, result)
        else:
            _print_download_summary(repo_cfg.owner, repo_cfg.repo, result)

        total_downloaded += result.downloaded
        total_failed += result.failed
        total_skipped = total_skipped + result.skipped

    print(f"Summary: {total_downloaded} downloaded, {total_skipped} cached, {total_failed} failed")

    if total_failed > 0:
        return 1
    return 0


def _handle_init(args: argparse.Namespace) -> int:
    """Execute the ``init`` subcommand.

    Returns:
        0 on success, 2 if the target config already exists (and ``--force``
        was not given) or if ``--global`` is combined with an output path.
    """
    if args.user or args.global_config:
        if args.global_config and args.output is not None:
            print(
                "Error: an output path cannot be combined with --global.",
                file=sys.stderr,
            )
            return 2

        try:
            if args.global_config:
                path = create_user_config_example(
                    global_=True, overwrite=args.force
                )
            else:
                path = create_user_config_example(overwrite=args.force)
        except FileExistsError as exc:
            print(f"Config already exists: {exc}", file=sys.stderr)
            print("Use --force to overwrite it.", file=sys.stderr)
            return 2

        print(f"Example user configuration written to {os.path.abspath(path)}")
        print(
            "Supported keys: github_token, gitlab_token, http_proxy, "
            "https_proxy, gitlab_url, github_api_url"
        )
        print("Edit the file and fill in your tokens and proxy settings.")
        return 0

    output = args.output or "gh-dl-config.json"
    create_example_config(output)
    print(f"Example configuration written to {output}")
    return 0


def _handle_list(args: argparse.Namespace) -> int:
    """Execute the ``list`` subcommand.

    Returns:
        0 on success.
    """
    provider = getattr(args, "provider", None)
    gitlab_url = getattr(args, "gitlab_url", None)
    github_api_url = getattr(args, "github_api_url", None)

    owner, repo, url_version = parse_full_repo_url(
        args.repo, provider=provider, gitlab_url=gitlab_url
    )
    version = url_version if url_version else args.version
    repo_full = f"{owner}/{repo}"

    client = create_client(
        args.repo,
        provider=provider,
        gitlab_url=gitlab_url,
        github_api_url=github_api_url,
        user_config=_USER_CONFIG,
    )
    _set_active_provider(client)
    release = client.get_release(repo_full, version)
    assets = client.get_assets(repo_full, release["id"])

    tag_name = release.get("tag_name", version)
    print(f"Assets for {owner}/{repo} ({tag_name}):")

    if not assets and args.sources is False:
        print("  (no assets)")
        return 0

    for asset in assets:
        name = asset["name"]
        size = asset.get("size", 0)
        url = GitHubClient.get_asset_download_url(asset)
        print(f"  {name:50s} {format_size(size):>10s}")
        print(f"  {'→':>2s} {url}")

    if args.sources is not False:
        release_sources = release.get("sources")
        if isinstance(release_sources, list) and release_sources:
            for src in release_sources:
                url = src.get("url")
                if not url:
                    continue
                src_name = src.get("name") or url.rsplit("/", 1)[-1]
                print(f"  {src_name:50s}       (auto)")
                print(f"  {'→':>2s} {url}")
        else:
            for key, label in (("zipball_url", "Source code (zip)"), ("tarball_url", "Source code (tar.gz)")):
                url = release.get(key)
                if url:
                    print(f"  {label:50s}       (auto)")
                    print(f"  {'→':>2s} {url}")

    return 0


# ---------------------------------------------------------------------------
# Pretty-printing helpers
# ---------------------------------------------------------------------------


def _print_download_summary(owner: str, repo: str, result: DownloadResult) -> None:
    """Print a human-readable download summary line."""
    label = f"{owner}/{repo}"
    good = result.downloaded
    skipped = result.skipped
    bad = result.failed
    total = result.total

    if total == 0:
        print(f"  [{label}] No matching assets found")
        return

    parts = []
    if good:
        parts.append(f"{good} downloaded")
    if skipped:
        parts.append(f"{skipped} cached")
    if bad:
        parts.append(f"{bad} failed")
    if not parts:
        parts.append("0 downloaded")

    print(f"  [{label}] {', '.join(parts)}, {total} total")


def _print_dry_run_summary(owner: str, repo: str, result: DownloadResult) -> None:
    """Print a human-readable dry-run summary line."""
    label = f"{owner}/{repo}"
    total = result.total
    print(f"  [{label}] Would download {total} asset(s) (dry run)")


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------

_SUBCOMMANDS = ("download", "config", "init", "list")


def _coerce_implicit_download(argv: list[str]) -> list[str]:
    """Default to the ``download`` subcommand when the first argument is a repo.

    Allows ``gh-dl stedolan/jq`` to be typed instead of
    ``gh-dl download stedolan/jq``. The first argument is treated as an
    implicit repository -- and ``download`` prepended -- only when it
    contains a ``/`` separator (which every ``owner/repo`` string and
    GitHub URL has) and is neither a known subcommand nor an option flag.

    Args:
        argv: Raw argument list (must already be materialised, not None).

    Returns:
        The argument list with ``download`` prepended when applicable.
    """
    if not argv:
        return argv
    first = argv[0]
    if first in _SUBCOMMANDS or first.startswith("-") or "/" not in first:
        return argv
    return ["download", *argv]


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
    global _USER_CONFIG, _ACTIVE_PROVIDER

    # Apply persistent user config before anything else
    user_cfg = load_user_config()
    _USER_CONFIG = user_cfg
    _ACTIVE_PROVIDER = "github"
    if user_cfg.github_token and "GITHUB_TOKEN" not in os.environ:
        os.environ["GITHUB_TOKEN"] = user_cfg.github_token
    if user_cfg.gitlab_token and "GITLAB_TOKEN" not in os.environ:
        os.environ["GITLAB_TOKEN"] = user_cfg.gitlab_token
    if user_cfg.http_proxy and "HTTP_PROXY" not in os.environ:
        os.environ["HTTP_PROXY"] = user_cfg.http_proxy
    if user_cfg.https_proxy and "HTTPS_PROXY" not in os.environ:
        os.environ["HTTPS_PROXY"] = user_cfg.https_proxy

    parser = build_parser()
    # Override sources default from user config (if explicitly set to False)
    if not user_cfg.download_sources:
        parser.set_defaults(sources=False)
    arg_list = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(_coerce_implicit_download(arg_list))

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
        label = "GitLab" if _ACTIVE_PROVIDER == "gitlab" else "GitHub"
        print(f"{label} API error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"File not found: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 2
