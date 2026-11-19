"""
Release provider abstraction for gh-downloader.

Exposes a common :class:`ReleaseClient` protocol implemented by both
:class:`gh_downloader.api.GitHubClient` and :class:`GitLabClient`, plus a
:func:`create_client` factory and a :func:`resolve_provider` helper that
auto-detects the provider from a repository identifier or URL.

The GitLab implementation supports GitLab.com as well as self-hosted
instances and authenticates with a personal access token sent through the
``PRIVATE-TOKEN`` header.
"""

from __future__ import annotations

import os
import time
import urllib.parse
from typing import Any, Protocol

import requests

from gh_downloader.api import (
    GitHubClient,
    GitHubError,
    NetworkError,
    RateLimitError,
    RepoNotFoundError,
    VersionNotFoundError,
    _parse_next_link,
)

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class ReleaseClient(Protocol):
    """Structural interface implemented by all release providers."""

    base_url: str

    def get_release(self, repo: str, version: str) -> dict: ...

    def get_assets(self, repo: str, release_id: Any) -> list[dict]: ...

    def get_asset_download_url(self, asset: dict) -> str: ...

    def is_authenticated(self) -> bool: ...

    def get_rate_limit_info(self) -> dict: ...

    def open_download(
        self, url: str, headers: dict[str, str] | None = None
    ) -> requests.Response: ...

    def rewrite_download_url(self, url: str) -> str: ...


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

_VALID_PROVIDERS = ("github", "gitlab")


def _host_from_url(url: str) -> str:
    """Return the lower-cased hostname of *url*, or an empty string."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    return (parsed.hostname or "").lower()


def _detect_provider_from_host(host: str, gitlab_url: str | None) -> str:
    """Infer the provider from a URL hostname."""
    host = host.lower()
    if host in ("github.com", "www.github.com", "api.github.com"):
        return "github"
    if "github" in host:
        return "github"
    if host == "gitlab.com" or "gitlab" in host:
        return "gitlab"
    if gitlab_url:
        gitlab_host = _host_from_url(gitlab_url)
        if gitlab_host and gitlab_host == host:
            return "gitlab"
    return "github"


def resolve_provider(
    repo: str,
    provider: str | None = None,
    gitlab_url: str | None = None,
) -> str:
    """Resolve which provider should handle *repo*.

    An explicit *provider* always wins.  Otherwise the hostname of a full
    URL is inspected; plain ``owner/repo`` strings default to GitHub.
    """
    if provider is not None:
        normalized = provider.strip().lower()
        if normalized not in _VALID_PROVIDERS:
            raise ValueError(
                f"Invalid provider {provider!r}; expected 'github' or 'gitlab'"
            )
        return normalized

    if "://" in repo:
        host = _host_from_url(repo)
        return _detect_provider_from_host(host, gitlab_url)

    return "github"


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def create_client(
    repo: str,
    provider: str | None = None,
    gitlab_url: str | None = None,
    github_api_url: str | None = None,
    user_config: Any = None,
    ghproxy: str | None = None,
) -> ReleaseClient:
    """Create the appropriate release client for *repo*.

    GitLab credentials/URLs come from the explicit parameters first, then
    from *user_config*, then from environment variables / defaults.  The
    GitHub mirror prefix is resolved in the same order (explicit *ghproxy*,
    ``user_config.ghproxy``, ``GH_PROXY``) and is ignored by GitLab.
    """
    config_gitlab_url = getattr(user_config, "gitlab_url", None)
    effective_gitlab_url = gitlab_url or config_gitlab_url
    resolved = resolve_provider(
        repo, provider=provider, gitlab_url=effective_gitlab_url
    )

    if resolved == "gitlab":
        config_token = getattr(user_config, "gitlab_token", None)
        base_url = effective_gitlab_url or "https://gitlab.com"
        token = os.environ.get("GITLAB_TOKEN") or config_token
        return GitLabClient(token=token, base_url=base_url)

    config_api = getattr(user_config, "github_api_url", None)
    config_token = getattr(user_config, "github_token", None)
    config_ghproxy = getattr(user_config, "ghproxy", None)
    api_url = github_api_url or config_api or "https://api.github.com"
    token = os.environ.get("GITHUB_TOKEN") or config_token
    effective_ghproxy = _clean_optional(
        ghproxy
    ) or _clean_optional(
        config_ghproxy
    ) or _clean_optional(
        os.environ.get("GH_PROXY")
    )
    return GitHubClient(
        token=token, base_url=api_url, proxy_prefix=effective_ghproxy
    )


def _clean_optional(value: str | None) -> str | None:
    """Return *value* stripped, or ``None`` when blank / not a string."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


