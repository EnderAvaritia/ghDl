"""
Interactive (TUI) mode for gh-downloader.

Provides a simple ``input()``-based prompt-driven interface for ad-hoc
downloads without typing CLI flags.  Uses only the Python standard library
(no ``rich``, ``tqdm``, or ``curses``).
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
from typing import NamedTuple

from gh_downloader.api import GitHubError
from gh_downloader.config import load_user_config
from gh_downloader.downloader import DownloadManager, DownloadResult
from gh_downloader.providers import create_client
from gh_downloader.utils import (
    display_width,
    fit_line,
    format_size,
    format_speed,
    parse_repo_string,
    terminal_width,
)


# ---------------------------------------------------------------------------
# Internal data model
# ---------------------------------------------------------------------------


class _RepoEntry(NamedTuple):
    """A single entry in the interactive download queue."""

    owner: str
    repo: str
    pattern: str
    version: str
    use_regex: bool
    repo_input: str


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def _prompt(prompt_text: str, default: str | None = None) -> str:
    """Read a line from stdin, gracefully handling EOF and KeyboardInterrupt.

    Parameters
    ----------
    prompt_text:
        The text to display (no extra space/newline appended).
    default:
        If provided and the user enters empty input, this value is returned.

    Returns
    -------
    str
        The user's input (stripped) or *default*.
    """
    try:
        value = input(prompt_text).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        if default is not None:
            return default
        raise SystemExit(0)

    if not value and default is not None:
        return default
    return value


def _confirm(prompt_text: str, default: bool = True) -> bool:
    """Ask a yes/no question and return the answer as a bool.

    Parameters
    ----------
    prompt_text:
        The question to display.
    default:
        The default answer (``True`` for y, ``False`` for n).

    Returns
    -------
    bool
    """
    hint = "Y/n" if default else "y/N"
    answer = _prompt(f"{prompt_text} ({hint}) ", default="y" if default else "n")
    return answer.lower().startswith("y")


# ---------------------------------------------------------------------------
# Progress display (thread-safe, tracks total assets and completed count)
# ---------------------------------------------------------------------------


class _ProgressTracker:
    """Callable progress tracker for interactive mode.

    Redraws a single line (``\\r``) while a file is downloading and prints a
    permanent line once it completes, with a ``[completed/total]`` prefix when
    the total number of assets is known.  The line is truncated to the
    terminal width so long asset names cannot wrap and flood the screen.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._finished: set[str] = set()
        self.total_assets = 0
        self._skipped: list[str] = []
        self._shown_first = False
        self._last_print_time = 0.0
        self._min_interval = 0.1  # 100ms throttle between screen writes
        self._is_tty = sys.stdout.isatty()

    def set_total_assets(self, total: int) -> None:
        self.total_assets = total

    def skip(self, name: str) -> None:
        """Record one asset as skipped (cached on disk)."""
        with self._lock:
            self._skipped.append(name)

    def __call__(self, name: str, current: int, total: int, speed: float) -> None:
        with self._lock:
            if current >= total > 0 and name not in self._finished:
                self._finished.add(name)
                self._print_completion(name, total, speed)
                return

            if not self._is_tty:
                # Redirected output: emit only the permanent completion lines.
                return

            if not self._shown_first:
                # The "Processing ..." header already ended with a newline, so
                # start drawing in place on the next row.
                self._shown_first = True
                self._last_print_time = 0.0  # force first progress write

            now = time.time()
            if now - self._last_print_time >= self._min_interval:
                self._last_print_time = now
                self._print_progress(name, current, total, speed)

    def _counter_prefix(self) -> str:
        if self.total_assets > 0:
            done = len(self._finished) + len(self._skipped)
            return f"[{done}/{self.total_assets}] "
        return ""

    def _print_progress(self, name: str, current: int, total: int, speed: float) -> None:
        width = terminal_width() - 1
        prefix = f"  {self._counter_prefix()}"

        if total <= 0:
            suffix = ": [?]"
        else:
            pct = current / total * 100
            suffix = (
                f": {pct:.0f}%"
                f" ({format_size(current)}/{format_size(total)})"
                f" {format_speed(speed)}"
            )

        line = fit_line(prefix, name, suffix, width)
        padded = line + " " * max(0, width - display_width(line))
        sys.stdout.write("\r" + padded)
        sys.stdout.flush()

    def _print_completion(self, name: str, total: int, speed: float) -> None:
        prefix = f"  {self._counter_prefix()}"
        suffix = (
            f": 100% ({format_size(total)}/{format_size(total)})"
            f" {format_speed(speed)}"
        )
        line = f"{prefix}{name}{suffix}"

        if self._is_tty:
            width = terminal_width() - 1
            line = fit_line(prefix, name, suffix, width)
            # Erase the in-place progress line before printing the final one.
            sys.stdout.write("\r" + " " * width + "\r")
            sys.stdout.flush()

        print(line)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_interactive(
    output_dir: str = "./downloads",
    flat: bool = False,
    dry_run: bool = False,
    max_workers: int = 4,
) -> None:
    """Run the interactive download prompt loop.

    Parameters
    ----------
    output_dir:
        Directory to place downloaded assets in.
    flat:
        If ``True``, ignore the ``owner/repo/version`` hierarchy and place
        all assets directly inside *output_dir*.
    dry_run:
        If ``True``, print what would be done without actually downloading.
    max_workers:
        Maximum number of concurrent download threads.
    """
    user_cfg = load_user_config()

    # -- Main application loop (restartable via "Download more?") ----------
    while True:
        _print_welcome()

        queue: list[_RepoEntry] = []

        # -- Collect repos -------------------------------------------------
        while _confirm("Add a repo to download", default=True):
            repo_str = _prompt("  Enter repo (owner/repo or URL): ")
            if not repo_str:
                print("  [skipped] Empty input.")
                continue

            # Parse the repo string; on failure ask again.
            try:
                owner, repo = parse_repo_string(
                    repo_str, gitlab_url=user_cfg.gitlab_url
                )
            except ValueError as exc:
                print(f"  [error] {exc}")
                continue

            pattern = _prompt("  File pattern (glob, e.g. *.exe): ")
            if not pattern:
                print("  [skipped] Pattern cannot be empty.")
                continue

            version = _prompt(
                "  Version (Enter for latest, or tag like v1.0.0): ",
                default="latest",
            )

            use_regex_str = _prompt("  Use regex? (y/N): ", default="n")
            use_regex = use_regex_str.lower().startswith("y")

            queue.append(
                _RepoEntry(owner, repo, pattern, version, use_regex, repo_str)
            )
            # Estimate: we don't know until we call the API, so we just show
            # the raw count.
            mode = "regex" if use_regex else "glob"
            print(
                f"  -> [{owner}/{repo}] pattern={pattern!r} version={version} ({mode})"
            )
            print()

        # -- Bail out early if nothing was queued --------------------------
        if not queue:
            print("No repos queued. Exiting.")
            return

        total_entries = len(queue)
        # We cannot know the asset count without calling the API, so we use
        # "?" to indicate unknown.
        print(
            f"Downloads queued: {total_entries} repo{'s' if total_entries != 1 else ''},"
            + " ? assets expected"
        )

        if not _confirm("Start download", default=True):
            print("Cancelled.")
            return

        print()

        # -- Execute downloads ---------------------------------------------
        overall = DownloadResult(total=0, downloaded=0, skipped=0, failed=0, errors=[])

        for entry in queue:
            label = f"[{entry.owner}/{entry.repo}]"
            print(f"Processing {label} ...")

            try:
                client = create_client(
                    entry.repo_input,
                    gitlab_url=user_cfg.gitlab_url,
                    github_api_url=user_cfg.github_api_url,
                    user_config=user_cfg,
                )
                manager = DownloadManager(client=client)
                tracker = _ProgressTracker()
                result = manager.download_release(
                    repo=f"{entry.owner}/{entry.repo}",
                    pattern=entry.pattern,
                    version=entry.version,
                    output_dir=output_dir,
                    flat=flat,
                    dry_run=dry_run,
                    max_workers=max_workers,
                    use_regex=entry.use_regex,
                    progress_callback=tracker,
                    on_total_known=tracker.set_total_assets,
                    skip_callback=tracker.skip,
                )
            except GitHubError as exc:
                print(f"  {label} API error: {exc}")
                overall = DownloadResult(
                    total=overall.total,
                    downloaded=overall.downloaded,
                    skipped=overall.skipped,
                    failed=overall.failed + 1,
                    errors=overall.errors + [str(exc)],
                )
                continue
            except Exception as exc:
                print(f"  {label} unexpected error: {exc}")
                overall = DownloadResult(
                    total=overall.total,
                    downloaded=overall.downloaded,
                    skipped=overall.skipped,
                    failed=overall.failed + 1,
                    errors=overall.errors + [str(exc)],
                )
                continue

            # Aggregate result counts
            overall = DownloadResult(
                total=overall.total + result.total,
                downloaded=overall.downloaded + result.downloaded,
                skipped=overall.skipped + result.skipped,
                failed=overall.failed + result.failed,
                errors=overall.errors + (result.errors or []),
            )

            print()

        # -- Final summary -------------------------------------------------
        print(
            f"Summary: Downloaded: {overall.downloaded},"
            + f" Skipped: {overall.skipped},"
            + f" Failed: {overall.failed}"
        )

        if not _confirm("Download more", default=False):
            break

    print("Goodbye!")


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


def _print_welcome() -> None:
    """Print the welcome banner and brief usage instructions."""
    width = min(shutil.get_terminal_size().columns, 72)
    line = "=" * width
    print(line)
    print("  gh-dl Interactive Mode")
    print()
    print("  Add repositories one at a time. For each one you will be asked")
    print("  for the repo identifier, a file-name glob pattern, and an")
    print("  optional version/tag.  When you are done, confirm to start")
    print("  downloading.  Press Ctrl+C at any prompt to exit.")
    print(line)
    print()
