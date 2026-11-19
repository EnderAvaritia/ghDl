"""
Download orchestration for gh-downloader.

Manages concurrent downloads of release assets with progress tracking,
resume support, retry logic, glob-based filtering, and smart caching.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
import time
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

import requests

from gh_downloader.api import GitHubError, NetworkError
from gh_downloader.locking import LOCK_FILENAME, DirLock
from gh_downloader.utils import (
    RateLimiter,
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
    matching, and a provider client (GitHub or GitLab) for HTTP interactions.

    Parameters
    ----------
    client:
        An authenticated (or anonymous) release provider client.
    on_lock_wait:
        Optional callback invoked with the lock-file path when another
        ``gh-dl`` process holds the output directory lock.  When omitted a
        message is printed to ``stderr``.
    """

    def __init__(
        self,
        client: Any,
        on_lock_wait: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._client: Any = client
        self._stop_event: threading.Event = threading.Event()
        self._on_lock_wait: Optional[Callable[[str], None]] = on_lock_wait

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
        use_regex: bool = False,
        sources: bool = True,
        on_total_known: Optional[Callable[[int], None]] = None,
        skip_callback: Optional[Callable[[str], None]] = None,
        rate_limiter: RateLimiter | None = None,
    ) -> DownloadResult:
        """Download assets matching *pattern* from a release.

        Parameters
        ----------
        repo:
            ``"owner/name"`` identifying the repository (for GitLab this may
            be a nested ``"group/sub/name"`` path).
        pattern:
            One or more patterns (``fnmatch`` glob or regex syntax).  Assets whose
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
        use_regex:
            If ``True``, interpret *pattern* as regular expressions instead of
            glob patterns.  Matching uses :func:`re.search` (match anywhere in
            the asset name).
        sources:
            If ``True``, also download the source code archives (zip & tar.gz)
            that GitHub provides for the release, in addition to matched assets.
        skip_callback:
            Optional callback invoked with the asset name when an asset is
            skipped because it already exists in the cache.
        rate_limiter:
            Optional shared :class:`RateLimiter` that caps the total
            bandwidth across all download threads.

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
        matched = self.match_assets(assets, patterns, use_regex=use_regex)

        # -- Source code archives (injected after pattern matching) ----------
        if sources:
            release_sources = release.get("sources")
            if isinstance(release_sources, list) and release_sources:
                # GitLab-style normalized sources (zip/tar.gz/tar.bz2/tar).
                for src in release_sources:
                    url = src.get("url")
                    if url:
                        matched.append({
                            "name": src.get("name") or url.rsplit("/", 1)[-1],
                            "size": 0,
                            "browser_download_url": url,
                        })
            else:
                # GitHub-style zipball/tarball archives.
                tag_name = release.get("tag_name", version)
                for key, filename in (
                    ("zipball_url", f"{repo_name}-{tag_name}.zip"),
                    ("tarball_url", f"{repo_name}-{tag_name}.tar.gz"),
                ):
                    url = release.get(key)
                    if url:
                        matched.append({
                            "name": filename,
                            "size": 0,
                            "browser_download_url": url,
                        })

        result.total = len(matched)

        if on_total_known:
            on_total_known(result.total)

        # -- Dry-run: just print & return -----------------------------------
        if dry_run:
            for asset in matched:
                print(f"[dry-run] Would download: {asset['name']}")
            return result

        # -- Signal handler for graceful Ctrl+C shutdown --------------------
        self._stop_event = setup_signal_handler()

        output_dir_abs = os.path.abspath(output_dir)
        ensure_dir(output_dir_abs)

        # Nothing to download: keep the legacy side effect of creating only
        # the output root, without creating the version directory or a lock.
        if not matched:
            return result

        # -- Serialise processes writing the same files ---------------------
        # In hierarchy mode every asset lands under owner/repo/version, so
        # only that version directory needs a lock (different repos or
        # versions stay fully parallel).  Flat mode writes everything into
        # the output root, so the root itself is the lock scope.
        if flat:
            lock_dir = output_dir_abs
        else:
            lock_dir = os.path.join(output_dir_abs, owner, repo_name, version)
        ensure_dir(lock_dir)

        # Acquire the lock *before* any cache check so a waiting process sees
        # the files finished by the process it was waiting for and skips them.
        lock = DirLock(os.path.join(lock_dir, LOCK_FILENAME))
        if self._on_lock_wait is not None:
            on_wait = self._on_lock_wait
        else:
            def on_wait(_lock_path: str) -> None:
                print(
                    f"Another gh-dl instance is downloading into "
                    f"'{lock_dir}'; waiting for it to finish...",
                    file=sys.stderr,
                )

        lock.acquire(on_wait=on_wait)
        try:
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

                    # ---- Smart cache check (done in the main thread) ------
                    asset_size = asset.get("size", 0)
                    if not no_cache and self.check_cache(dest_path, asset_size):
                        result.skipped += 1
                        name = asset["name"]
                        if skip_callback:
                            skip_callback(name)
                        continue

                    # ---- Ensure parent directory exists -------------------
                    ensure_dir(os.path.dirname(dest_path))

                    # ---- Submit download task -----------------------------
                    future = executor.submit(
                        self.download_asset,
                        asset,
                        dest_path,
                        resume=True,
                        progress_callback=progress_callback,
                        rate_limiter=rate_limiter,
                    )
                    futures[future] = asset

                # -- Collect results as they complete ----------------------
                # Use a polling loop instead of as_completed() so Ctrl+C
                # (which only sets stop_event on Windows) is detected promptly.
                while futures:
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

                    # Poll with short timeout so stop_event is checked regularly
                    done, _ = wait(
                        list(futures.keys()),
                        timeout=0.5,
                        return_when=FIRST_COMPLETED,
                    )

                    for future in done:
                        asset = futures.pop(future)
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
        finally:
            lock.release()

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
        rate_limiter: RateLimiter | None = None,
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
        rate_limiter:
            Optional shared :class:`RateLimiter` throttling the transfer.

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
                    expected_size=asset_info.get("size", 0),
                    rate_limiter=rate_limiter,
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
    def match_assets(assets: list[dict[str, Any]], patterns: list[str], use_regex: bool = False) -> list[dict[str, Any]]:
        """Filter a list of asset dicts by patterns (OR logic).

        An asset is included if its ``"name"`` matches **any** of the
        supplied patterns.  Matching is performed via :func:`fnmatch.fnmatch`
        for glob patterns, or :func:`re.search` when *use_regex* is ``True``.

        Parameters
        ----------
        assets:
            Asset dicts returned by the provider's ``get_assets`` method.
        patterns:
            Glob or regex patterns (e.g. ``["*.exe", ".*\\.exe$"]``).
            An empty list or ``["*"]`` returns all assets unchanged.
        use_regex:
            If ``True``, interpret *patterns* as regular expressions using
            :func:`re.search` (match anywhere in the asset name).

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
                if use_regex:
                    if re.search(pattern, name):
                        matched.append(asset)
                        break
                else:
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
        expected_size: int = 0,
        allow_restart: bool = True,
        rate_limiter: RateLimiter | None = None,
    ) -> str:
        """Low-level streaming download with progress and resume.

        Writes to a ``.part`` temporary file and atomically moves it to
        *dest_path* only on success.  When resuming, the server response is
        validated (``Content-Range`` start offset / 206 vs. 200 / 416) before
        any bytes are appended, so a stale or invalid ``.part`` file is never
        merged with a full body.

        When *rate_limiter* is given, every received chunk is passed through
        it so the shared download budget is respected.

        Parameters
        ----------
        url:
            Download URL.
        dest_path:
            Final filesystem path for the downloaded file.
        asset_name:
            Asset name used in messages and progress callbacks.
        resume:
            If ``True``, attempt to resume from an existing ``.part`` file.
        progress_callback:
            Optional callback invoked with
            ``(name, bytes_downloaded, total_bytes, bytes_per_sec)``.
        expected_size:
            Expected final size in bytes (``0`` means unknown).  Used to
            discard an implausible ``.part`` prefix and to verify the
            finished file before it replaces the destination.
        allow_restart:
            Internal flag preventing infinite restart recursion; the second
            attempt disables it.
        rate_limiter:
            Optional shared bandwidth pacer applied to every chunk.

        Returns
        -------
        str
            *dest_path* on success.

        Raises
        ------
        NetworkError
            If the finished file size does not match *expected_size*.
        """
        rewrite = getattr(self._client, "rewrite_download_url", lambda value: value)
        url = rewrite(url)

        part_path = dest_path + ".part"
        resume_bytes = 0

        if resume and os.path.exists(part_path):
            resume_bytes = os.path.getsize(part_path)
            if expected_size > 0 and resume_bytes >= expected_size:
                # The .part cannot be a valid prefix of the expected file.
                try:
                    os.remove(part_path)
                except OSError:
                    pass
                resume_bytes = 0

        headers: dict[str, str] = {}
        mode = "wb"
        if resume_bytes > 0:
            headers["Range"] = f"bytes={resume_bytes}-"
            mode = "ab"

        start_time = time.time()

        # Perform the HTTP request via the client.  Providers expose
        # ``open_download`` so they can scope auth headers safely; fall back
        # to the raw session for plain/custom clients.
        open_download = getattr(self._client, "open_download", None)
        if open_download is not None:
            response = open_download(url, headers)
        else:  # backwards compatibility with plain/custom clients
            response = self._client._session.get(
                url, stream=True, headers=headers
            )

        # -- Validate the server's answer before touching the .part file ----
        range_total: Optional[int] = None
        if resume_bytes > 0:
            status = response.status_code

            def _restart() -> str:
                """Drop the .part file and retry once from scratch."""
                response.close()
                try:
                    os.remove(part_path)
                except OSError:
                    pass
                return self._stream_download(
                    url,
                    dest_path,
                    asset_name,
                    resume=False,
                    progress_callback=progress_callback,
                    expected_size=expected_size,
                    allow_restart=False,
                    rate_limiter=rate_limiter,
                )

            if status == 416 and allow_restart:
                # Requested range is not satisfiable: the prefix is stale.
                return _restart()

            if status == 206:
                content_range = response.headers.get("content-range", "")
                match = re.search(r"bytes\s+(\d+)-", content_range)
                if match is None or int(match.group(1)) != resume_bytes:
                    # Server resumed at the wrong offset -- start over.
                    if allow_restart:
                        return _restart()
                else:
                    total_match = re.search(r"/\s*(\d+)\s*$", content_range)
                    if total_match is not None:
                        range_total = int(total_match.group(1))
            elif status == 200:
                # Server ignored Range and sent the whole body: never append
                # it onto the existing part file.
                resume_bytes = 0
                mode = "wb"

        # Raise here (as before) once resume-specific checks have run.
        response.raise_for_status()

        # Determine total transfer size
        content_length = response.headers.get("content-length")
        total_size = resume_bytes
        if range_total is not None:
            total_size = range_total
        elif content_length:
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

                        # Pace the transfer before reporting progress so the
                        # displayed speed reflects the throttle.
                        if rate_limiter is not None:
                            rate_limiter.consume(len(chunk), self._stop_event)

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

        # -- Verify the finished size before publishing the file -----------
        if expected_size > 0:
            actual_size = os.path.getsize(part_path)
            if actual_size != expected_size:
                try:
                    os.remove(part_path)
                except OSError:
                    pass
                raise NetworkError(
                    f"Size mismatch for {asset_name!r}: expected "
                    f"{expected_size} bytes, got {actual_size}"
                )

        # Atomic on both Windows and POSIX when the target is not held open
        # by another process.
        os.replace(part_path, dest_path)

        return dest_path
