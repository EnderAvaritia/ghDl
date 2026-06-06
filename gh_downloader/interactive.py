"""
Interactive (TUI) mode for gh-downloader.

Provides a simple ``input()``-based prompt-driven interface for ad-hoc
downloads without typing CLI flags.  Uses only the Python standard library
(no ``rich``, ``tqdm``, or ``curses``).
"""

from __future__ import annotations

import shutil
from typing import NamedTuple

from gh_downloader.api import GitHubError
from gh_downloader.downloader import DownloadManager, DownloadResult
from gh_downloader.utils import format_size, format_speed, parse_repo_string


# ---------------------------------------------------------------------------
# Internal data model
# ---------------------------------------------------------------------------


class _RepoEntry(NamedTuple):
    """A single entry in the interactive download queue."""

    owner: str
    repo: str
    pattern: str
    version: str


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


def _progress_callback(name: str, current: int, total: int, speed: float) -> None:
    """Print a single progress line for one asset being downloaded.

    Parameters
    ----------
    name:
        Asset filename (e.g. ``foo.exe``).
    current:
        Bytes downloaded so far.
    total:
        Total bytes of the asset.
    speed:
        Transfer rate in bytes/second.
    """
    pct = current / total * 100 if total > 0 else 0.0
    print(
        f"  {name}: {pct:.0f}% ({format_size(current)}/{format_size(total)})"
        + f" {format_speed(speed)}"
    )


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
    manager = DownloadManager(max_workers=max_workers)

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
                owner, repo = parse_repo_string(repo_str)
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

            queue.append(_RepoEntry(owner, repo, pattern, version))
            # Estimate: we don't know until we call the API, so we just show
            # the raw count.
            print(
                f"  -> [{owner}/{repo}] pattern={pattern!r} version={version}"
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
                result = manager.download_release(
                    repo=f"{entry.owner}/{entry.repo}",
                    pattern=entry.pattern,
                    version=entry.version,
                    output_dir=output_dir,
                    flat=flat,
                    dry_run=dry_run,
                    progress_callback=_progress_callback,
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
