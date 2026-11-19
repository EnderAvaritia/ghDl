"""
Tests for ghproxy-style mirror rewriting and download-token scoping
(``gh_downloader.api`` and ``gh_downloader.providers``).
"""

from __future__ import annotations

from gh_downloader.api import GitHubClient
from gh_downloader.config import UserConfig
from gh_downloader.providers import GitLabClient, create_client

_GITHUB_URL = "https://github.com/o/r/releases/download/v1/a.zip"
_MIRROR = "https://ghfast.top"


# ---------------------------------------------------------------------------
# URL rewriting
# ---------------------------------------------------------------------------


class TestGitHubRewrite:
    def test_prefix_is_prepended(self):
        client = GitHubClient(proxy_prefix=_MIRROR)
        assert (
            client.rewrite_download_url(_GITHUB_URL)
            == f"{_MIRROR}/{_GITHUB_URL}"
        )

    def test_trailing_slash_is_normalised(self):
        client = GitHubClient(proxy_prefix=_MIRROR + "/")
        assert client.rewrite_download_url(_GITHUB_URL) == f"{_MIRROR}/{_GITHUB_URL}"

    def test_no_prefix_is_identity(self):
        client = GitHubClient()
        assert client.rewrite_download_url(_GITHUB_URL) == _GITHUB_URL

    def test_non_http_input_is_identity(self):
        client = GitHubClient(proxy_prefix=_MIRROR)
        assert client.rewrite_download_url("ftp://example.com/a.zip") == (
            "ftp://example.com/a.zip"
        )
        assert client.rewrite_download_url("relative/a.zip") == "relative/a.zip"


class TestGitLabRewrite:
    def test_is_identity(self):
        client = GitLabClient()
        assert client.rewrite_download_url(_GITHUB_URL) == _GITHUB_URL


# ---------------------------------------------------------------------------
# Token scoping
# ---------------------------------------------------------------------------


class _DummyResponse:
    pass


class TestDownloadAuthScoping:
    def test_proxied_download_sends_no_authorization(self, monkeypatch):
        client = GitHubClient(token="secret-token", proxy_prefix=_MIRROR)
        captured: list[dict[str, str]] = []

        def fake_get(url: str, **kwargs: object) -> _DummyResponse:
            captured.append(dict(kwargs.get("headers") or {}))
            return _DummyResponse()

        monkeypatch.setattr(client._download_session, "get", fake_get)

        proxied_url = client.rewrite_download_url(_GITHUB_URL)
        client.open_download(proxied_url, {})

        assert captured[0].get("Authorization") is None
        assert "Authorization" not in client._download_session.headers

    def test_without_prefix_uses_authenticated_session(self, monkeypatch):
        client = GitHubClient(token="secret-token")
        captured: list[str] = []

        def fake_get(url: str, **kwargs: object) -> _DummyResponse:
            captured.append(url)
            return _DummyResponse()

        monkeypatch.setattr(client._session, "get", fake_get)

        client.open_download(_GITHUB_URL, {})

        assert captured == [_GITHUB_URL]
        assert client._session.headers["Authorization"] == "Bearer secret-token"

    def test_download_session_stays_clean_after_proxied_call(self, monkeypatch):
        client = GitHubClient(token="secret-token", proxy_prefix=_MIRROR)
        monkeypatch.setattr(
            client._download_session, "get", lambda *a, **k: _DummyResponse()
        )

        client.open_download(client.rewrite_download_url(_GITHUB_URL), {})

        assert "Authorization" not in client._download_session.headers


# ---------------------------------------------------------------------------
# create_client resolution
# ---------------------------------------------------------------------------


class TestCreateClientProxy:
    def test_explicit_proxy_is_used(self, monkeypatch):
        monkeypatch.delenv("GH_PROXY", raising=False)
        client = create_client(
            "owner/repo", ghproxy=_MIRROR, user_config=UserConfig()
        )
        assert isinstance(client, GitHubClient)
        assert (
            client.rewrite_download_url(_GITHUB_URL)
            == f"{_MIRROR}/{_GITHUB_URL}"
        )

    def test_env_proxy_is_used(self, monkeypatch):
        monkeypatch.setenv("GH_PROXY", _MIRROR)
        client = create_client("owner/repo", user_config=UserConfig())
        assert isinstance(client, GitHubClient)
        assert (
            client.rewrite_download_url(_GITHUB_URL)
            == f"{_MIRROR}/{_GITHUB_URL}"
        )

    def test_user_config_proxy_is_used(self, monkeypatch):
        monkeypatch.delenv("GH_PROXY", raising=False)
        client = create_client(
            "owner/repo",
            user_config=UserConfig(ghproxy=_MIRROR),
        )
        assert isinstance(client, GitHubClient)
        assert (
            client.rewrite_download_url(_GITHUB_URL)
            == f"{_MIRROR}/{_GITHUB_URL}"
        )

    def test_explicit_beats_env(self, monkeypatch):
        monkeypatch.setenv("GH_PROXY", "https://env-mirror.example")
        client = create_client(
            "owner/repo", ghproxy=_MIRROR, user_config=UserConfig()
        )
        assert (
            client.rewrite_download_url(_GITHUB_URL)
            == f"{_MIRROR}/{_GITHUB_URL}"
        )

    def test_gitlab_client_is_unaffected(self, monkeypatch):
        monkeypatch.delenv("GH_PROXY", raising=False)
        client = create_client(
            "https://gitlab.com/group/proj",
            ghproxy=_MIRROR,
            user_config=UserConfig(),
        )
        assert isinstance(client, GitLabClient)
        assert client.rewrite_download_url(_GITHUB_URL) == _GITHUB_URL