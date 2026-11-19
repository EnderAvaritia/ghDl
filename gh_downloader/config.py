"""
Configuration parser for gh-downloader.

Loads and validates repository configurations from JSON and YAML files.
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RepoConfig:
    """Configuration for a single GitHub repository to download from."""

    owner: str
    repo: str
    pattern: str
    version: str = "latest"
    output: str | None = None
    sources: bool = True
    provider: str | None = None


@dataclass
class ConfigData:
    """Top-level parsed configuration."""

    repos: list[RepoConfig] = field(default_factory=list)


class ConfigError(Exception):
    """Raised when a configuration is invalid or cannot be parsed."""

    pass


def detect_format(path: str) -> str:
    """Return ``"json"`` or ``"yaml"`` based on file extension."""
    ext = Path(path).suffix.lower()
    if ext in (".yaml", ".yml"):
        return "yaml"
    return "json"


def _load_json(path: str) -> dict[str, Any]:
    """Load and return JSON from *path*."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_yaml(path: str) -> dict[str, Any]:
    """Load and return YAML from *path*."""
    try:
        import yaml
    except ImportError:
        raise ConfigError(
            "YAML support requires pyyaml: pip install pyyaml"
        ) from None
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"YAML file '{path}' must contain a top-level mapping")
    return data


def validate_config(data: dict[str, Any]) -> ConfigData:
    """Validate a parsed config dictionary and return a ``ConfigData``.

    Parameters
    ----------
    data:
        The raw dictionary parsed from JSON / YAML.

    Returns
    -------
    ConfigData
        Validated configuration object.

    Raises
    ------
    ConfigError
        If any required fields are missing, empty, or the repo list is empty.
    """
    repos_raw = data.get("repos", [])
    if not isinstance(repos_raw, list):
        raise ConfigError('"repos" must be a list')
    if len(repos_raw) == 0:
        raise ConfigError("No repositories defined")

    known_fields = {
        "owner", "repo", "pattern", "version", "output", "sources", "provider",
    }
    repos: list[RepoConfig] = []

    for i, entry in enumerate(repos_raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"repo entry #{i} must be an object")

        # Warn about unknown fields
        for key in entry:
            if key not in known_fields:
                warnings.warn(
                    f"repo #{i}: unknown field '{key}' will be ignored"
                )

        owner = entry.get("owner")
        repo = entry.get("repo")
        pattern = entry.get("pattern")

        if not owner or not isinstance(owner, str):
            raise ConfigError(
                f"repo #{i}: 'owner' is required and must be a non-empty string"
            )
        if not repo or not isinstance(repo, str):
            raise ConfigError(
                f"repo #{i}: 'repo' is required and must be a non-empty string"
            )
        if not pattern or not isinstance(pattern, str):
            raise ConfigError(
                f"repo #{i}: 'pattern' is required and must be a non-empty string"
            )

        version = entry.get("version", "latest")
        if version is not None and not isinstance(version, str):
            raise ConfigError(f"repo #{i}: 'version' must be a string")
        if version is None:
            version = "latest"

        output = entry.get("output")
        if output is not None and not isinstance(output, str):
            raise ConfigError(f"repo #{i}: 'output' must be a string")

        sources = entry.get("sources", True)
        if not isinstance(sources, bool):
            raise ConfigError(f"repo #{i}: 'sources' must be a boolean")

        provider = entry.get("provider")
        if provider is not None:
            if not isinstance(provider, str):
                raise ConfigError(
                    f"repo #{i}: 'provider' must be 'github' or 'gitlab'"
                )
            provider = provider.strip().lower()
            if provider not in ("github", "gitlab"):
                raise ConfigError(
                    f"repo #{i}: 'provider' must be 'github' or 'gitlab'"
                )

        repos.append(
            RepoConfig(
                owner=owner.strip(),
                repo=repo.strip(),
                pattern=pattern.strip(),
                version=version.strip() if version else "latest",
                output=output.strip() if output else None,
                sources=sources,
                provider=provider,
            )
        )

    return ConfigData(repos=repos)


