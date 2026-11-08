"""
Offline tests for GitLab support and the provider abstraction.

No test here performs real network access: HTTP interactions are replaced by
small fakes or monkeypatched methods.
"""

from __future__ import annotations

import pytest

from gh_downloader.api import (
    GitHubClient,
    RepoNotFoundError,
    VersionNotFoundError,
)
from gh_downloader.config import UserConfig
from gh_downloader.downloader import DownloadManager
from gh_downloader.providers import (
    GitLabClient,
    create_client,
    resolve_provider,
)
from gh_downloader.utils import parse_full_repo_url, parse_repo_string

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal ``requests.Response`` stand-in."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: object = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}

    def json(self) -> object:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeGitLabClient:
    """Minimal client implementing the pieces DownloadManager needs."""

    def __init__(self) -> None:
        self.last_release_id: object = None

    def get_release(self, repo: str, version: str) -> dict:
        return {
            "id": "v1",
            "tag_name": "v1",
            "assets": {},
            "sources": [
                {"name": "a.zip", "url": "https://gitlab.com/a.zip"},
                {"name": "b.tar.gz", "url": "https://gitlab.com/b.tar.gz"},
            ],
        }

    def get_assets(self, repo: str, release_id: object) -> list[dict]:
        self.last_release_id = release_id
        return [
            {
                "name": "c.zip",
                "size": 0,
                "browser_download_url": "https://gitlab.com/c.zip",
            }
        ]

    @staticmethod
    def get_asset_download_url(asset: dict) -> str:
        return asset["browser_download_url"]


# ---------------------------------------------------------------------------
# parse_repo_string
# ---------------------------------------------------------------------------


class TestParseRepoStringGitLab:
    @pytest.mark.parametrize(
        ("value", "provider", "gitlab_url", "expected"),
        [
            ("https://gitlab.com/group/project", None, None, ("group", "project")),
            (
                "https://gitlab.com/group/sub/project",
                None,
                None,
                ("group/sub", "project"),
            ),
            (
                "https://gitlab.com/group/sub/project.git",
                None,
                None,
                ("group/sub", "project"),
            ),
            (
                "https://gitlab.example.com/team/proj",
                None,
                None,
                ("team", "proj"),
            ),
            (
                "https://code.example.com/team/proj",
                None,
                "https://code.example.com",
                ("team", "proj"),
            ),
            (
                "group/sub/project",
                "gitlab",
                None,
                ("group/sub", "project"),
            ),
        ],
    )
    def test_gitlab_parsing(self, value, provider, gitlab_url, expected):
        assert parse_repo_string(value, provider=provider, gitlab_url=gitlab_url) == expected

    def test_github_extra_path_still_truncates(self):
        assert parse_repo_string("https://github.com/org/repo/extra/path") == (
            "org",
            "repo",
        )

    def test_github_default_for_plain_repo(self):
        assert parse_repo_string("owner/repo") == ("owner", "repo")


# ---------------------------------------------------------------------------
# parse_full_repo_url
# ---------------------------------------------------------------------------


class TestParseFullRepoUrlGitLab:
    def test_nested_release_tag(self):
        assert parse_full_repo_url(
            "https://gitlab.com/group/sub/proj/-/releases/v1.2.3"
        ) == ("group/sub", "proj", "v1.2.3")

    def test_permalink_latest(self):
        assert parse_full_repo_url(
            "https://gitlab.com/group/sub/proj/-/releases/permalink/latest"
        ) == ("group/sub", "proj", "latest")

    def test_plain_gitlab_url_has_no_version(self):
        assert parse_full_repo_url("https://gitlab.com/group/sub/proj") == (
            "group/sub",
            "proj",
            None,
        )

    def test_github_cases_unchanged(self):
        assert parse_full_repo_url("owner/repo") == ("owner", "repo", None)
        assert parse_full_repo_url(
            "https://github.com/owner/repo/releases/tag/v1.0.0"
        ) == ("owner", "repo", "v1.0.0")
        assert parse_full_repo_url(
            "https://github.com/owner/repo/releases/latest"
        ) == ("owner", "repo", "latest")


# ---------------------------------------------------------------------------
# GitLabClient
# ---------------------------------------------------------------------------


