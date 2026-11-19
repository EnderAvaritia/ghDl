"""
Cross-process directory lock for gh-downloader.

Implements :class:`DirLock`, a small advisory file lock used to serialise
``gh-dl`` processes that write into the same output directory.  The lock is
backed by an OS-level advisory lock (``msvcrt.locking`` on Windows,
``fcntl.flock`` elsewhere) on byte 0 of a dedicated lock file.

The lock file is intentionally **never deleted**: removing it would allow a
second process to create a fresh inode and lock that, defeating the whole
point of the lock.  The file is truncated and stamped with the owning PID
so it remains useful for debugging.

Only the Python standard library is used.
"""

from __future__ import annotations

import errno
import os
import time
from typing import Callable, Optional


#: Name of the lock file created inside an output directory.
LOCK_FILENAME = ".gh-dl.lock"


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
# DirLock
# ---------------------------------------------------------------------------


class DirLock:
    """Advisory cross-process lock on a directory's lock file.

    Parameters
    ----------
    lock_path:
        Path to the lock file, normally
        ``os.path.join(output_dir, LOCK_FILENAME)``.
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