# ---------------------------------------------------------------------------
# GitLab client
# ---------------------------------------------------------------------------

class GitLabClient:
    """Client for the GitLab Releases REST API v4.

    Parameters
    ----------
    token:
        A GitLab personal access token.  If ``None`` the ``GITLAB_TOKEN``
        environment variable is consulted.
    base_url:
        Web base URL of the GitLab instance (e.g. ``https://gitlab.com``).
        An ``/api/v4`` suffix is accepted and handled transparently.
    """

    _API_SUFFIX = "/api/v4"

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://gitlab.com",
    ) -> None:
        raw_base = (base_url or "https://gitlab.com").rstrip("/")
        if raw_base.endswith(self._API_SUFFIX):
            self.web_url = raw_base[: -len(self._API_SUFFIX)].rstrip("/")
            self.base_url = raw_base
        else:
            self.web_url = raw_base
            self.base_url = raw_base + self._API_SUFFIX

        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "gh-dl/0.1.0",
        })

        # Token in the API session; the download session deliberately has no
        # auth headers so tokens never leak to external asset hosts.
        raw_token = token if token is not None else os.environ.get("GITLAB_TOKEN")
        self._token = raw_token.strip("\"'") if raw_token else None
        if self._token:
            self._session.headers["PRIVATE-TOKEN"] = self._token

        self._download_session = requests.Session()
        self._last_release: dict | None = None

    # -- Public helpers ----------------------------------------------------

    def is_authenticated(self) -> bool:
        """Return ``True`` if a token is configured."""
        return bool(self._token)

    def get_rate_limit_info(self) -> dict:
        """Return an empty dict (GitLab has no equivalent endpoint here)."""
        return {}

    # -- Release / asset API ------------------------------------------------

    @staticmethod
    def _project_id(repo: str) -> str:
        """URL-encode a project path (``group/sub/proj`` -> ``group%2Fsub%2Fproj``)."""
        return urllib.parse.quote(repo, safe="")

    def get_release(self, repo: str, version: str) -> dict:
        """Fetch release information for *repo*.

        ``version`` may be ``"latest"`` or a tag name.  The returned dict is
        augmented with ``id`` (the tag name, used as the release key by the
        downloader) and a normalized ``sources`` list.
        """
        project_id = self._project_id(repo)

        if version == "latest":
            path = f"/projects/{project_id}/releases/permalink/latest"
        else:
            encoded_tag = urllib.parse.quote(version, safe="")
            path = f"/projects/{project_id}/releases/{encoded_tag}"

        response = self._request("GET", f"{self.base_url}{path}")

        if response.status_code == 404:
            if version == "latest":
                raise RepoNotFoundError(
                    f"GitLab project '{repo}' not found or has no releases."
                )
            raise VersionNotFoundError(
                f"Version/tag '{version}' not found in project '{repo}'."
            )

        response.raise_for_status()
        data: dict = response.json()

        # Reuse the tag name as the release id so the downloader's
        # ``get_assets(repo, release["id"])`` call works unchanged.
        data["id"] = data.get("tag_name") or version

        data["sources"] = self._normalize_sources(data)
        self._last_release = data
        return data

    @staticmethod
    def _normalize_sources(data: dict) -> list[dict]:
        """Normalize ``assets.sources`` into name/url dicts."""
        sources: list[dict] = []
        raw_sources = data.get("assets", {}).get("sources", []) or []
        for source in raw_sources:
            url = source.get("url")
            if not url:
                continue
            path = urllib.parse.urlsplit(url).path
            name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            sources.append({"name": name, "url": url})
        return sources

    def get_assets(self, repo: str, release_id: Any) -> list[dict]:
        """Return normalized asset links for a GitLab release.

        ``release_id`` is the tag name for GitLab.  Falls back to the cached
        release payload when the links endpoint is unavailable.
        """
        project_id = self._project_id(repo)
        encoded_tag = urllib.parse.quote(str(release_id), safe="")
        path = f"/projects/{project_id}/releases/{encoded_tag}/assets/links"

        links = self._get_api_paginated(path)
        if links is None:
            # Endpoint returned 404 -- fall back to links embedded in the
            # release payload, if any.
            if self._last_release:
                links = self._last_release.get("assets", {}).get("links", [])
            else:
                links = []

        if not isinstance(links, list):
            return []

        assets: list[dict] = []
        for link in links:
            if not isinstance(link, dict):
                continue
            url = link.get("direct_asset_url") or link.get("url")
            if not url:
                continue
            link_name = link.get("name") or ""
            path_part = urllib.parse.urlsplit(url).path
            url_name = urllib.parse.unquote(path_part.rsplit("/", 1)[-1])
            # GitLab link names are often just a platform (for example
            # "amd64") while the URL carries the real filename.  Prefer
            # whichever candidate looks most like a filename, then the URL
            # basename unless it is a generic endpoint such as "download".
            generic_names = {"", "download", "direct", "file"}
            if "." in url_name:
                name = url_name
            elif "." in link_name:
                name = link_name
            elif url_name.lower() not in generic_names:
                name = url_name
            else:
                name = link_name or url_name
            # Guard against path traversal from crafted link names.
            if "/" in name:
                name = name.rsplit("/", 1)[-1]
            if not name:
                continue
            assets.append({
                "name": name,
                "size": 0,
                "browser_download_url": url,
            })
        return assets

    @staticmethod
    def get_asset_download_url(asset: dict) -> str:
        """Return the direct download URL from an asset dict."""
        return asset["browser_download_url"]

    def rewrite_download_url(self, url: str) -> str:
        """Return *url* unchanged (mirrors are a GitHub-only feature)."""
        return url

    def open_download(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        """Open a streaming download, adding the token only for GitLab hosts."""
        request_headers = dict(headers or {})
        if self._token:
            target_host = urllib.parse.urlsplit(url).netloc
            gitlab_host = urllib.parse.urlsplit(self.web_url).netloc
            if target_host == gitlab_host:
                request_headers["PRIVATE-TOKEN"] = self._token
        return self._download_session.get(
            url, stream=True, headers=request_headers
        )

    # -- Internal request helpers ------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Low-level request with GitLab-specific error handling."""
        try:
            response = self._session.request(method, url, **kwargs)
        except requests.exceptions.ConnectionError as e:
            raise NetworkError(f"Connection failed: {e}") from e
        except requests.exceptions.Timeout as e:
            raise NetworkError(f"Request timed out: {e}") from e
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Request failed: {e}") from e

        if response.status_code == 401:
            raise GitHubError(
                "GitLab API requires authentication. "
                "Set the GITLAB_TOKEN environment variable.\n"
                "Create a token at: "
                f"{self.web_url}/-/user_settings/personal_access_tokens"
            )

        if response.status_code == 429:
            self._raise_rate_limit(response)

        if response.status_code == 403:
            remaining = response.headers.get("RateLimit-Remaining")
            if remaining is not None and remaining == "0":
                self._raise_rate_limit(response)
            raise GitHubError(
                "GitLab API returned 403 Forbidden. "
                "This usually means authentication is required.\n"
                "Set the GITLAB_TOKEN environment variable.\n"
                "Create a token at: "
                f"{self.web_url}/-/user_settings/personal_access_tokens"
            )

        return response

    def _get_api_paginated(self, path: str) -> Any:
        """GET an API endpoint, following ``Link`` headers for pagination.

        Returns ``None`` when the endpoint responds with 404 so callers can
        apply a fallback.
        """
        url = f"{self.base_url}{path}"
        params: dict[str, Any] = {"per_page": 100}

        response = self._request("GET", url, params=params)
        if response.status_code == 404:
            return None

        data = response.json()
        if not isinstance(data, list):
            return data

        collected: list[Any] = list(data)
        next_url = _parse_next_link(response.headers.get("Link", ""))
        while next_url:
            response = self._request("GET", next_url)
            page = response.json()
            if isinstance(page, list):
                collected.extend(page)
            next_url = _parse_next_link(response.headers.get("Link", ""))
        return collected

    def _raise_rate_limit(self, response: requests.Response) -> None:
        """Raise a :class:`RateLimitError` with GitLab wording."""
        raw_reset = response.headers.get("RateLimit-Reset", "0")
        try:
            reset_value = int(float(raw_reset))
        except (TypeError, ValueError):
            reset_value = 0

        if reset_value > 1_000_000_000:
            reset_epoch = reset_value
        elif reset_value > 0:
            reset_epoch = int(time.time()) + reset_value
        else:
            reset_epoch = 0

        if reset_epoch:
            reset_str = time.strftime(
                "%Y-%m-%d %H:%M:%S UTC", time.gmtime(reset_epoch)
            )
        else:
            reset_str = "unknown"

        raise RateLimitError(
            f"GitLab API rate limit exceeded. "
            f"Resets at {reset_str}. "
            f"Set the GITLAB_TOKEN environment variable for a higher limit."
        )


__all__ = [
    "GitLabClient",
    "ReleaseClient",
    "create_client",
    "resolve_provider",
]