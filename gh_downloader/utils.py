"""
Utility functions for gh-downloader.

Provides helper functions for filename sanitization, path management,
human-readable formatting, signal handling, and platform detection.
All functions use only the Python standard library.
"""

import os
import shutil
import signal
import sys
import threading
import unicodedata
import urllib.parse

# ---------------------------------------------------------------------------
# Terminal display helpers
# ---------------------------------------------------------------------------


def _char_width(char: str) -> int:
    """Return the number of terminal columns occupied by *char*."""
    if unicodedata.combining(char) or unicodedata.category(char) in ("Cc", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1


def display_width(text: str) -> int:
    """Return the number of terminal columns needed to display *text*.

    East-Asian wide/fullwidth characters count as two columns, combining
    marks and control characters as zero.  This is an approximation (it does
    not implement the Unicode grapheme-cluster algorithm), but it is accurate
    enough for progress lines containing ASCII and CJK text.

    Args:
        text: The text to measure.

    Returns:
        Display width in terminal columns.
    """
    return sum(_char_width(char) for char in text)


def truncate_to_width(text: str, max_width: int, ellipsis: str = "...") -> str:
    """Truncate *text* so its :func:`display_width` does not exceed *max_width*.

    If truncation is needed the result ends with *ellipsis*.  When even the
    ellipsis does not fit, a hard cut without ellipsis is returned.

    Args:
        text: The text to truncate.
        max_width: Maximum display width in terminal columns.
        ellipsis: Marker appended to truncated text (default ``"..."``).

    Returns:
        The original text when it fits, otherwise a width-limited prefix.
    """
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text

    ellipsis_width = display_width(ellipsis)
    if ellipsis_width >= max_width:
        return _take_width(text, max_width)
    return _take_width(text, max_width - ellipsis_width) + ellipsis


def _take_width(text: str, max_width: int) -> str:
    """Return the longest prefix of *text* fitting within *max_width*."""
    result: list[str] = []
    used = 0
    for char in text:
        char_width = _char_width(char)
        if used + char_width > max_width:
            break
        result.append(char)
        used += char_width
    return "".join(result)


def fit_line(prefix: str, text: str, suffix: str, max_width: int) -> str:
    """Fit ``prefix + text + suffix`` into *max_width* display columns.

    *text* (typically a file name) is the only part that gets truncated, so
    important trailing information such as percentage, size and speed is
    preserved even when the name is very long.

    Args:
        prefix: Fixed leading text.
        text: Variable-length middle text.
        suffix: Fixed trailing text.
        max_width: Maximum display width in terminal columns.

    Returns:
        A line whose display width is at most *max_width*.
    """
    if max_width <= 0:
        return ""
    budget = max_width - display_width(prefix) - display_width(suffix)
    if budget >= len("..."):
        return prefix + truncate_to_width(text, budget) + suffix
    return truncate_to_width(prefix + text + suffix, max_width)


def terminal_width(minimum: int = 20) -> int:
    """Return the current terminal width in columns, with a sane floor.

    Falls back to the default size (80 columns) when stdout is not a
    terminal.  ``minimum`` avoids degenerate layouts on tiny/zero-width
    terminals.

    Args:
        minimum: Smallest width to return (default 20).

    Returns:
        Terminal width in columns.
    """
    columns = shutil.get_terminal_size().columns
    return max(columns, minimum)


# ---------------------------------------------------------------------------
# Filename / path helpers
# ---------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    """Replace characters unsafe for Windows filenames with underscores.

    The unsafe characters are ``\\ / : " < > | ? *``.  All other characters
    (including Unicode letters, digits, symbols) are kept as-is.

    Args:
        name: The original string (e.g. a release asset name).

    Returns:
        A string with every unsafe character replaced by ``_``.
    """
    unsafe_chars = '\\/:*?"<>|'
    table = str.maketrans(unsafe_chars, "_" * len(unsafe_chars))
    return name.translate(table)


def ensure_dir(path: str) -> str:
    """Create directory at *path* if it does not already exist.

    Has no effect if the directory already exists (``exist_ok=True``).

    Args:
        path: Filesystem path to ensure as a directory.

    Returns:
        The same *path* string (for convenient chaining).
    """
    os.makedirs(path, exist_ok=True)
    return path


def build_output_path(
    base_dir: str,
    owner: str,
    repo: str,
    version: str,
    asset_name: str,
    flat: bool = False,
) -> str:
    """Build a full output file path for a downloaded asset.

    When *flat* is ``False`` (default) the path follows the pattern::

        base_dir / owner / repo / version / asset_name

    When *flat* is ``True`` the path is simply::

        base_dir / asset_name

    In flat mode a warning is printed to stderr if two different assets
    map to the same filename (collision), but the path is still returned.

    Args:
        base_dir: Root download directory.
        owner: Repository owner (GitHub username or organisation).
        repo: Repository name.
        version: Release version or tag.
        asset_name: Filename of the release asset.
        flat: If ``True``, ignore owner/repo/version hierarchy.

    Returns:
        The resolved absolute file path.
    """
    if flat:
        # In flat mode every asset lives directly under base_dir.
        # We deliberately do *not* make sub-directories, so collisions
        # are possible -- warn the user once per collision.
        path = os.path.join(base_dir, asset_name)
        if os.path.exists(path):
            print(
                f"Warning: flat output collision -- {asset_name!r} already exists at {path!r}",
                file=sys.stderr,
            )
        return try_enable_long_paths(path)

    path = os.path.join(base_dir, owner, repo, version, asset_name)
    return try_enable_long_paths(path)


# ---------------------------------------------------------------------------
# Human-readable formatting
# ---------------------------------------------------------------------------

_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def format_size(bytes_: int) -> str:
    """Format a byte count as a human-readable string.

    Uses 1024-based (binary) units::

        1023       -> "1023 B"
        2048       -> "2.00 KB"
        1234567    -> "1.18 MB"
        3221225472 -> "3.00 GB"

    Args:
        bytes_: Size in bytes (non-negative).

    Returns:
        Formatted string with an appropriate unit suffix.

    Raises:
        ValueError: If *bytes_* is negative.
    """
    if bytes_ < 0:
        raise ValueError(f"bytes_ must be non-negative, got {bytes_}")

    if bytes_ == 0:
        return "0 B"

    magnitude = 0
    remaining = float(bytes_)
    while remaining >= 1024 and magnitude < len(_SIZE_UNITS) - 1:
        remaining /= 1024
        magnitude += 1

    if magnitude == 0:
        return f"{bytes_} B"

    return f"{remaining:.2f} {_SIZE_UNITS[magnitude]}"


def format_speed(bytes_per_sec: float) -> str:
    """Format a transfer speed as a human-readable string.

    Examples::

        500    -> "500.0 B/s"
        51200  -> "50.0 KB/s"
        5242880 -> "5.0 MB/s"

    Args:
        bytes_per_sec: Transfer rate in bytes/second (non-negative).

    Returns:
        Formatted string with an appropriate unit suffix.

    Raises:
        ValueError: If *bytes_per_sec* is negative.
    """
    if bytes_per_sec < 0:
        raise ValueError(
            f"bytes_per_sec must be non-negative, got {bytes_per_sec}"
        )

    if bytes_per_sec == 0:
        return "0 B/s"

    magnitude = 0
    remaining = float(bytes_per_sec)
    while remaining >= 1024 and magnitude < len(_SIZE_UNITS) - 1:
        remaining /= 1024
        magnitude += 1

    unit = _SIZE_UNITS[magnitude]
    if magnitude == 0:
        return f"{remaining:.1f} B/s"
    return f"{remaining:.1f} {unit}/s"


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def setup_signal_handler() -> threading.Event:
    """Register a ``SIGINT`` handler that sets a ``threading.Event``.

    The returned event is cleared on entry.  When the user presses
    ``Ctrl+C`` the handler sets the event, allowing long-running
    operations to poll it and shut down gracefully.

    On Windows ``signal.signal(signal.SIGINT, ...)`` works as expected.
    This function does **not** attempt to handle ``SIGTERM`` or platform-
    specific signals.

    Returns:
        A :class:`threading.Event` that will be set when SIGINT is received.
    """
    stop_event = threading.Event()
    stop_event.clear()

    def _handler(signum: object, _frame: object) -> None:
        """Internal SIGINT handler."""
        _ = signum
        stop_event.set()

    _ = signal.signal(signal.SIGINT, _handler)
    return stop_event


# ---------------------------------------------------------------------------
# Repository string parsing
# ---------------------------------------------------------------------------

def _detect_provider_from_host(host: str, gitlab_url: str | None) -> str:
    """Infer ``"github"`` or ``"gitlab"`` from a URL *host*.

    This duplicates the tiny host rule from
    :func:`gh_downloader.providers.resolve_provider` on purpose: importing
    the providers package here would create a circular import, and this
    module is intentionally dependency-free.
    """
    try:
        host = (urllib.parse.urlsplit("//" + host).hostname or host).lower()
    except ValueError:
        host = host.lower()
    if host in ("github.com", "www.github.com", "api.github.com"):
        return "github"
    if "github" in host:
        return "github"
    if host == "gitlab.com" or "gitlab" in host:
        return "gitlab"
    if gitlab_url:
        try:
            gitlab_host = (urllib.parse.urlsplit(gitlab_url).hostname or "").lower()
        except ValueError:
            gitlab_host = ""
        if gitlab_host and gitlab_host == host:
            return "gitlab"
    return "github"


def parse_repo_string(
    s: str,
    provider: str | None = None,
    gitlab_url: str | None = None,
) -> tuple[str, str]:
    """Extract ``(namespace, repo)`` from a GitHub or GitLab repository string.

    Accepted GitHub input formats::

        "owner/repo"
        "https://github.com/owner/repo"
        "https://github.com/owner/repo.git"
        "http://github.com/owner/repo"
        "http://github.com/owner/repo.git"

    GitLab inputs additionally support nested namespaces::

        "group/sub/project"
        "https://gitlab.com/group/sub/project"
        "https://gitlab.com/group/sub/project/-/releases/v1.2.3"

    The provider is taken from *provider* when given, otherwise detected
    from the URL host (with *gitlab_url* used to recognise self-hosted
    instances).  Plain ``owner/repo`` strings default to GitHub.

    Trailing slashes, ``.git`` suffixes, ``/-/...`` web paths, and
    surrounding whitespace are stripped automatically.

    Args:
        s: A repository identifier string.
        provider: Optional explicit provider (``"github"`` or ``"gitlab"``).
        gitlab_url: Optional self-hosted GitLab base URL used for host
            comparison.

    Returns:
        A ``(owner_or_namespace, repo)`` tuple.

    Raises:
        ValueError: If the string cannot be parsed into a repository.
    """
    s = s.strip()
    if not s:
        raise ValueError("Empty repository string")

    if "://" in s:
        # Take the part after the host (github.com/... or gitlab.com/...)
        after_scheme = s.split("://", 1)[1]
        parts = after_scheme.split("/")
        # parts[0] is the host, parts[1:] is the path
        if len(parts) < 3:
            raise ValueError(
                f"Cannot parse repository URL, expected at least "
                f"'host/owner/repo': {s!r}"
            )
        host = parts[0]
        path = "/".join(parts[1:])
        resolved = provider or _detect_provider_from_host(host, gitlab_url)
    else:
        path = s
        resolved = provider or "github"

    resolved = resolved.strip().lower()

    if resolved == "gitlab":
        # Drop the GitLab web sub-path (e.g. "/-/releases/v1.2.3").
        if "/-/" in path:
            path = path.split("/-/", 1)[0]
        path = path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        path = path.rstrip("/")

        if "/" not in path:
            raise ValueError(
                f"Repository string must contain a '/' separator: {s!r}"
            )

        # GitLab namespaces may be nested: split at the *last* slash.
        owner, repo = path.rsplit("/", 1)
        if not owner or not repo:
            raise ValueError(
                f"Could not extract both namespace and project from: {s!r}"
            )
        return owner, repo

    # GitHub / legacy parsing -- behaviour must stay identical.
    if path.endswith(".git"):
        path = path[:-4]
    path = path.rstrip("/")

    if "/" not in path:
        raise ValueError(
            f"Repository string must contain a '/' separator: {s!r}"
        )

    owner, repo = path.split("/", 1)

    if not owner or not repo:
        raise ValueError(
            f"Could not extract both owner and repo from: {s!r}"
        )

    # If there is anything left after the first '/' (e.g. nested path),
    # repo is only the first component
    if "/" in repo:
        repo = repo.split("/", 1)[0]

    return owner, repo


def parse_full_repo_url(
    s: str,
    provider: str | None = None,
    gitlab_url: str | None = None,
) -> tuple[str, str, str | None]:
    """Extract ``(namespace, repo, version)`` from a repo or release URL.

    Extends :func:`parse_repo_string` with release URL support for both
    providers::

        "https://github.com/owner/repo/releases/tag/v1.2.3"
            -> ("owner", "repo", "v1.2.3")

        "https://github.com/owner/repo/releases/latest"
            -> ("owner", "repo", "latest")

        "https://gitlab.com/group/sub/proj/-/releases/v1.2.3"
            -> ("group/sub", "proj", "v1.2.3")

        "https://gitlab.com/group/sub/proj/-/releases/permalink/latest"
            -> ("group/sub", "proj", "latest")

        "owner/repo"
            -> ("owner", "repo", None)

    Args:
        s: A repository or release URL string.
        provider: Optional explicit provider (``"github"`` or ``"gitlab"``).
        gitlab_url: Optional self-hosted GitLab base URL.

    Returns:
        A ``(owner_or_namespace, repo, version_or_None)`` tuple.

    Raises:
        ValueError: If the string cannot be parsed.
    """
    s = s.strip()
    if not s:
        raise ValueError("Empty repository string")

    version: str | None = None
    lower = s.lower()

    # -- GitLab release URLs: .../-/releases/<tag> or .../-/releases/permalink/latest
    marker = "/-/releases/"
    if marker in lower:
        marker_index = lower.index(marker)
        before = s[:marker_index]
        after = s[marker_index + len(marker):]
        segments = [segment for segment in after.split("/") if segment]
        if len(segments) >= 2 and segments[0] == "permalink" and segments[1] == "latest":
            version = "latest"
        elif segments:
            version = segments[0]
        owner, repo = parse_repo_string(
            before, provider="gitlab", gitlab_url=gitlab_url
        )
        return owner, repo, version

    # -- GitHub release URLs (legacy behaviour, plus trailing-segment trim)
    if "://" in s and "/releases/" in lower:
        before, after = s.rsplit("/releases/", 1)
        if after.startswith("tag/"):
            version = after[4:].split("/", 1)[0]  # strip "tag/" and extras
        else:
            version = after  # "latest" (unchanged legacy behaviour)
        owner, repo = parse_repo_string(
            before, provider=provider, gitlab_url=gitlab_url
        )
        return owner, repo, version

    # Fall back to regular repo parsing (no version in URL)
    owner, repo = parse_repo_string(
        s, provider=provider, gitlab_url=gitlab_url
    )
    return owner, repo, None


# ---------------------------------------------------------------------------
# Platform / environment introspection
# ---------------------------------------------------------------------------

def is_long_path(path: str) -> bool:
    """Check if *path* exceeds the classical Windows MAX_PATH limit (260).

    On non-Windows systems this function always returns ``False`` because
    those platforms do not have a similar arbitrary path-length limit.

    The check uses ``os.name == 'nt'`` to detect Windows.  The limit is
    checked against the absolute form of the path (via
    :func:`os.path.abspath`).

    Args:
        path: A filesystem path string.

    Returns:
        ``True`` if on Windows and the absolute path is longer than 260
        characters.
    """
    if os.name != "nt":
        return False
    return len(os.path.abspath(path)) > 260


# ---------------------------------------------------------------------------
# Long-path helpers (Windows)
# ---------------------------------------------------------------------------

MAX_PATH = 260


def try_enable_long_paths(path: str) -> str:
    """Check if *path* exceeds MAX_PATH and try to enable long-path support.

    On Windows, when the absolute form of *path* exceeds 260 characters, a
    warning is printed to stderr suggesting the use of the ``\\\\?\\``
    prefix or moving the output to a shorter directory.

    If the path is already prefixed with ``\\\\?\\``, no warning is printed.

    On non-Windows systems this function is a no-op (returns *path*
    unchanged).

    Args:
        path: A filesystem path string.

    Returns:
        The path with ``\\\\?\\`` prefix applied on Windows when the
        absolute path exceeds MAX_PATH, or the original *path* otherwise.
    """
    if os.name != "nt":
        return path

    abs_path = os.path.abspath(path)
    if len(abs_path) <= MAX_PATH:
        return path

    # Already has the extended-length prefix?
    prefix = "\\\\?\\"
    if abs_path.startswith(prefix):
        return path

    print(
        f"Warning: Path exceeds {MAX_PATH} characters ({len(abs_path)}).\n"
        + f"  To avoid issues, use a shorter output directory or move\n"
        + f"  the download folder closer to the drive root (e.g. C:\\dl).",
        file=sys.stderr,
    )

    # Return the path with the \\?\ prefix to opt into extended-length
    # path support on Windows 10 (1607+) and Windows 11.
    return prefix + abs_path


def get_terminal_encoding() -> str:
    """Detect the encoding of the current terminal / console.

    Tries, in order:

    1. The value of :func:`sys.stdout.encoding` (preferred).
    2. The value of :func:`sys.stdin.encoding`.
    3. The :envvar:`PYTHONIOENCODING` environment variable.
    4. The value of :func:`locale.getpreferredencoding` (if available).
    5. Falls back to ``"utf-8"``.

    Returns:
        A lower-cased encoding name (e.g. ``"utf-8"``, ``"cp1252"``).
    """
    encoding: str | None = getattr(sys.stdout, "encoding", None)
    if encoding:
        return encoding.lower()

    encoding = getattr(sys.stdin, "encoding", None)
    if encoding:
        return encoding.lower()

    encoding = os.environ.get("PYTHONIOENCODING")
    if encoding:
        return encoding.lower()

    try:
        import locale

        pref = locale.getpreferredencoding()
        if pref:
            return pref.lower()
    except Exception:
        pass

    return "utf-8"


# ---------------------------------------------------------------------------
# Disk space
# ---------------------------------------------------------------------------

def check_disk_space(path: str, required_bytes: int) -> bool:
    """Check whether the filesystem containing *path* has enough free space.

    Uses :func:`shutil.disk_usage` to query available space.  If the path
    does not exist the check is performed on the parent directory instead.

    Args:
        path: Any path on the filesystem to check.
        required_bytes: Number of bytes required.

    Returns:
        ``True`` if the filesystem has at least *required_bytes* available,
        ``False`` otherwise (including when the path cannot be stat-ed).
    """
    try:
        # If the path doesn't exist yet, chdir to its parent so
        # disk_usage can still work.
        if not os.path.exists(path):
            parent = os.path.dirname(path)
            if not parent:
                parent = "."
            usage = shutil.disk_usage(parent)
        else:
            usage = shutil.disk_usage(path)
    except OSError:
        return False

    return usage.free >= required_bytes