class TestGitLabClient:
    def test_base_url_normalization(self):
        client = GitLabClient(base_url="https://gitlab.example.com")
        assert client.web_url == "https://gitlab.example.com"
        assert client.base_url == "https://gitlab.example.com/api/v4"

    def test_accepts_api_base_url(self):
        client = GitLabClient(base_url="https://gitlab.example.com/api/v4")
        assert client.web_url == "https://gitlab.example.com"
        assert client.base_url == "https://gitlab.example.com/api/v4"

    def test_session_header_has_private_token(self):
        client = GitLabClient(token="secret-token")
        assert client._session.headers["PRIVATE-TOKEN"] == "secret-token"
        assert client.is_authenticated() is True

    def test_latest_release_url(self, monkeypatch):
        client = GitLabClient(token="t")
        calls: list[str] = []
        response = _FakeResponse(
            json_data={"tag_name": "v1", "assets": {"sources": [], "links": []}}
        )

        def fake_request(method: str, url: str, **kwargs: object) -> _FakeResponse:
            calls.append(url)
            return response

        monkeypatch.setattr(client, "_request", fake_request)
        release = client.get_release("group/sub/proj", "latest")

        assert calls == [
            (
                "https://gitlab.com/api/v4/projects/"
                "group%2Fsub%2Fproj/releases/permalink/latest"
            )
        ]
        assert release["id"] == "v1"

    def test_tag_release_url_is_encoded(self, monkeypatch):
        client = GitLabClient(token="t")
        calls: list[str] = []

        def fake_request(method: str, url: str, **kwargs: object) -> _FakeResponse:
            calls.append(url)
            return _FakeResponse(json_data={"tag_name": "release/1"})

        monkeypatch.setattr(client, "_request", fake_request)
        client.get_release("group/proj", "release/1")

        assert calls == [
            (
                "https://gitlab.com/api/v4/projects/group%2Fproj"
                "/releases/release%2F1"
            )
        ]

    def test_latest_404_raises_repo_not_found(self, monkeypatch):
        client = GitLabClient(token="t")
        monkeypatch.setattr(
            client, "_request",
            lambda method, url, **kwargs: _FakeResponse(status_code=404),
        )
        with pytest.raises(RepoNotFoundError):
            client.get_release("group/proj", "latest")

    def test_tag_404_raises_version_not_found(self, monkeypatch):
        client = GitLabClient(token="t")
        monkeypatch.setattr(
            client, "_request",
            lambda method, url, **kwargs: _FakeResponse(status_code=404),
        )
        with pytest.raises(VersionNotFoundError):
            client.get_release("group/proj", "v9.9.9")

    def test_get_release_normalizes_sources(self, monkeypatch):
        client = GitLabClient(token="t")
        sources = [
            {
                "format": "zip",
                "url": "https://gitlab.com/group/proj/-/archive/v0.2/"
                "awesome-app-v0.2.zip",
            },
            {
                "format": "tar.gz",
                "url": "https://gitlab.com/group/proj/-/archive/v0.2/"
                "awesome-app-v0.2.tar.gz",
            },
            {
                "format": "tar.bz2",
                "url": "https://gitlab.com/group/proj/-/archive/v0.2/"
                "awesome-app-v0.2.tar.bz2",
            },
            {
                "format": "tar",
                "url": "https://gitlab.com/group/proj/-/archive/v0.2/"
                "awesome-app-v0.2.tar",
            },
            {
                "format": "zip",
                "url": "https://gitlab.com/group/proj/-/archive/v0.2/"
                "my%20app.zip?inline=false",
            },
            {"format": "zip", "url": ""},
        ]
        response = _FakeResponse(
            json_data={
                "tag_name": "v0.2",
                "assets": {"sources": sources, "links": []},
            }
        )
        monkeypatch.setattr(
            client, "_request", lambda method, url, **kwargs: response
        )

        release = client.get_release("group/proj", "latest")
        names = [src["name"] for src in release["sources"]]

        assert names == [
            "awesome-app-v0.2.zip",
            "awesome-app-v0.2.tar.gz",
            "awesome-app-v0.2.tar.bz2",
            "awesome-app-v0.2.tar",
            "my app.zip",
        ]

    def test_get_assets_normalizes_links(self, monkeypatch):
        client = GitLabClient(token="t")
        links = [
            {
                "name": "tool.zip",
                "url": "https://gitlab.com/group/proj/-/releases/v1/downloads/tool.zip",
                "direct_asset_url": "https://gitlab.com/group/proj/-/releases/v1/downloads/tool.zip/direct",
            },
            {
                "name": "plain.zip",
                "url": "https://gitlab.com/group/proj/uploads/plain.zip",
            },
            {
                "name": "dir/sub/file.zip",
                "url": "https://gitlab.com/group/proj/uploads/file.zip",
                "direct_asset_url": "https://gitlab.com/group/proj/uploads/file.zip/direct",
            },
            {
                "name": "amd64",
                "url": "https://gitlab.com/group/proj/-/releases/v1/"
                "downloads/bin/release-cli-windows-amd64.exe",
            },
            {"name": "no-url"},
            {"url": ""},
        ]
        monkeypatch.setattr(client, "_get_api_paginated", lambda path: links)

        assets = client.get_assets("group/sub/proj", "v1")

        assert len(assets) == 4
        assert assets[0] == {
            "name": "tool.zip",
            "size": 0,
            "browser_download_url": "https://gitlab.com/group/proj/-/releases/v1/downloads/tool.zip/direct",
        }
        assert assets[1]["browser_download_url"] == (
            "https://gitlab.com/group/proj/uploads/plain.zip"
        )
        assert assets[2]["name"] == "file.zip"
        # Link names that are just a platform must not hide the real filename.
        assert assets[3]["name"] == "release-cli-windows-amd64.exe"
        assert all(asset["size"] == 0 for asset in assets)

    def test_get_assets_falls_back_to_cached_release(self, monkeypatch):
        client = GitLabClient(token="t")
        client._last_release = {
            "assets": {
                "links": [
                    {"name": "cached.zip", "url": "https://gitlab.com/cached.zip"}
                ]
            }
        }
        monkeypatch.setattr(client, "_get_api_paginated", lambda path: None)

        assets = client.get_assets("group/proj", "v1")

        assert assets == [
            {
                "name": "cached.zip",
                "size": 0,
                "browser_download_url": "https://gitlab.com/cached.zip",
            }
        ]

    def test_token_not_leaked_to_external_host(self, monkeypatch):
        client = GitLabClient(token="secret")
        captured: list[dict[str, str]] = []

        def fake_get(url: str, **kwargs: object) -> object:
            captured.append(dict(kwargs.get("headers") or {}))
            return object()

        monkeypatch.setattr(client._download_session, "get", fake_get)

        client.open_download("https://cdn.example.com/asset.zip", {})
        client.open_download("https://gitlab.com/group/proj/asset.zip", {})

        assert "PRIVATE-TOKEN" not in captured[0]
        assert captured[1]["PRIVATE-TOKEN"] == "secret"

    def test_self_hosted_token_scoped_to_same_host(self, monkeypatch):
        client = GitLabClient(
            token="secret", base_url="https://gitlab.example.com"
        )
        captured: list[dict[str, str]] = []

        def fake_get(url: str, **kwargs: object) -> object:
            captured.append(dict(kwargs.get("headers") or {}))
            return object()

        monkeypatch.setattr(client._download_session, "get", fake_get)

        client.open_download("https://gitlab.example.com/files/x", {})
        client.open_download("https://gitlab.com/files/x", {})

        assert captured[0]["PRIVATE-TOKEN"] == "secret"
        assert "PRIVATE-TOKEN" not in captured[1]