def load_config(path: str) -> ConfigData:
    """Load and validate a configuration file.

    Automatically detects JSON vs YAML by file extension.

    Parameters
    ----------
    path:
        Path to the configuration file (``.json``, ``.yaml``, or ``.yml``).

    Returns
    -------
    ConfigData
        Validated configuration object.

    Raises
    ------
    ConfigError
        On invalid config, missing file, or parse errors.
    FileNotFoundError
        If the file does not exist.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    fmt = detect_format(path)

    if fmt == "yaml":
        raw = _load_yaml(path)
    else:
        raw = _load_json(path)

    return validate_config(raw)


def create_example_config(path: str) -> None:
    """Write a well-commented example configuration file (JSON format).

    Parameters
    ----------
    path:
        Destination path for the example config file.
    """
    example = {
        "//_comment": (
            "gh-downloader configuration file.\n"
            "Define the repositories and assets you want to download below.\n"
            "Supported fields per entry:\n"
            "  owner   - GitHub user or organisation name (required)\n"
            "  repo    - Repository name (required)\n"
            "  pattern - Glob (or regex with --regex) pattern to match asset filenames (required)\n"
            "  version - Release tag or 'latest' (optional, default 'latest')\n"
            "  output  - Download directory (optional, default: current dir)\n"
            "  sources - Whether to also download source code archives (optional, default true)\n"
            "  provider - Release provider: 'github' or 'gitlab' (optional, auto-detect)"
        ),
        "repos": [
            {
                "owner": "jeffrey-xuan",
                "repo": "some-project",
                "pattern": "*.zip",
                "version": "latest",
                "output": "./downloads",
            },
            {
                "owner": "microsoft",
                "repo": "vscode",
                "pattern": "*.exe",
                "version": "1.85.0",
            },
        ],
    }

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(example, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------------------
# User-level config (persistent token / proxy settings)
# ---------------------------------------------------------------------------


@dataclass
class UserConfig:
    """Persistent user-level configuration.

    Stored in a JSON file on disk and applied before any API calls.
    Environment variables take precedence over these values.
    """

    github_token: str | None = None
    http_proxy: str | None = None
    https_proxy: str | None = None
    download_sources: bool = True
    gitlab_token: str | None = None
    gitlab_url: str | None = None
    github_api_url: str | None = None
    limit_rate: str | int | None = None
    ghproxy: str | None = None


def global_user_config_path() -> Path:
    """Return the platform-specific global user config path.

    Windows: ``%APPDATA%/gh-dl/config.json`` (falls back to
    ``~/AppData/Roaming/gh-dl/config.json`` when ``APPDATA`` is unset).
    Linux/macOS: ``$XDG_CONFIG_HOME/gh-dl/config.json`` when set, otherwise
    ``~/.config/gh-dl/config.json``.

    Returns
    -------
    Path
        The global user config file path (the file may not exist yet).
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "gh-dl" / "config.json"


def _user_config_paths() -> list[Path]:
    """Return user config file paths in priority order (first found wins)."""
    return [
        # 1. Current-directory .gh-dl.json
        Path(".gh-dl.json").resolve(),
        # 2. Platform-specific global config dir
        global_user_config_path(),
        # 3. Home-directory dotfile
        Path.home() / ".gh-dl.json",
    ]


def load_user_config() -> UserConfig:
    """Load user-level config from the first existing config file.

    Searches in order:
    1. ``.gh-dl.json`` in the current working directory.
    2. ``%APPDATA%/gh-dl/config.json`` (Windows) or
       ``~/.config/gh-dl/config.json`` (Unix).
    3. ``~/.gh-dl.json``.

    Returns an empty :class:`UserConfig` if no file is found or none can
    be parsed.
    """
    for path in _user_config_paths():
        if path.is_file():
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)

                raw_limit_rate = data.get("limit_rate")
                if isinstance(raw_limit_rate, str):
                    limit_rate: str | int | None = (
                        raw_limit_rate.strip() or None
                    )
                elif isinstance(raw_limit_rate, int):
                    limit_rate = raw_limit_rate
                else:
                    limit_rate = None

                raw_ghproxy = data.get("ghproxy")
                ghproxy = (
                    raw_ghproxy.strip() or None
                    if isinstance(raw_ghproxy, str)
                    else None
                )

                return UserConfig(
                    github_token=data.get("github_token"),
                    http_proxy=data.get("http_proxy"),
                    https_proxy=data.get("https_proxy"),
                    download_sources=data.get("download_sources", True),
                    gitlab_token=data.get("gitlab_token"),
                    gitlab_url=data.get("gitlab_url"),
                    github_api_url=data.get("github_api_url"),
                    limit_rate=limit_rate,
                    ghproxy=ghproxy,
                )
            except (json.JSONDecodeError, OSError):
                continue
    return UserConfig()


def create_user_config_example(
    path: str | None = None,
    *,
    global_: bool = False,
    overwrite: bool = False,
) -> str:
    """Write an example user config JSON file.

    Parameters
    ----------
    path:
        Destination path.  If ``None``, uses
        :func:`global_user_config_path` when *global_* is ``True``; otherwise
        uses the first path from :func:`_user_config_paths`
        (``.gh-dl.json`` in the CWD).
    global_:
        When ``True`` and *path* is ``None``, write to the platform global
        user config path.
    overwrite:
        When ``False`` (default) and the target file already exists, raise
        :class:`FileExistsError` instead of overwriting it.

    Returns
    -------
    str
        The path that was written to.

    Raises
    ------
    FileExistsError
        If the target file exists and *overwrite* is ``False``.  The existence
        check and the write are atomic, so concurrent creators cannot both
        succeed.
    """
    if path is None:
        path = (
            str(global_user_config_path())
            if global_
            else str(_user_config_paths()[0])
        )

    example = {
        "//_comment": (
            "gh-dl user configuration file.\n"
            "Settings here persist across terminal sessions.\n"
            "All fields are optional; leave blank or omit to use env vars."
        ),
        "github_token": "your_github_token_here",
        "http_proxy": "http://127.0.0.1:7890",
        "https_proxy": "http://127.0.0.1:7890",
        "download_sources": True,
        "gitlab_token": "your_gitlab_token_here",
        "gitlab_url": "https://gitlab.com",
        "github_api_url": "https://api.github.com",
        "limit_rate": "",
        "ghproxy": "",
    }

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if overwrite:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(example, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return path

    try:
        fh = open(path, "x", encoding="utf-8")
    except FileExistsError:
        raise FileExistsError(path) from None
    with fh:
        json.dump(example, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path
