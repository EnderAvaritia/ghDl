"""
Cross-process directory lock for gh-downloader.

Implements :class:`DirLock`, a small advisory file lock used to serialise
``gh-dl`` processes that write into the same output directory.  The lock is
backed by an OS-level advisory lock (``msvcrt.locking`` on Windows,
``fcntl.flock`` elsewhere) on byte 0 of a dedicated lock file.

Lock files are kept **out of the download directory** and centralised in a
per-user lock directory under gh-dl's global config directory (see
:func:`lock_root`), one file per locked scope named after the SHA-256 of the
scope's absolute path (see :func:`lock_path_for`).  This keeps download
output clean while still serialising every process that targets the same
scope.

A lock file is intentionally **never deleted**: removing it would allow a
second process to create a fresh inode and lock that, defeating the whole
point of the lock.  The file is truncated and stamped with the owning PID
so it remains useful for debugging.
"""

from __future__ import annotations

import errno
import hashlib
import os
import time
from pathlib import Path
from typing import Callable, Optional

from gh_downloader.config import global_user_config_path


#: Sub-directory (under gh-dl's global config dir) holding lock files.
LOCKS_DIRNAME = "locks"

#: Suffix for generated lock file names.
LOCK_SUFFIX = ".lock"

#: Environment variable overriding the lock directory (mainly for tests).
LOCK_DIR_ENV = "GH_DL_LOCK_DIR"


# ---------------------------------------------------------------------------
# Platform-specific primitives (imported lazily so this module imports on any OS)
# ---------------------------------------------------------------------------


def _lock_fd(fd: int) -> None:
    """Try to take a non-blocking exclusive advisory lock on *fd*.

    Parameters
    ----------
    fd:
        Open file descriptor for the lock file.

    Raises
    ------
    OSError
        When the lock is held by another process/handle (contention) or any
        other operating-system error occurs.
    """
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: int) -> None:
    """Release the advisory lock held on *fd*, ignoring failures in callers."""
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


#: errno values that mean "somebody else holds the lock" on either platform.
_BUSY_ERRNOS = frozenset(
    value
    for value in (
        13,  # EACCES (Windows and POSIX)
        36,  # EDEADLK (Windows)
        getattr(errno, "EACCES", -1),
        getattr(errno, "EAGAIN", -1),
        getattr(errno, "EDEADLK", -1),
    )
    if value >= 0
)


def _is_busy(exc: OSError) -> bool:
    """Return ``True`` when *exc* represents lock contention (not a real error).

    Parameters
    ----------
    exc:
        The :class:`OSError` raised by the platform locking primitive.

    Returns
    -------
    bool
        ``True`` for contention errors that should be retried.
    """
    if isinstance(exc, BlockingIOError):
        return True
    return exc.errno in _BUSY_ERRNOS


# ---------------------------------------------------------------------------
# Lock location
# ---------------------------------------------------------------------------


def lock_root() -> str:
    """Return the directory that holds cross-process lock files.

    By default this is ``locks/`` inside gh-dl's global config directory:

    * Windows: ``%APPDATA%/gh-dl/locks``
    * Linux/macOS: ``$XDG_CONFIG_HOME/gh-dl/locks`` (or
      ``~/.config/gh-dl/locks``)

    The directory is created if missing.  Set the ``GH_DL_LOCK_DIR``
    environment variable to override the location.

    Returns
    -------
    str
        Absolute path to the lock directory.
    """
    override = os.environ.get(LOCK_DIR_ENV)
    if override:
        base: Path = Path(override).expanduser()
    else:
        base = global_user_config_path().parent / LOCKS_DIRNAME
    root = os.path.abspath(os.fspath(base))
    os.makedirs(root, exist_ok=True)
    return root


def lock_path_for(scope: str | os.PathLike[str]) -> str:
    """Map a lock *scope* to its lock-file path inside :func:`lock_root`.

    The *scope* is the directory whose writers must be serialised (in
    hierarchy mode the ``owner/repo/version`` directory, in flat mode the
    output root).  The file name is the SHA-256 of the scope's normalised
    absolute path, so every process targeting the same scope maps to the same
    lock file while different scopes never collide.

    Parameters
    ----------
    scope:
        Directory that defines the locking scope.  It does not need to exist.

    Returns
    -------
    str
        Path to the lock file (the file itself is created on acquire).
    """
    scope_key = os.path.normcase(os.path.abspath(os.fspath(scope)))
    digest = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()
    return os.path.join(lock_root(), digest + LOCK_SUFFIX)


# ---------------------------------------------------------------------------
# DirLock
# ---------------------------------------------------------------------------


class DirLock:
    """Advisory cross-process lock on a directory's lock file.

    Parameters
    ----------
    lock_path:
        Path to the lock file, normally produced by :func:`lock_path_for`.
    """

    def __init__(self, lock_path: str | os.PathLike[str]) -> None:
        self._path: str = os.fspath(lock_path)
        self._fd: Optional[int] = None

    @property
    def path(self) -> str:
        """The lock file path this instance operates on."""
        return self._path

    def acquire(
        self,
        on_wait: Optional[Callable[[str], None]] = None,
        poll_interval: float = 0.2,
    ) -> None:
        """Block until the lock is acquired.

        The lock is first attempted non-blocking.  On contention the optional
        *on_wait* callback is invoked exactly once with :attr:`path`, after
        which the method retries every *poll_interval* seconds.

        Parameters
        ----------
        on_wait:
            Optional callback notified the first time the lock is found busy.
            It receives the lock file path.
        poll_interval:
            Delay in seconds between retry attempts.

        Raises
        ------
        OSError
            Propagated unchanged if the lock file cannot be created or the
            platform lock fails for a reason other than contention.
        KeyboardInterrupt
            Propagated if the user interrupts while waiting; the descriptor is
            closed first.
        """
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)

        notified = False
        try:
            while True:
                try:
                    _lock_fd(fd)
                except OSError as exc:
                    if not _is_busy(exc):
                        raise
                    if not notified:
                        notified = True
                        if on_wait is not None:
                            on_wait(self._path)
                    time.sleep(poll_interval)
                    continue
                break
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

        # Best-effort: record the owning PID for human debugging.
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            pass

        self._fd = fd

    def release(self) -> None:
        """Release the lock.

        Idempotent and never raises.  The lock file is deliberately **not**
        deleted: deleting it would let a second process create a different
        inode and lock that, which is exactly the race this class prevents.
        """
        fd = self._fd
        if fd is None:
            return
        self._fd = None

        try:
            _unlock_fd(fd)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> "DirLock":
        """Acquire the lock and return this instance."""
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Release the lock when leaving a ``with`` block."""
        self.release()