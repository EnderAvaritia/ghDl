"""
Shared pytest fixtures for gh-downloader test suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from gh_downloader.api import GitHubClient
from gh_downloader.config import RepoConfig


# ---------------------------------------------------------------------------
# Sample API response fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_release_response() -> dict:
    """Dict mimicking a GitHub Releases API release object."""
    return {
        "url": "https://api.github.com/repos/stedolan/jq/releases/123",
        "assets_url": (
            "https://api.github.com/repos/stedolan/jq/releases/123/assets"
        ),
        "id": 123,
        "tag_name": "jq-1.6",
        "target_commitish": "main",
        "name": "jq 1.6",
        "draft": False,
        "prerelease": False,
        "created_at": "2018-11-04T19:24:14Z",
        "published_at": "2018-11-04T19:24:14Z",
        "author": {"login": "stedolan", "id": 1},
        "assets": [],
        "body": "Release notes...",
        "zipball_url": "https://api.github.com/repos/stedolan/jq/zipball/jq-1.6",
        "tarball_url": "https://api.github.com/repos/stedolan/jq/tarball/jq-1.6",
    }


@pytest.fixture
def sample_assets_response() -> list[dict]:
    """List of dicts mimicking GitHub API asset objects."""
    return [
        {
            "id": 1001,
            "name": "jq-win64.exe",
            "size": 1_048_576,
            "browser_download_url": (
                "https://github.com/stedolan/jq/releases/download/"
                "jq-1.6/jq-win64.exe"
            ),
            "content_type": "application/octet-stream",
            "state": "uploaded",
            "created_at": "2018-11-04T19:24:14Z",
            "updated_at": "2018-11-04T19:24:14Z",
            "download_count": 42,
        },
        {
            "id": 1002,
            "name": "jq-linux64",
            "size": 2_048_000,
            "browser_download_url": (
                "https://github.com/stedolan/jq/releases/download/"
                "jq-1.6/jq-linux64"
            ),
            "content_type": "application/octet-stream",
            "state": "uploaded",
            "created_at": "2018-11-04T19:24:14Z",
            "updated_at": "2018-11-04T19:24:14Z",
            "download_count": 1337,
        },
        {
            "id": 1003,
            "name": "jq-osx-amd64",
            "size": 1_048_576,
            "browser_download_url": (
                "https://github.com/stedolan/jq/releases/download/"
                "jq-1.6/jq-osx-amd64"
            ),
            "content_type": "application/octet-stream",
            "state": "uploaded",
            "created_at": "2018-11-04T19:24:14Z",
            "updated_at": "2018-11-04T19:24:14Z",
            "download_count": 99,
        },
    ]


# ---------------------------------------------------------------------------
# Domain object fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_repo_config() -> RepoConfig:
    """A ``RepoConfig`` instance representing stedolan/jq."""
    return RepoConfig(
        owner="stedolan",
        repo="jq",
        pattern="*.exe",
        version="latest",
        output=None,
    )


# ---------------------------------------------------------------------------
# Temporary directory
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_output_dir(tmp_path) -> str:
    """Temporary download directory that auto-cleans after the test."""
    d = tmp_path / "downloads"
    d.mkdir()
    return str(d)


# ---------------------------------------------------------------------------
# Mocked API client
# ---------------------------------------------------------------------------


def _build_mock_response(
    status_code: int = 200,
    json_data: object = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a ``requests.Response``-like ``MagicMock``."""
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.headers = headers or {}
    mock.json.return_value = json_data if json_data is not None else {}

    if status_code >= 400:

        def _raise():
            raise requests.HTTPError(
                f"HTTP {status_code}", response=mock
            )

        mock.raise_for_status = _raise
    else:
        mock.raise_for_status = lambda: None

    return mock


@pytest.fixture
def mock_api_client():
    """Return a ``GitHubClient`` with ``_request`` patched.

    The client's ``_mock_registry`` dict maps URL → ``(status, json_data)``.
    Tests can register responses before calling API methods::

        client._mock_registry[url] = (200, {"tag_name": "v1.0"})
    """
    client = GitHubClient(token="test-token")
    registry: dict[str, tuple[int, object]] = {}

    def _side_effect(method: str, url: str, **kwargs: object) -> MagicMock:
        status, json_data = registry.get(url, (200, {}))
        return _build_mock_response(status_code=status, json_data=json_data)

    patcher = patch.object(client, "_request", side_effect=_side_effect)
    patcher.start()

    # Also patch _get_api_paginated to use the same registry
    def _paginated_side_effect(path: str) -> object:
        url = f"{client.base_url}{path}"
        status, json_data = registry.get(url, (200, {}))
        return json_data

    paginated_patcher = patch.object(
        client, "_get_api_paginated", side_effect=_paginated_side_effect
    )
    paginated_patcher.start()

    client._mock_registry = registry
    client._mock_patchers = [patcher, paginated_patcher]

    yield client

    for p in client._mock_patchers:
        p.stop()
