"""
Tests for the download orchestration module (``gh_downloader.downloader``).
"""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from gh_downloader.api import GitHubClient, NetworkError
from gh_downloader.downloader import DownloadManager, DownloadResult
from gh_downloader.locking import LOCK_FILENAME, DirLock


# -- DownloadResult ----------------------------------------------------------


class TestDownloadResult:
    def test_defaults(self):
        r = DownloadResult()
        assert r.total == 0
        assert r.downloaded == 0
        assert r.skipped == 0
        assert r.failed == 0
        assert r.errors == []
        assert r.partial_failure is False

    def test_partial_failure_true(self):
        r = DownloadResult(total=2, downloaded=1, failed=1)
        assert r.partial_failure is True

    def test_partial_failure_false(self):
        r = DownloadResult(total=2, downloaded=2)
        assert r.partial_failure is False


# -- DownloadManager.match_assets --------------------------------------------


class TestMatchAssets:
    def test_empty_patterns_returns_all(self, sample_assets_response):
        result = DownloadManager.match_assets(sample_assets_response, [])
        assert len(result) == 3

    def test_wildcard_returns_all(self, sample_assets_response):
        result = DownloadManager.match_assets(sample_assets_response, ["*"])
        assert len(result) == 3

    def test_single_pattern(self, sample_assets_response):
        result = DownloadManager.match_assets(sample_assets_response, ["*.exe"])
        assert len(result) == 1
        assert result[0]["name"] == "jq-win64.exe"

    def test_multiple_patterns(self, sample_assets_response):
        result = DownloadManager.match_assets(
            sample_assets_response, ["*.exe", "*-linux*"]
        )
        assert len(result) == 2
        names = {a["name"] for a in result}
        assert names == {"jq-win64.exe", "jq-linux64"}

    def test_no_match_returns_empty(self, sample_assets_response):
        result = DownloadManager.match_assets(sample_assets_response, ["*.dmg"])
        assert result == []


# -- DownloadManager.check_cache ---------------------------------------------


class TestCheckCache:
    def test_file_exists_and_size_matches(self, tmp_path):
        f = tmp_path / "asset.zip"
        f.write_text("x" * 100)
        assert DownloadManager.check_cache(str(f), 100) is True

    def test_file_exists_size_mismatch(self, tmp_path):
        f = tmp_path / "asset.zip"
        f.write_text("x" * 100)
        assert DownloadManager.check_cache(str(f), 200) is False

    def test_file_not_exists(self, tmp_path):
        p = str(tmp_path / "nonexistent.zip")
        assert DownloadManager.check_cache(p, 100) is False

    def test_file_exists_zero_size_check(self, tmp_path):
        """When expected_size is 0, only existence is checked."""
        f = tmp_path / "asset.zip"
        f.write_text("x" * 100)
        assert DownloadManager.check_cache(str(f), 0) is True


# -- DownloadManager instantiation -------------------------------------------


class TestDownloadManagerInit:
    def test_requires_client(self):
        client = GitHubClient(token="test")
        mgr = DownloadManager(client)
        assert mgr is not None


# -- DownloadManager.download_release (dry run) ------------------------------


