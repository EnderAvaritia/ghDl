"""
GitHub Releases API v3 client for gh-dl.

Provides the GitHubClient class for all HTTP interactions with the
GitHub Releases API, including authentication, pagination, rate limit
handling, and streaming downloads.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import requests


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class GitHubError(IOError):
    """Base exception for all GitHub API errors in gh-dl."""


class RepoNotFoundError(GitHubError):
    """The requested repository was not found (HTTP 404)."""


class VersionNotFoundError(GitHubError):
    """The requested release version or tag was not found."""


class RateLimitError(GitHubError):
    """GitHub API rate limit has been exceeded."""


class NetworkError(GitHubError):
    """A network-level error occurred (connection refused, timeout, DNS, ...)."""


# ---------------------------------------------------------------------------
# GitHubClient
# ---------------------------------------------------------------------------

class GitHubClient:
    """Client for the GitHub Releases REST API v3.

    Wraps a ``requests.Session`` for connection reuse, handles
    authentication via ``GITHUB_TOKEN``, parses paginated responses,
    detects rate limiting, and provides streaming asset downloads.

    Parameters
    ----------
    token:
        A GitHub personal access token.  If ``None`` (the default) the
        ``GITHUB_TOKEN`` environment variable is consulted.
    base_url:
        Base URL of the GitHub API.  Defaults to ``https://api.github.com``.
        Can be changed for GitHub Enterprise.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        self.base_url = base_url.rstrip("/")

        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/vnd.github+json",
            "User-Agent": "gh-dl/0.1.0",
        })

        # Resolve token: explicit arg > env var
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        if self._token:
            self._session.headers["Authorization"] = f"Bearer {self._token}"

    # -- Public helpers ----------------------------------------------------

    def is_authenticated(self) -> bool:
        """Return ``True`` if a token is configured."""
        return bool(self._token)

    def get_rate_limit_info(self) -> Dict[str, Any]:
        """Query the ``/rate_limit`` endpoint and return core resource info.

        Returns a dict with keys ``limit``, ``remaining``, ``reset`` (epoch),
        and ``used``.  Returns an empty dict if the endpoint can't be reached.
        """
        try:
            response = self._request("GET", f"{self.base_url}/rate_limit")
            data: dict = response.json()
        except (GitHubError, requests.RequestException, ValueError):
            return {}

        core = data.get("resources", {}).get("core", {})
        return {
            "limit": core.get("limit", "unknown"),
            "remaining": core.get("remaining", "unknown"),
            "reset": core.get("reset", 0),
            "used": core.get("used", "unknown"),
        }

    # -- Release / asset API -----------------------------------------------

    def get_release(self, repo: str, version: str) -> dict:
        """Fetch release information for *repo*.

        Parameters
        ----------
        repo:
            Repository identifier in ``owner/name`` format (e.g. ``stedolan/jq``).
        version:
            Either the literal string ``"latest"`` to fetch the latest
            published release, or a tag name (e.g. ``jq-1.6``).

        Returns
        -------
        dict
            The JSON response body from the GitHub API.

        Raises
        ------
        RepoNotFoundError
            If the repository does not exist or has no releases.
        VersionNotFoundError
            If the requested tag does not exist in the repository.
        RateLimitError
        NetworkError
        """
        if version == "latest":
            path = f"/repos/{repo}/releases/latest"
        else:
            path = f"/repos/{repo}/releases/tags/{version}"

        response = self._request("GET", f"{self.base_url}{path}")

        if response.status_code == 404:
            if version == "latest":
                raise RepoNotFoundError(
                    f"Repository '{repo}' not found or has no published releases."
                )
            raise VersionNotFoundError(
                f"Version/tag '{version}' not found in repository '{repo}'."
            )

        response.raise_for_status()
        return response.json()

    def get_assets(self, repo: str, release_id: int) -> List[dict]:
        """Return *all* assets belonging to a release (handles pagination).

        Parameters
        ----------
        repo:
            Repository identifier in ``owner/name`` format.
        release_id:
            Numeric release ID as returned by :meth:`get_release`.

        Returns
        -------
        list[dict]
        """
        path = f"/repos/{repo}/releases/{release_id}/assets"
        result = self._get_api_paginated(path)
        assert isinstance(result, list)
        return result

    @staticmethod
    def get_asset_download_url(asset: dict) -> str:
        """Return the ``browser_download_url`` from an asset dict."""
        return asset["browser_download_url"]

    # -- Download -----------------------------------------------------------

    def download_asset(
        self,
        url: str,
        dest_path: str,
        resume_bytes: int = 0,
    ) -> str:
        """Stream a file from *url* to *dest_path*.

        Parameters
        ----------
        url:
            Download URL (usually from :meth:`get_asset_download_url`).
        dest_path:
            Local filesystem path to write to.
        resume_bytes:
            If non-zero, set ``Range: bytes={resume_bytes}-`` so the server
            resumes from that offset, and the file is opened in append mode.

        Returns
        -------
        str
            *dest_path* on success.

        Raises
        ------
        NetworkError
        """
        headers: Dict[str, str] = {}
        mode = "wb"
        if resume_bytes > 0:
            headers["Range"] = f"bytes={resume_bytes}-"
            mode = "ab"

        try:
            response = self._session.get(url, stream=True, headers=headers)
            response.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            raise NetworkError(
                f"Failed to connect for download: {e}"
            ) from e
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Download failed: {e}") from e

        with open(dest_path, mode) as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return dest_path

    # -- Internal request helpers ------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Low-level request with transport-level error handling.

        Raises
        ------
        NetworkError
            On connection refused, DNS failure, timeout, etc.
        RateLimitError
            When GitHub responds with 429 or 403 + zero remaining quota.
        GitHubError
            When GitHub responds with 401 (unauthorized) or 403 without
            rate-limit exhaustion (authentication required).
        """
        try:
            response = self._session.request(method, url, **kwargs)
        except requests.exceptions.ConnectionError as e:
            raise NetworkError(f"Connection failed: {e}") from e
        except requests.exceptions.Timeout as e:
            raise NetworkError(f"Request timed out: {e}") from e
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Request failed: {e}") from e

        # -- Auth / rate-limit detection -----------------------------------
        if response.status_code == 401:
            raise GitHubError(
                "GitHub API requires authentication. "
                "Set the GITHUB_TOKEN environment variable.\n"
                "Create a token at: https://github.com/settings/tokens"
            )

        if response.status_code == 429:
            self._raise_rate_limit(response)

        if response.status_code == 403:
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None and remaining == "0":
                self._raise_rate_limit(response)
            # 403 without rate limit exhaustion = auth required
            raise GitHubError(
                "GitHub API returned 403 Forbidden. "
                "This usually means authentication is required.\n"
                "Set the GITHUB_TOKEN environment variable.\n"
                "Create a token at: https://github.com/settings/tokens"
            )

        return response

    def _get_api_paginated(self, path: str) -> Any:
        """GET an API endpoint, following ``Link`` headers for pagination.

        For list responses, all pages are accumulated and returned as a
        single list.  For single-object responses (no pagination), the
        parsed JSON is returned directly.
        """
        url = f"{self.base_url}{path}"
        params: Dict[str, Any] = {"per_page": 100}
        collected: List[Any] = []

        # One iteration to detect list vs. single-object response.
        response = self._request("GET", url, params=params)
        data = response.json()
        is_list = isinstance(data, list)

        if is_list:
            collected.extend(data)
            next_url = _parse_next_link(response.headers.get("Link", ""))
            while next_url:
                response = self._request("GET", next_url)
                page = response.json()
                if isinstance(page, list):
                    collected.extend(page)
                next_url = _parse_next_link(response.headers.get("Link", ""))
            return collected

        # Single object -- return as-is (no pagination needed).
        return data

    @staticmethod
    def _raise_rate_limit(response: requests.Response) -> None:
        """Raise a :class:`RateLimitError` with a helpful message."""
        reset_epoch = int(response.headers.get("X-RateLimit-Reset", "0"))
        if reset_epoch:
            reset_str = time.strftime(
                "%Y-%m-%d %H:%M:%S UTC", time.gmtime(reset_epoch)
            )
        else:
            reset_str = "unknown"

        raise RateLimitError(
            f"GitHub API rate limit exceeded. "
            f"Resets at {reset_str}. "
            f"Set the GITHUB_TOKEN environment variable for a higher limit."
        )


# -- Module-level helpers ---------------------------------------------------

def _parse_next_link(link_header: str) -> Optional[str]:
    """Extract the ``rel="next"`` URL from a ``Link`` header, if present.

    Example header::

        <https://api.github.com/...?page=2>; rel="next",
        <https://api.github.com/...?page=5>; rel="last"
    """
    if not link_header:
        return None

    for part in link_header.split(","):
        part = part.strip()
        if ";" not in part:
            continue
        segment, rel_part = part.split(";", 1)
        rel_value = rel_part.strip()
        if 'rel="next"' in rel_value or "rel='next'" in rel_value:
            url = segment.strip()
            if url.startswith("<") and url.endswith(">"):
                return url[1:-1]
    return None
