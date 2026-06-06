"""
Download orchestration for gh-downloader.

Manages concurrent downloads of release assets with progress tracking,
resume support, retry logic, glob-based filtering, and smart caching.
"""

from __future__ import annotations

import fnmatch
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

import requests

from gh_downloader.api import GitHubClient, GitHubError, NetworkError
from gh_downloader.utils import (
    build_output_path,
    ensure_dir,
    setup_signal_handler,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class DownloadResult:
    """Aggregated result of a ``download_release`` call.

    Attributes
    ----------
    total:
        Number of assets matched by the user-supplied patterns.
    downloaded:
        Number of assets successfully downloaded (or re-downloaded after
        cache invalidation).
    skipped:
        Number of assets that already existed on disk with the expected
        size and were therefore skipped (smart cache).
    failed:
        Number of assets that could not be downloaded after all retries.
    errors:
        Human-readable error messages, one per failed asset.
    """

    total: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Exit-code semantics
    # ------------------------------------------------------------------

    @property
    def partial_failure(self) -> bool:
        """``True`` when at least one asset failed but others succeeded.

        The CLI can use this property to decide on a non-zero exit code
        without aborting mid-way through a batch.
        """
        return self.failed > 0


# ---------------------------------------------------------------------------
# DownloadManager
# ---------------------------------------------------------------------------

class DownloadManager:
    """Coordinates concurrent downloads of GitHub release assets.

    Uses a ``ThreadPoolExecutor`` for concurrency, ``fnmatch`` for glob
    matching, and the ``GitHubClient`` for all HTTP interactions.

    Parameters
    ----------
    client:
        An authenticated (or anonymous) :class:`GitHubClient` instance.
    """

    def __init__(self, client: GitHubClient) -> None:
        self._client: GitHubClient = client
        self._stop_event: threading.Event = threading.Event()

    # -- Public API ---------------------------------------------------------

    def download_release(
        self,
        repo: str,
        pattern: Union[str, list[str]],
        version: str = "latest",
        output_dir: str = ".",
        flat: bool = False,
        dry_run: bool = False,
        no_cache: bool = False,
        max_workers: int = 4,
        progress_callback: Optional[Callable[[str, int, int, float], None]] = None,
    ) -> DownloadResult:
        """Download assets matching *pattern* from a release.

        Parameters
        ----------
        repo:
            ``"owner/name"`` identifying the GitHub repository.
        pattern:
            One or more glob patterns (``fnmatch`` syntax).  Assets whose
            ``name`` matches **any** of the patterns will be downloaded.
        version:
            Release tag name, or ``"latest"`` for the latest published
            release.
        output_dir:
            Root directory under which assets are saved.
        flat:
            If ``True``, save all assets directly under *output_dir*
            instead of the default ``owner/repo/version/`` hierarchy.
        dry_run:
            If ``True``, only print what *would* be downloaded without
            creating any files.
        no_cache:
            If ``True``, bypass the smart-cache check and always download.
        max_workers:
            Maximum number of concurrent download threads.
        progress_callback:
            Optional callback invoked during each asset download with the
            signature ``(name: str, current: int, total: int, speed: float)``.

        Returns
        -------
        DownloadResult
        """
        result = DownloadResult()

        # -- Normalise pattern(s) to a list ---------------------------------
        patterns: list[str] = [pattern] if isinstance(pattern, str) else pattern

        # -- Resolve repo into owner / name parts ---------------------------
        if "/" in repo:
            owner, repo_name = repo.split("/", 1)
        else:
            owner, repo_name = repo, repo

        # -- Fetch release metadata -----------------------------------------
        try:
            release = self._client.get_release(repo, version)
        except GitHubError as exc:
            result.failed = 1
            result.total = 1
            result.errors.append(str(exc))
            return result

        assets = self._client.get_assets(repo, release["id"])

        # -- Glob matching --------------------------------------------------
        matched = self.match_assets(assets, patterns)
        result.total = len(matched)

        if not matched:
            return result

        # -- Dry-run: just print & return -----------------------------------
        if dry_run:
            for asset in matched:
                print(f"[dry-run] Would download: {asset['name']}")
            return result

        # -- Signal handler for graceful Ctrl+C shutdown --------------------
        self._stop_event = setup_signal_handler()

        output_dir_abs = os.path.abspath(output_dir)
        ensure_dir(output_dir_abs)

        from concurrent.futures import Future

        futures: dict[Future[Any], dict[str, Any]] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for asset in matched:
                if self._stop_event.is_set():
                    break

                dest_path = build_output_path(
                    output_dir_abs,
                    owner,
                    repo_name,
                    version,
                    asset["name"],
                    flat=flat,
                )

                # ---- Smart cache check (done in the main thread) ----------
                asset_size = asset.get("size", 0)
                if not no_cache and self.check_cache(dest_path, asset_size):
                    result.skipped += 1
                    continue

                # ---- Ensure parent directory exists -----------------------
                ensure_dir(os.path.dirname(dest_path))

                # ---- Submit download task ---------------------------------
                future = executor.submit(
                    self.download_asset,
                    asset,
                    dest_path,
                    resume=True,
                    progress_callback=progress_callback,
                )
                futures[future] = asset

            # -- Collect results as they complete ---------------------------
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    # Mark remaining futures as cancelled
                    for f in futures:
                        f.cancel()
                    # Remove incomplete .part files for cancelled futures
                    for f, asset in futures.items():
                        if not f.done():
                            part_path = build_output_path(
                                output_dir_abs,
                                owner,
                                repo_name,
                                version,
                                asset["name"],
                                flat=flat,
                            ) + ".part"
                            if os.path.exists(part_path):
                                try:
                                    os.remove(part_path)
                                except OSError:
                                    pass
                    break

                asset = futures[future]
                asset_name = asset["name"]
                try:
                    future.result()
                    result.downloaded += 1
                except KeyboardInterrupt:
                    # Re-raise immediately – no point continuing
                    raise
                except Exception as exc:
                    result.failed += 1
                    result.errors.append(f"{asset_name}: {exc}")

        return result

    # ------------------------------------------------------------------
    # Single-asset download (public, usable standalone)
    # ------------------------------------------------------------------

    def download_asset(
        self,
        asset_info: dict[str, Any],
        dest_path: str,
        resume: bool = False,
        progress_callback: Optional[Callable[[str, int, int, float], None]] = None,
    ) -> str:
        """Download a single release asset to *dest_path*.

        Implements resume via a ``.part`` temporary file, exponential
        back-off retry (up to 3 retries), and streaming progress
        reporting.

        Parameters
        ----------
        asset_info:
            Asset dictionary as returned by the GitHub API (must contain
            at least ``"name"``, ``"size"`` and the download URL).
        dest_path:
            Final filesystem path for the downloaded file.
        resume:
            If ``True``, check for an existing ``.part`` file and attempt
            to resume the download via the ``Range`` HTTP header.
        progress_callback:
            Optional callback invoked with
            ``(name, bytes_downloaded, total_bytes, bytes_per_sec)``.

        Returns
        -------
        str
            *dest_path* on success.

        Raises
        ------
        NetworkError
            When all retries are exhausted.
        KeyboardInterrupt
            Propagated if the user pressed Ctrl+C during the download
            (the incomplete ``.part`` file is cleaned up by the caller).
        """
        asset_name = asset_info["name"]
        url = self._client.get_asset_download_url(asset_info)

        retry_delays = [1, 3, 9]
        last_exc: Optional[Exception] = None

        for attempt in range(1 + len(retry_delays)):  # initial + 3 retries
            if self._stop_event.is_set():
                raise KeyboardInterrupt("Download cancelled by user")

            try:
                return self._stream_download(
                    url=url,
                    dest_path=dest_path,
                    asset_name=asset_name,
                    resume=resume,
                    progress_callback=progress_callback,
                )
            except (requests.RequestException, OSError, GitHubError) as exc:
                last_exc = exc
                if attempt < len(retry_delays):
                    delay = retry_delays[attempt]
                    if progress_callback:
                        progress_callback(
                            asset_name, 0, asset_info.get("size", 0), 0
                        )
                    time.sleep(delay)
                    continue
                # Exhausted retries – clean up .part and raise
                part_path = dest_path + ".part"
                if os.path.exists(part_path):
                    try:
                        os.remove(part_path)
                    except OSError:
                        pass
                raise NetworkError(
                    f"Failed to download {asset_name!r} after "
                    f"{1 + len(retry_delays)} attempts: {last_exc}"
                ) from last_exc

        # Should never be reached, but keep the type-checker happy.
        raise RuntimeError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------
    # Glob matching
    # ------------------------------------------------------------------

    @staticmethod
    def match_assets(assets: list[dict[str, Any]], patterns: list[str]) -> list[dict[str, Any]]:
        """Filter a list of asset dicts by glob patterns (OR logic).

        An asset is included if its ``"name"`` matches **any** of the
        supplied patterns.  Matching is performed via :func:`fnmatch.fnmatch`.

        Parameters
        ----------
        assets:
            Asset dicts returned by :meth:`GitHubClient.get_assets`.
        patterns:
            Glob patterns (e.g. ``["*.exe", "*.dmg"]``).  An empty list
            or ``["*"]`` returns all assets unchanged.

        Returns
        -------
        list[dict]
            Subset of *assets* that matched at least one pattern.
        """
        if not patterns or patterns == ["*"]:
            return list(assets)

        matched: list[dict[str, Any]] = []
        for asset in assets:
            name = asset.get("name", "")
            for pattern in patterns:
                if fnmatch.fnmatch(name, pattern):
                    matched.append(asset)
                    break
        return matched

    # ------------------------------------------------------------------
    # Smart cache check
    # ------------------------------------------------------------------

    @staticmethod
    def check_cache(dest_path: str, expected_size: int) -> bool:
        """Return ``True`` if *dest_path* exists and has the expected size.

        This is the **smart cache** check: if the file already exists on
        disk and its size matches the asset size reported by the GitHub
        API, the download can be skipped.

        Parameters
        ----------
        dest_path:
            The final output path for an asset.
        expected_size:
            Asset size in bytes as reported by the GitHub API.  If zero
            or negative, the check falls back to ``os.path.exists`` only.

        Returns
        -------
        bool
        """
        if not os.path.exists(dest_path):
            return False
        if expected_size > 0:
            return os.path.getsize(dest_path) == expected_size
        return True

    # ==================================================================
    # Internal helpers
    # ==================================================================

    def _stream_download(
        self,
        url: str,
        dest_path: str,
        asset_name: str,
        resume: bool = False,
        progress_callback: Optional[Callable[[str, int, int, float], None]] = None,
    ) -> str:
        """Low-level streaming download with progress and resume.

        Writes to a ``.part`` temporary file and renames it to
        *dest_path* only on success.  Handles the ``Range`` header for
        resume when a ``.part`` file already exists.
        """
        part_path = dest_path + ".part"
        resume_bytes = 0

        if resume and os.path.exists(part_path):
            resume_bytes = os.path.getsize(part_path)

        headers: dict[str, str] = {}
        mode = "wb"
        if resume_bytes > 0:
            headers["Range"] = f"bytes={resume_bytes}-"
            mode = "ab"

        start_time = time.time()

        # Perform the HTTP request via the client's session (reuses auth &
        # connection pooling).
        response = self._client._session.get(url, stream=True, headers=headers)
        response.raise_for_status()

        # Determine total transfer size
        content_length = response.headers.get("content-length")
        total_size = resume_bytes
        if content_length:
            total_size = resume_bytes + int(content_length)

        downloaded = resume_bytes

        try:
            with open(part_path, mode) as f:
                for chunk in response.iter_content(chunk_size=8192):
                    # ---- Graceful Ctrl+C check ----
                    if self._stop_event.is_set():
                        # Remove incomplete .part
                        f.close()
                        if os.path.exists(part_path):
                            os.remove(part_path)
                        raise KeyboardInterrupt(
                            f"Download of {asset_name!r} cancelled"
                        )

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_callback:
                            elapsed = time.time() - start_time
                            speed = downloaded / elapsed if elapsed > 0 else 0.0
                            progress_callback(
                                asset_name, downloaded, total_size or downloaded, speed
                            )

        except (KeyboardInterrupt, Exception):
            # Clean up incomplete .part on any failure
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            raise

        # Atomically replace the final destination
        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.rename(part_path, dest_path)

        return dest_path