class TestDownloadReleaseDryRun:
    def test_dry_run_does_not_create_files(
        self, mock_api_client, sample_release_response,
        sample_assets_response, temp_output_dir,
    ):
        """Verify dry_run=True produces no files on disk."""
        repo = "stedolan/jq"

        release_url = (
            f"https://api.github.com/repos/{repo}/releases/latest"
        )
        assets_url = (
            f"https://api.github.com/repos/{repo}/releases/123/assets"
        )
        mock_api_client._mock_registry[release_url] = (
            200, sample_release_response
        )
        mock_api_client._mock_registry[assets_url] = (
            200, sample_assets_response
        )

        mgr = DownloadManager(mock_api_client)

        before = set(os.listdir(temp_output_dir))
        result = mgr.download_release(
            repo=repo,
            pattern=["*"],
            output_dir=temp_output_dir,
            dry_run=True,
            sources=False,
        )
        after = set(os.listdir(temp_output_dir))

        assert result.downloaded == 0
        assert result.total == 3
        assert before == after  # no files created

    def test_dry_run_default_includes_sources(
        self, mock_api_client, sample_release_response,
        sample_assets_response, temp_output_dir,
    ):
        """Verify sources=True is the default."""
        repo = "stedolan/jq"

        release_url = (
            f"https://api.github.com/repos/{repo}/releases/latest"
        )
        assets_url = (
            f"https://api.github.com/repos/{repo}/releases/123/assets"
        )
        mock_api_client._mock_registry[release_url] = (
            200, sample_release_response
        )
        mock_api_client._mock_registry[assets_url] = (
            200, sample_assets_response
        )

        mgr = DownloadManager(mock_api_client)

        result = mgr.download_release(
            repo=repo,
            pattern=["*"],
            output_dir=temp_output_dir,
            dry_run=True,
            # Not passing sources= — should default to True
        )

        assert result.total == 5  # 3 assets + 2 source archives

    def test_dry_run_with_sources_includes_source_archives(
        self, mock_api_client, sample_release_response,
        sample_assets_response, temp_output_dir,
    ):
        """Verify sources=True adds zipball and tarball to the matched list."""
        repo = "stedolan/jq"

        release_url = (
            f"https://api.github.com/repos/{repo}/releases/latest"
        )
        assets_url = (
            f"https://api.github.com/repos/{repo}/releases/123/assets"
        )
        mock_api_client._mock_registry[release_url] = (
            200, sample_release_response
        )
        mock_api_client._mock_registry[assets_url] = (
            200, sample_assets_response
        )

        mgr = DownloadManager(mock_api_client)

        result = mgr.download_release(
            repo=repo,
            pattern=["*"],
            output_dir=temp_output_dir,
            dry_run=True,
            sources=True,
        )

        # 3 original assets + 2 source archives
        assert result.total == 5
        assert result.downloaded == 0


