"""
Tests for the download orchestration module (``gh_downloader.downloader``).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from gh_downloader.api import GitHubClient
from gh_downloader.downloader import DownloadManager, DownloadResult


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
        )
        after = set(os.listdir(temp_output_dir))

        assert result.downloaded == 0
        assert result.total == 3
        assert before == after  # no files created
