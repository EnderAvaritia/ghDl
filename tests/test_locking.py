"""
Tests for the cross-process directory lock (``gh_downloader.locking``).
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from gh_downloader.locking import LOCK_FILENAME, DirLock


# -- Basic lifecycle ---------------------------------------------------------


class TestDirLockLifecycle:
    def test_acquire_release_reacquire(self, tmp_path):
        lock_path = tmp_path / LOCK_FILENAME
        lock = DirLock(lock_path)
        assert lock.path == str(lock_path)

        lock.acquire()
        assert lock_path.exists()
        lock.release()

        lock.acquire()
        lock.release()

    def test_context_manager(self, tmp_path):
        lock_path = tmp_path / LOCK_FILENAME
        with DirLock(lock_path) as lock:
            assert lock.path == str(lock_path)
            assert lock_path.exists()
        # Releasing must not delete the file (deleting would defeat the lock).
        assert lock_path.exists()

    def test_release_is_idempotent(self, tmp_path):
        lock = DirLock(tmp_path / LOCK_FILENAME)
        lock.acquire()
        lock.release()
        lock.release()  # must not raise

    def test_release_without_acquire_is_noop(self, tmp_path):
        DirLock(tmp_path / LOCK_FILENAME).release()


# -- Contention --------------------------------------------------------------


class TestDirLockContention:
    def test_second_acquirer_waits_and_is_notified_once(self, tmp_path):
        """A lock held by another handle must block until released.

        Two handles in the same process are used.  Both ``flock`` (POSIX)
        and ``LockFile`` (Windows, via ``msvcrt.locking``) are per-handle,
        so a second ``os.open`` on the same path conflicts as it would for
        a second process.
        """
        lock_path = tmp_path / LOCK_FILENAME

        first = DirLock(lock_path)
        first.acquire()

        calls: list[str] = []
        notified = threading.Event()
        acquired = threading.Event()
        worker_locks: list[DirLock] = []

        def on_wait(path: str) -> None:
            calls.append(path)
            notified.set()

        def worker() -> None:
            second = DirLock(lock_path)
            second.acquire(on_wait=on_wait, poll_interval=0.02)
            worker_locks.append(second)
            acquired.set()

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            assert notified.wait(5), "on_wait callback was never invoked"
            assert calls == [str(lock_path)]
            assert thread.is_alive(), "second acquirer did not block"
            assert not acquired.is_set()

            first.release()
            thread.join(5)
            assert not thread.is_alive()
            assert acquired.is_set()
        finally:
            if thread.is_alive():
                first.release()
                thread.join(5)
            for held in worker_locks:
                held.release()

        # Give any stray retry a moment; the callback must still be exactly 1.
        time.sleep(0.05)
        assert calls == [str(lock_path)]

    def test_keyboard_interrupt_while_waiting_releases_fd(self, tmp_path):
        """KeyboardInterrupt during the wait must close the fd, not leak it."""
        lock_path = tmp_path / LOCK_FILENAME

        first = DirLock(lock_path)
        first.acquire()

        second = DirLock(lock_path)

        def raise_interrupt(_path: str) -> None:
            raise KeyboardInterrupt

        try:
            with pytest.raises(KeyboardInterrupt):
                # Bypass retries: the callback raises before the first sleep.
                second.acquire(on_wait=raise_interrupt, poll_interval=0.01)
        finally:
            first.release()

        # A fresh lock must now be acquirable (no leaked OS lock/handle).
        fresh = DirLock(lock_path)
        fresh.acquire()
        fresh.release()

    def test_lock_file_survives_release(self, tmp_path):
        lock_path = tmp_path / LOCK_FILENAME
        with DirLock(lock_path):
            pass
        assert os.path.exists(lock_path)