# ---------------------------------------------------------------------------
# Fake HTTP client / response helpers for resume + lock tests
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal ``requests.Response`` stand-in for streaming tests."""

    def __init__(self, status_code: int = 200, headers=None, body: bytes = b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size: int = 8192):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start:start + chunk_size]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self) -> None:
        self.closed = True


class FakeClient:
    """Queued-response fake client implementing the provider download API."""

    def __init__(self, responses, assets=None, release=None):
        self._responses = list(responses)
        self._assets = assets or []
        self._release = release or {"id": 1, "tag_name": "v1"}
        self.headers_seen: list[dict[str, str]] = []

    def get_release(self, repo, version):
        return self._release

    def get_assets(self, repo, release_id):
        return list(self._assets)

    @staticmethod
    def get_asset_download_url(asset):
        return asset["browser_download_url"]

    def open_download(self, url, headers=None):
        self.headers_seen.append(dict(headers or {}))
        return self._responses.pop(0)


# -- Resume validation / atomic finalize ------------------------------------


class TestStreamDownloadResume:
    def test_range_ignored_restarts_from_scratch(self, tmp_path):
        body = b"ABCDEFGH"
        part = tmp_path / "asset.bin.part"
        part.write_bytes(b"AAAA")
        client = FakeClient([
            FakeResponse(200, {"content-length": str(len(body))}, body)
        ])
        mgr = DownloadManager(client)
        dest = str(tmp_path / "asset.bin")

        result = mgr._stream_download(
            "https://example.test/asset.bin",
            dest,
            "asset.bin",
            resume=True,
            expected_size=len(body),
        )

        assert result == dest
        assert (tmp_path / "asset.bin").read_bytes() == body
        assert client.headers_seen[0]["Range"] == "bytes=4-"
        assert not part.exists()

    def test_206_correct_offset_appends(self, tmp_path):
        body = b"ABCDEFGH"
        part = tmp_path / "asset.bin.part"
        part.write_bytes(b"ABCD")
        client = FakeClient([
            FakeResponse(
                206,
                {"content-range": "bytes 4-7/8", "content-length": "4"},
                b"EFGH",
            )
        ])
        mgr = DownloadManager(client)
        dest = str(tmp_path / "asset.bin")

        mgr._stream_download(
            "https://example.test/asset.bin",
            dest,
            "asset.bin",
            resume=True,
            expected_size=len(body),
        )

        assert (tmp_path / "asset.bin").read_bytes() == body
        assert not part.exists()

    def test_206_wrong_start_restarts(self, tmp_path):
        body = b"ABCDEFGH"
        part = tmp_path / "asset.bin.part"
        part.write_bytes(b"WXYZ")
        client = FakeClient([
            FakeResponse(
                206,
                {"content-range": "bytes 0-7/8", "content-length": "8"},
                body,
            ),
            FakeResponse(200, {"content-length": "8"}, body),
        ])
        mgr = DownloadManager(client)
        dest = str(tmp_path / "asset.bin")

        mgr._stream_download(
            "https://example.test/asset.bin",
            dest,
            "asset.bin",
            resume=True,
            expected_size=len(body),
        )

        assert (tmp_path / "asset.bin").read_bytes() == body
        assert not part.exists()

    def test_416_restarts(self, tmp_path):
        body = b"ABCDEFGH"
        part = tmp_path / "asset.bin.part"
        part.write_bytes(b"WXYZ")
        client = FakeClient([
            FakeResponse(416),
            FakeResponse(200, {"content-length": "8"}, body),
        ])
        mgr = DownloadManager(client)
        dest = str(tmp_path / "asset.bin")

        mgr._stream_download(
            "https://example.test/asset.bin",
            dest,
            "asset.bin",
            resume=True,
            expected_size=len(body),
        )

        assert (tmp_path / "asset.bin").read_bytes() == body
        assert not part.exists()

    def test_existing_destination_is_replaced(self, tmp_path):
        body = b"NEW-CONTENT"
        dest = tmp_path / "asset.bin"
        dest.write_bytes(b"old content that must disappear")
        client = FakeClient([
            FakeResponse(200, {"content-length": str(len(body))}, body)
        ])
        mgr = DownloadManager(client)

        mgr._stream_download(
            "https://example.test/asset.bin",
            str(dest),
            "asset.bin",
            resume=False,
            expected_size=len(body),
        )

        assert dest.read_bytes() == body

    def test_size_mismatch_raises_after_retries(self, tmp_path):
        # Body is shorter than the size advertised by the API.
        asset = {
            "name": "asset.bin",
            "size": 8,
            "browser_download_url": "https://example.test/asset.bin",
        }
        responses = [
            FakeResponse(200, {"content-length": "4"}, b"ABCD")
            for _ in range(4)  # initial attempt + 3 retries
        ]
        client = FakeClient(responses)
        mgr = DownloadManager(client)
        dest = str(tmp_path / "asset.bin")

        with patch("gh_downloader.downloader.time.sleep", return_value=None):
            with pytest.raises(NetworkError):
                mgr.download_asset(asset, dest, resume=True)

        assert not os.path.exists(dest)
        assert not os.path.exists(dest + ".part")


# -- download_release locking integration -----------------------------------


class TestDownloadReleaseLocking:
    def _asset(self, body_len: int = 4) -> dict:
        return {
            "name": "asset.bin",
            "size": body_len,
            "browser_download_url": "https://example.test/asset.bin",
        }

    def test_release_creates_lock_file_and_asset(self, tmp_path):
        body = b"DATA"
        asset = self._asset(len(body))
        client = FakeClient(
            [FakeResponse(200, {"content-length": str(len(body))}, body)],
            assets=[asset],
        )
        mgr = DownloadManager(client)

        result = mgr.download_release(
            repo="owner/repo",
            pattern="*",
            output_dir=str(tmp_path),
            flat=True,
            sources=False,
            max_workers=1,
        )

        assert result.downloaded == 1
        assert result.failed == 0
        assert (tmp_path / LOCK_FILENAME).exists()
        assert (tmp_path / "asset.bin").read_bytes() == body

    def test_dry_run_creates_neither_lock_nor_asset(self, tmp_path):
        asset = self._asset(4)
        client = FakeClient([], assets=[asset])
        mgr = DownloadManager(client)

        result = mgr.download_release(
            repo="owner/repo",
            pattern="*",
            output_dir=str(tmp_path),
            flat=True,
            sources=False,
            dry_run=True,
        )

        assert result.total == 1
        assert not (tmp_path / LOCK_FILENAME).exists()
        assert not (tmp_path / "asset.bin").exists()

    def test_download_release_waits_for_external_lock(self, tmp_path):
        body = b"DATA"
        asset = self._asset(len(body))
        client = FakeClient(
            [FakeResponse(200, {"content-length": str(len(body))}, body)],
            assets=[asset],
        )

        held = DirLock(str(tmp_path / LOCK_FILENAME))
        held.acquire()

        called = threading.Event()
        wait_paths: list[str] = []

        def on_lock_wait(path: str) -> None:
            wait_paths.append(path)
            called.set()

        mgr = DownloadManager(client, on_lock_wait=on_lock_wait)
        results: list[DownloadResult] = []

        def run() -> None:
            results.append(
                mgr.download_release(
                    repo="owner/repo",
                    pattern="*",
                    output_dir=str(tmp_path),
                    flat=True,
                    sources=False,
                    max_workers=1,
                )
            )

        # ``setup_signal_handler`` only works on the main thread.
        with patch(
            "gh_downloader.downloader.setup_signal_handler",
            return_value=threading.Event(),
        ):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                assert called.wait(5), "on_lock_wait was not invoked"
                assert wait_paths == [str(tmp_path / LOCK_FILENAME)]
            finally:
                held.release()
            thread.join(10)

        assert not thread.is_alive()
        assert results and results[0].downloaded == 1
        assert (tmp_path / "asset.bin").read_bytes() == body

    def test_non_flat_locks_version_directory(self, tmp_path):
        body = b"DATA"
        asset = self._asset(len(body))
        client = FakeClient(
            [FakeResponse(200, {"content-length": str(len(body))}, body)],
            assets=[asset],
        )
        mgr = DownloadManager(client)

        result = mgr.download_release(
            repo="owner/repo",
            pattern="*",
            output_dir=str(tmp_path),
            sources=False,
            max_workers=1,
        )

        version_dir = tmp_path / "owner" / "repo" / "latest"
        assert result.downloaded == 1
        assert (version_dir / LOCK_FILENAME).exists()
        assert (version_dir / "asset.bin").read_bytes() == body
        assert not (tmp_path / LOCK_FILENAME).exists()

    def test_different_repos_do_not_block(self, tmp_path):
        body = b"DATA"
        asset = self._asset(len(body))
        client = FakeClient(
            [FakeResponse(200, {"content-length": str(len(body))}, body)],
            assets=[asset],
        )

        # Hold the lock for a different repository's version directory.
        other_dir = tmp_path / "other" / "repo" / "latest"
        other_dir.mkdir(parents=True)
        held = DirLock(str(other_dir / LOCK_FILENAME))
        held.acquire()

        called = threading.Event()
        wait_paths: list[str] = []
        mgr = DownloadManager(
            client,
            on_lock_wait=lambda path: (wait_paths.append(path), called.set()),
        )

        try:
            result = mgr.download_release(
                repo="owner/repo",
                pattern="*",
                output_dir=str(tmp_path),
                sources=False,
                max_workers=1,
            )
        finally:
            held.release()

        assert result.downloaded == 1
        assert not called.is_set()
        assert wait_paths == []
        assert (tmp_path / "owner" / "repo" / "latest" / "asset.bin").read_bytes() == body

    def test_same_repo_version_non_flat_waits(self, tmp_path):
        body = b"DATA"
        asset = self._asset(len(body))
        client = FakeClient(
            [FakeResponse(200, {"content-length": str(len(body))}, body)],
            assets=[asset],
        )

        version_dir = tmp_path / "owner" / "repo" / "latest"
        version_dir.mkdir(parents=True)
        held = DirLock(str(version_dir / LOCK_FILENAME))
        held.acquire()

        called = threading.Event()
        wait_paths: list[str] = []
        mgr = DownloadManager(
            client,
            on_lock_wait=lambda path: (wait_paths.append(path), called.set()),
        )
        results: list[DownloadResult] = []

        def run() -> None:
            results.append(
                mgr.download_release(
                    repo="owner/repo",
                    pattern="*",
                    output_dir=str(tmp_path),
                    sources=False,
                    max_workers=1,
                )
            )

        with patch(
            "gh_downloader.downloader.setup_signal_handler",
            return_value=threading.Event(),
        ):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                assert called.wait(5), "on_lock_wait was not invoked"
                assert wait_paths == [str(version_dir / LOCK_FILENAME)]
            finally:
                held.release()
            thread.join(10)

        assert not thread.is_alive()
        assert results and results[0].downloaded == 1
        assert (version_dir / "asset.bin").read_bytes() == body

    def test_zero_matched_assets_creates_no_version_dir_or_lock(self, tmp_path):
        asset = self._asset(4)
        client = FakeClient([], assets=[asset])
        mgr = DownloadManager(client)

        result = mgr.download_release(
            repo="owner/repo",
            pattern="*.does-not-match",
            output_dir=str(tmp_path),
            sources=False,
        )

        assert result.total == 0
        assert result.downloaded == 0
        assert not (tmp_path / "owner").exists()
        assert not (tmp_path / LOCK_FILENAME).exists()