# ---------------------------------------------------------------------------
# resolve_provider / create_client
# ---------------------------------------------------------------------------


class TestCreateClient:
    def test_resolve_provider(self):
        assert resolve_provider("owner/repo") == "github"
        assert resolve_provider("https://gitlab.com/group/proj") == "gitlab"
        assert resolve_provider("gitlab.com/group/proj") == "github"
        assert (
            resolve_provider(
                "https://code.example.com/group/proj",
                gitlab_url="https://code.example.com",
            )
            == "gitlab"
        )
        assert resolve_provider("owner/repo", provider="gitlab") == "gitlab"

    def test_resolve_provider_rejects_unknown(self):
        with pytest.raises(ValueError):
            resolve_provider("owner/repo", provider="bitbucket")

    def test_gitlab_com_url(self, monkeypatch):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        client = create_client(
            "https://gitlab.com/group/proj", user_config=UserConfig()
        )
        assert isinstance(client, GitLabClient)
        assert client.base_url == "https://gitlab.com/api/v4"

    def test_self_hosted_url(self, monkeypatch):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        client = create_client(
            "https://code.example.com/group/proj",
            gitlab_url="https://code.example.com",
            user_config=UserConfig(),
        )
        assert isinstance(client, GitLabClient)
        assert client.base_url == "https://code.example.com/api/v4"

    def test_plain_repo_defaults_to_github(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        client = create_client("owner/repo", user_config=UserConfig())
        assert isinstance(client, GitHubClient)
        assert client.base_url == "https://api.github.com"

    def test_explicit_provider_on_plain_path(self, monkeypatch):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        client = create_client(
            "group/proj", provider="gitlab", user_config=UserConfig()
        )
        assert isinstance(client, GitLabClient)

    def test_custom_github_api_url(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        client = create_client(
            "owner/repo",
            github_api_url="https://ghe.example.com/api/v3",
            user_config=UserConfig(),
        )
        assert isinstance(client, GitHubClient)
        assert client.base_url == "https://ghe.example.com/api/v3"

    def test_user_config_values_are_used(self, monkeypatch):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        config = UserConfig(
            gitlab_url="https://gl.example.com", gitlab_token="cfg-token"
        )
        client = create_client(
            "https://gl.example.com/group/proj", user_config=config
        )
        assert isinstance(client, GitLabClient)
        assert client.base_url == "https://gl.example.com/api/v4"
        assert client._token == "cfg-token"


# ---------------------------------------------------------------------------
# DownloadManager integration (dry run)
# ---------------------------------------------------------------------------


class TestDownloadManagerGitLab:
    def test_dry_run_counts_links_and_sources(
        self, tmp_path, capsys
    ):
        client = _FakeGitLabClient()
        manager = DownloadManager(client)

        result = manager.download_release(
            repo="group/sub/proj",
            pattern=["*"],
            output_dir=str(tmp_path),
            dry_run=True,
            sources=True,
        )

        assert result.total == 3
        assert client.last_release_id == "v1"
        output = capsys.readouterr().out
        assert "c.zip" in output
        assert "a.zip" in output
        assert "b.tar.gz" in output

    def test_sources_can_be_disabled(self, tmp_path):
        client = _FakeGitLabClient()
        manager = DownloadManager(client)

        result = manager.download_release(
            repo="group/sub/proj",
            pattern=["*"],
            output_dir=str(tmp_path),
            dry_run=True,
            sources=False,
        )

        assert result.total == 1