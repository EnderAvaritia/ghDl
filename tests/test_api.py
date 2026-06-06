"""
Tests for the GitHub Releases API client (``gh_downloader.api``).
"""

from __future__ import annotations

import pytest

from gh_downloader.api import (
    GitHubClient,
    RepoNotFoundError,
    VersionNotFoundError,
)


# -- Helpers -----------------------------------------------------------------


def _url(repo: str, version: str) -> str:
    """Build the expected API URL for a release lookup."""
    base = "https://api.github.com"
    if version == "latest":
        return f"{base}/repos/{repo}/releases/latest"
    return f"{base}/repos/{repo}/releases/tags/{version}"


# -- get_release: latest -----------------------------------------------------


class TestGetReleaseLatest:
    def test_returns_expected_tag(self, mock_api_client, sample_release_response):
        repo = "stedolan/jq"
        url = _url(repo, "latest")
        mock_api_client._mock_registry[url] = (200, sample_release_response)

        result = mock_api_client.get_release(repo, "latest")

        assert result["tag_name"] == "jq-1.6"
        assert result["id"] == 123

    def test_raises_repo_not_found_on_404(self, mock_api_client):
        repo = "nonexistent/user"
        url = _url(repo, "latest")
        mock_api_client._mock_registry[url] = (404, {"message": "Not Found"})

        with pytest.raises(RepoNotFoundError) as exc:
            mock_api_client.get_release(repo, "latest")

        assert "nonexistent/user" in str(exc.value)

    def test_no_assets_succeeds(self, mock_api_client):
        """A release with no assets should still return a valid response."""
        repo = "stedolan/jq"
        url = _url(repo, "latest")
        empty_release = {
            "tag_name": "jq-1.6",
            "id": 123,
            "assets_url": (
                "https://api.github.com/repos/stedolan/jq/releases/123/assets"
            ),
            "assets": [],
        }
        mock_api_client._mock_registry[url] = (200, empty_release)

        result = mock_api_client.get_release(repo, "latest")
        assert result["assets"] == []


# -- get_release: by tag -----------------------------------------------------


class TestGetReleaseByTag:
    def test_returns_expected_tag(self, mock_api_client, sample_release_response):
        repo = "stedolan/jq"
        url = _url(repo, "jq-1.6")
        mock_api_client._mock_registry[url] = (200, sample_release_response)

        result = mock_api_client.get_release(repo, "jq-1.6")

        assert result["tag_name"] == "jq-1.6"

    def test_raises_version_not_found(self, mock_api_client):
        repo = "stedolan/jq"
        url = _url(repo, "nonexistent-tag")
        mock_api_client._mock_registry[url] = (404, {"message": "Not Found"})

        with pytest.raises(VersionNotFoundError) as exc:
            mock_api_client.get_release(repo, "nonexistent-tag")

        assert "nonexistent-tag" in str(exc.value)
        assert "stedolan/jq" in str(exc.value)


# -- is_authenticated --------------------------------------------------------


class TestIsAuthenticated:
    def test_with_token(self):
        client = GitHubClient(token="ghp_abc123")
        assert client.is_authenticated() is True

    def test_without_token(self):
        client = GitHubClient(token=None)
        assert client.is_authenticated() is False

    def test_empty_token_string(self):
        client = GitHubClient(token="")
        assert client.is_authenticated() is False


# -- get_asset_download_url --------------------------------------------------


class TestGetAssetDownloadUrl:
    def test_returns_browser_download_url(self):
        asset = {
            "browser_download_url": (
                "https://github.com/stedolan/jq/releases/download/"
                "jq-1.6/jq-win64.exe"
            ),
        }
        result = GitHubClient.get_asset_download_url(asset)
        assert result == asset["browser_download_url"]


# -- Pagination --------------------------------------------------------------


class TestPagination:
    def test_get_assets_multiple_pages(self, mock_api_client, sample_assets_response):
        """Verify that paginated asset lists are flattened into one."""
        repo = "stedolan/jq"
        path = f"/repos/{repo}/releases/123/assets"
        url = f"https://api.github.com{path}"

        # The _mock_registry is used by _get_api_paginated via our fixture
        mock_api_client._mock_registry[url] = (200, sample_assets_response)

        assets = mock_api_client.get_assets(repo, 123)
        assert len(assets) == 3
        assert assets[0]["name"] == "jq-win64.exe"

    def test_get_assets_single_page(self, mock_api_client):
        repo = "stedolan/jq"
        path = f"/repos/{repo}/releases/456/assets"
        url = f"https://api.github.com{path}"
        single_asset = [
            {
                "id": 2001,
                "name": "single-asset.zip",
                "size": 512,
                "browser_download_url": "https://example.com/single-asset.zip",
            }
        ]
        mock_api_client._mock_registry[url] = (200, single_asset)

        assets = mock_api_client.get_assets(repo, 456)
        assert len(assets) == 1
        assert assets[0]["name"] == "single-asset.zip"

    def test_no_assets_returns_empty_list(self, mock_api_client):
        repo = "stedolan/jq"
        path = f"/repos/{repo}/releases/789/assets"
        url = f"https://api.github.com{path}"
        mock_api_client._mock_registry[url] = (200, [])

        assets = mock_api_client.get_assets(repo, 789)
        assert assets == []
