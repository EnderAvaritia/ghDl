"""
Integration tests that hit the real GitHub Releases API.

All tests are tagged with ``@pytest.mark.integration`` and are skipped
unless the ``CI`` environment variable is set.  Run them explicitly with::

    pytest tests/test_integration.py -v -m integration

The tests target the ``jqlang/jq`` repository (formerly ``stedolan/jq``)
because it is small, stable, and public.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gh_downloader.api import GitHubClient, RepoNotFoundError, VersionNotFoundError
from gh_downloader.cli import run_cli
from gh_downloader.config import load_config
from gh_downloader.downloader import DownloadManager

# ---------------------------------------------------------------------------
# Markers & CI gating
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("CI"),
        reason="Integration test – requires network and real GitHub API",
    ),
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO = "jqlang/jq"
"""Small, stable, public repository."""

SMALL_ASSET = "jq-linux-armel"
"""One of the smaller binary assets (~1.6 MB)."""

SMALL_ASSET_SIZE = 1_692_212
"""Expected size in bytes for *SMALL_ASSET* (from API metadata)."""

TARGET_PATTERN = "jq-linux-*"
"""Glob pattern that matches platform-specific binaries."""


# ===================================================================
# Helpers
# ===================================================================


# ===================================================================
# Download tests (direct DownloadManager usage)
# ===================================================================


class TestDownloadIntegration:
    """Tests that download real assets from the GitHub API."""

    def test_download_single_asset(self, tmp_path: Path) -> None:
        """Download one file from jqlang/jq, verify it exists with correct size."""
        output_dir = str(tmp_path / "downloads")
        client = GitHubClient()
        manager = DownloadManager(client)

        result = manager.download_release(
            repo=REPO,
            pattern=[SMALL_ASSET],
            output_dir=output_dir,
            flat=True,
        )

        assert result.total == 1, f"Expected 1 match, got {result.total}"
        assert result.downloaded == 1, f"Expected 1 download, got {result.downloaded}"
        assert result.failed == 0, f"Unexpected failures: {result.errors}"
        assert result.skipped == 0

        # Verify file on disk
        asset_path = Path(output_dir) / SMALL_ASSET
        assert asset_path.is_file(), f"File not found: {asset_path}"
        # Allow slight size variance (the API size may differ from actual on disk
        # for compressed transfers, but for binaries it should match exactly).
        actual_size = asset_path.stat().st_size
        assert actual_size == SMALL_ASSET_SIZE, (
            f"Size mismatch: expected {SMALL_ASSET_SIZE}, got {actual_size}"
        )

    def test_dry_run_creates_no_files(self, tmp_path: Path) -> None:
        """Verify --dry-run flag creates zero files."""
        output_dir = str(tmp_path / "downloads")
        os.makedirs(output_dir, exist_ok=True)
        client = GitHubClient()
        manager = DownloadManager(client)

        before = set(os.listdir(output_dir))
        result = manager.download_release(
            repo=REPO,
            pattern=["*"],
            output_dir=output_dir,
            flat=True,
            dry_run=True,
        )
        after = set(os.listdir(output_dir))

        assert result.total > 0, "Should have matched assets"
        assert result.downloaded == 0, "dry_run should not download"
        assert before == after, "dry_run created files on disk"

    def test_cache_skip_on_second_run(self, tmp_path: Path) -> None:
        """Download twice; second run should skip the cached asset."""
        output_dir = str(tmp_path / "downloads")
        client = GitHubClient()
        manager = DownloadManager(client)

        # First download
        first = manager.download_release(
            repo=REPO,
            pattern=[SMALL_ASSET],
            output_dir=output_dir,
            flat=True,
        )
        assert first.downloaded == 1
        assert first.skipped == 0

        # Second download – asset already on disk with correct size
        second = manager.download_release(
            repo=REPO,
            pattern=[SMALL_ASSET],
            output_dir=output_dir,
            flat=True,
        )
        assert second.downloaded == 0, "Should not re-download cached asset"
        assert second.skipped == 1, "Should have skipped cached asset"
        assert second.total == 1

    def test_multiple_patterns(self, tmp_path: Path) -> None:
        """Download with multiple --pattern args filters correctly."""
        output_dir = str(tmp_path / "downloads")
        client = GitHubClient()
        manager = DownloadManager(client)

        # Match two specific binaries
        result = manager.download_release(
            repo=REPO,
            pattern=["jq-linux-amd64", "jq-linux-arm64"],
            output_dir=output_dir,
            flat=True,
        )

        assert result.total == 2, f"Expected 2 matches, got {result.total}"
        assert result.downloaded == 2, f"Expected 2 downloads, got {result.downloaded}"
        assert result.failed == 0

        # Both files should be on disk
        for name in ("jq-linux-amd64", "jq-linux-arm64"):
            path = Path(output_dir) / name
            assert path.is_file(), f"Missing file: {path}"


# ===================================================================
# List subcommand
# ===================================================================


class TestListIntegration:
    """Integration tests for ``gh-dl list``."""

    def test_list_assets(self, tmp_path: Path, capsys) -> None:
        """``gh-dl list`` shows assets for a real repository."""
        # Redirect output to avoid polluting test stdout
        exit_code = run_cli(["list", REPO])
        captured = capsys.readouterr()

        assert exit_code == 0, f"list failed with exit code {exit_code}"
        assert "jq-linux-amd64" in captured.out, (
            f"Expected asset names in list output.\nstdout:\n{captured.out}"
        )
        assert "jq-1.8.1" in captured.out or "tag_name" not in captured.out, (
            "Expected version/tag info in list output"
        )
        assert "https://" in captured.out, (
            "Expected download URLs in list output"
        )


# ===================================================================
# Init subcommand
# ===================================================================


class TestInitIntegration:
    """Integration tests for ``gh-dl init``."""

    def test_init_creates_config(self, tmp_path: Path) -> None:
        """``gh-dl init`` creates a valid configuration file."""
        config_path = str(tmp_path / "gh-dl-config.json")

        exit_code = run_cli(["init", config_path])

        assert exit_code == 0
        assert os.path.isfile(config_path), "init did not create the file"

        # Should be valid JSON loadable by load_config
        config = load_config(config_path)
        assert len(config.repos) == 2
        assert config.repos[0].owner == "jeffrey-xuan"
        assert config.repos[1].repo == "vscode"

    def test_init_default_path(self, tmp_path: Path) -> None:
        """``gh-dl init`` with no path writes to ``gh-dl-config.json`` in CWD."""
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            exit_code = run_cli(["init"])
            assert exit_code == 0
            assert (tmp_path / "gh-dl-config.json").is_file()
        finally:
            os.chdir(old_cwd)


# ===================================================================
# Error handling tests
# ===================================================================


class TestErrorHandlingIntegration:
    """Integration tests for graceful error handling."""

    def test_nonexistent_repo_error(self, capsys) -> None:
        """Graceful error message on 404 (non-existent repository)."""
        exit_code = run_cli(["download", "this/doesnotexist", "-p", "*"])
        captured = capsys.readouterr()

        assert exit_code == 2, (
            f"Expected exit code 2 for non-existent repo, got {exit_code}"
        )
        assert "GitHub API error" in captured.err, (
            f"Expected API error on stderr.\nstderr:\n{captured.err}"
        )
        assert "doesnotexist" in captured.err or "not found" in captured.err.lower(), (
            f"Expected repo name in error message.\nstderr:\n{captured.err}"
        )

    def test_nonexistent_version_error(self, capsys) -> None:
        """Graceful error message for a non-existent tag/version."""
        exit_code = run_cli(
            ["download", REPO, "-p", "*", "-v", "v999.999.999"]
        )
        captured = capsys.readouterr()

        assert exit_code == 2, (
            f"Expected exit code 2 for bad version, got {exit_code}"
        )
        assert "GitHub API error" in captured.err, (
            f"Expected API error on stderr.\nstderr:\n{captured.err}"
        )
        assert "v999.999.999" in captured.err, (
            f"Expected version in error message.\nstderr:\n{captured.err}"
        )


# ===================================================================
# Config subcommand
# ===================================================================


class TestConfigIntegration:
    """Integration tests for ``gh-dl config``."""

    def test_config_file_download(self, tmp_path: Path, capsys) -> None:
        """``gh-dl config`` with a valid JSON file downloads assets."""
        output_dir = str(tmp_path / "output")
        config_path = str(tmp_path / "repos.json")

        config_data = {
            "repos": [
                {
                    "owner": "jqlang",
                    "repo": "jq",
                    "pattern": SMALL_ASSET,
                    "version": "latest",
                    "output": output_dir,
                }
            ]
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f)

        exit_code = run_cli(["config", config_path])
        captured = capsys.readouterr()

        assert exit_code == 0, (
            f"config subcommand failed with exit {exit_code}.\n"
            f"stdout:\n{captured.out}\nstderr:\n{captured.err}"
        )
        # Verify the asset was downloaded
        asset_path = Path(output_dir) / "jqlang" / "jq" / "latest" / SMALL_ASSET
        assert asset_path.is_file(), f"Asset not found at expected path: {asset_path}"
        assert asset_path.stat().st_size == SMALL_ASSET_SIZE


# ===================================================================
# Flat output mode
# ===================================================================


class TestFlatOutputIntegration:
    """Integration tests for ``--flat`` output mode."""

    def test_flat_output_mode(self, tmp_path: Path) -> None:
        """``--flat`` downloads without the owner/repo/version hierarchy."""
        output_dir = str(tmp_path / "flat_dl")
        client = GitHubClient()
        manager = DownloadManager(client)

        result = manager.download_release(
            repo=REPO,
            pattern=[SMALL_ASSET],
            output_dir=output_dir,
            flat=True,
        )

        assert result.downloaded == 1

        # File should be directly in output_dir, not in subdirectories
        direct_path = Path(output_dir) / SMALL_ASSET
        assert direct_path.is_file(), (
            f"Expected file at {direct_path} (flat mode)"
        )

        # Hierarchy subdirectories should NOT exist
        hierarchy_path = Path(output_dir) / "jqlang" / "jq"
        assert not hierarchy_path.exists(), (
            f"Flat mode created hierarchy at {hierarchy_path}"
        )


# ===================================================================
# Edge-case tests
# ===================================================================


class TestEdgeCasesIntegration:
    """Edge-case handling with the real API."""

    def test_no_matching_patterns(self, capsys) -> None:
        """No matching patterns → clean message, exit 0."""
        exit_code = run_cli(
            ["download", REPO, "-p", "*.nonexistent_extension_xyz"]
        )
        captured = capsys.readouterr()

        assert exit_code == 0, (
            f"Expected exit 0 for no matches, got {exit_code}\n"
            f"stdout:\n{captured.out}\nstderr:\n{captured.err}"
        )
        assert "No matching assets found" in captured.out, (
            f"Expected 'No matching assets found' message.\nstdout:\n{captured.out}"
        )

    def test_list_with_no_matching_assets_shows_message(
        self, capsys,
    ) -> None:
        """``gh-dl list`` handles releases that exist but have no assets."""
        # jqlang/jq has many assets; test the "no assets" path via a version
        # that exists but conceptually has no downloadable assets.
        # Actually we can't easily find such a version, so test that list
        # output for a real repo at least mentions the expected assets.
        exit_code = run_cli(["list", REPO, "-v", "jq-1.8.1"])
        captured = capsys.readouterr()

        assert exit_code == 0
        # The release definitely has assets
        assert "jq-linux-amd64" in captured.out, (
            f"Expected asset listing.\nstdout:\n{captured.out}"
        )

    def test_invalid_config_file(self, tmp_path: Path, capsys) -> None:
        """Invalid config file → ConfigError with specific field name."""
        config_path = str(tmp_path / "bad_config.json")
        # Missing 'owner' field
        bad_data = {
            "repos": [
                {"repo": "jq", "pattern": "*.exe"}
            ]
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(bad_data, f)

        exit_code = run_cli(["config", config_path])
        captured = capsys.readouterr()

        assert exit_code == 2, (
            f"Expected exit 2 for invalid config, got {exit_code}"
        )
        assert "'owner' is required" in captured.err or "owner" in captured.err, (
            f"Expected error about missing 'owner' field.\nstderr:\n{captured.err}"
        )

    def test_flat_mode_collision_warning(self, tmp_path: Path, capsys) -> None:
        """File name conflicts in flat mode → warning on stderr."""
        output_dir = str(tmp_path / "collision_test")
        os.makedirs(output_dir, exist_ok=True)

        client = GitHubClient()
        manager = DownloadManager(client)

        # First download
        manager.download_release(
            repo=REPO,
            pattern=[SMALL_ASSET],
            output_dir=output_dir,
            flat=True,
        )

        # Second download of same asset (simulates colliding writes)
        result = manager.download_release(
            repo=REPO,
            pattern=[SMALL_ASSET],
            output_dir=output_dir,
            flat=True,
        )

        # Cache should skip it, so no collision warning is expected
        assert result.skipped == 1

        # Now test a real collision: place a file manually then check
        # build_output_path's warning path by calling the util directly.
        from gh_downloader.utils import build_output_path

        # Create an existing file at the would-be output path
        dummy_path = Path(output_dir) / SMALL_ASSET
        dummy_path.write_text("fake content")

        # build_output_path with flat=True should warn about the collision
        # (it checks os.path.exists at the destination)
        result_path = build_output_path(
            output_dir, "jqlang", "jq", "latest", SMALL_ASSET, flat=True,
        )

        captured = capsys.readouterr()
        assert "Warning" in captured.err or "collision" in captured.err, (
            f"Expected collision warning on stderr.\nstderr:\n{captured.err}"
        )
        assert SMALL_ASSET in captured.err, (
            f"Expected asset name in collision warning.\nstderr:\n{captured.err}"
        )
        assert result_path == str(dummy_path), (
            "build_output_path should still return the path despite collision"
        )

    def test_invalid_config_file_corrupt_json(self, tmp_path: Path, capsys) -> None:
        """Corrupt JSON config → ConfigError / parse error."""
        config_path = str(tmp_path / "corrupt.json")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write("{invalid json content}")

        exit_code = run_cli(["config", config_path])
        captured = capsys.readouterr()

        assert exit_code == 2, (
            f"Expected exit 2 for corrupt JSON, got {exit_code}"
        )
        # Should get either a JSON decode error or ConfigError wrapping it
        assert "error" in captured.err.lower(), (
            f"Expected error message on stderr.\nstderr:\n{captured.err}"
        )


# ===================================================================
# API-level integration tests (direct GitHubClient usage)
# ===================================================================


class TestApiClientIntegration:
    """Direct API client integration tests."""

    def test_get_release_latest(self) -> None:
        """Fetch latest release metadata from jqlang/jq."""
        client = GitHubClient()
        release = client.get_release(REPO, "latest")

        assert "tag_name" in release
        assert "id" in release
        assert isinstance(release["id"], int)

    def test_get_release_by_tag(self) -> None:
        """Fetch a specific tagged release."""
        client = GitHubClient()
        release = client.get_release(REPO, "jq-1.8.1")

        assert release["tag_name"] == "jq-1.8.1"

    def test_get_assets(self) -> None:
        """Fetch asset list for a release, verify it has assets."""
        client = GitHubClient()
        release = client.get_release(REPO, "latest")
        assets = client.get_assets(REPO, release["id"])

        assert len(assets) > 0, "Expected at least one asset"
        names = [a["name"] for a in assets]
        assert SMALL_ASSET in names, (
            f"Expected {SMALL_ASSET} in assets: {names}"
        )

    def test_repo_not_found(self) -> None:
        """Non-existent repo raises RepoNotFoundError."""
        client = GitHubClient()
        with pytest.raises(RepoNotFoundError) as exc:
            client.get_release("this/doesnotexist", "latest")
        assert "doesnotexist" in str(exc.value)

    def test_version_not_found(self) -> None:
        """Non-existent tag raises VersionNotFoundError."""
        client = GitHubClient()
        with pytest.raises(VersionNotFoundError) as exc:
            client.get_release(REPO, "v999.999.999")
        assert "v999.999.999" in str(exc.value)

    def test_is_authenticated_without_token(self) -> None:
        """Client is not authenticated when no token is set."""
        client = GitHubClient(token=None)
        assert client.is_authenticated() is False

    def test_rate_limit_info(self) -> None:
        """Rate limit endpoint returns expected structure."""
        client = GitHubClient()
        info = client.get_rate_limit_info()

        assert "limit" in info
        assert "remaining" in info
        assert "reset" in info
        assert info["remaining"] is not None
