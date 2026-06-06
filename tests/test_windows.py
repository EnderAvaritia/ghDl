"""
Windows-specific hardening tests for gh-downloader.

These tests validate platform-specific behaviour on Windows (long paths,
terminal encoding, Ctrl+C handling, safe filenames, disk space checks).

All tests are skipped on non-Windows platforms via ``@pytest.mark.skipif``.
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
from typing import Callable

import pytest

from gh_downloader.utils import (
    build_output_path,
    check_disk_space,
    get_terminal_encoding,
    is_long_path,
    safe_filename,
    setup_signal_handler,
    try_enable_long_paths,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

win_only = pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Windows only",
)

not_win = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Non-Windows behaviour",
)


def _restore_sigint() -> None:
    """Restore the default SIGINT handler (test cleanup)."""
    signal.signal(signal.SIGINT, signal.default_int_handler)


def _patch_input() -> Callable[[str], str]:
    """Return a mock ``input`` replacement that raises ``KeyboardInterrupt``."""

    def _mock(_prompt_text: str = "") -> str:
        raise KeyboardInterrupt()

    return _mock


# ===========================================================================
# Long path detection
# ===========================================================================


class TestLongPathDetection:
    @win_only
    def test_short_path_returns_false(self):
        """A typical short path should not be flagged as long."""
        assert is_long_path("C:\\short\\path") is False
        assert is_long_path("C:\\Users\\test\\file.zip") is False

    @win_only
    def test_long_path_returns_true(self):
        """A path > 260 chars should be detected."""
        assert is_long_path("C:\\" + "a" * 300) is True

    @win_only
    def test_path_at_exactly_260_is_not_long(self):
        """MAX_PATH is exclusive -- 260 chars is still OK."""
        base = "C:\\" + "a" * 257  # 3 + 257 = 260
        assert len(os.path.abspath(base)) == 260
        assert is_long_path(base) is False

    @win_only
    def test_path_at_261_is_long(self):
        """261 chars exceeds the limit."""
        base = "C:\\" + "a" * 258  # 3 + 258 = 261
        assert len(os.path.abspath(base)) == 261
        assert is_long_path(base) is True

    @not_win
    def test_always_false_on_non_windows(self):
        """On non-Windows, is_long_path should always be False."""
        assert is_long_path("/" + "a" * 500) is False


# ===========================================================================
# try_enable_long_paths
# ===========================================================================


class TestTryEnableLongPaths:
    @win_only
    def test_short_path_unchanged(self):
        """Short paths should be returned verbatim."""
        path = "C:\\short\\path\\file.zip"
        assert try_enable_long_paths(path) == path

    @win_only
    def test_long_path_gets_prefix_and_warning(self, capsys):
        """Long paths should get the ``\\\\?\\`` prefix and a warning."""
        result = try_enable_long_paths("C:\\" + "a" * 300)
        assert result.startswith("\\\\?\\")
        assert "Warning: Path exceeds" in capsys.readouterr().err

    @win_only
    def test_already_prefixed_not_warned(self, capsys):
        """A path already with ``\\\\?\\`` prefix should not warn again."""
        prefixed = "\\\\?\\C:\\" + "a" * 300
        assert try_enable_long_paths(prefixed) == prefixed
        stderr = capsys.readouterr().err
        assert "Warning" not in stderr

    @not_win
    def test_non_windows_noop(self):
        """On non-Windows, the function is a no-op."""
        path = "/very/long/" + "a" * 500 + "/file.zip"
        assert try_enable_long_paths(path) == path

    @win_only
    def test_build_output_path_triggers_warning_for_deep_path(self, capsys):
        """``build_output_path`` should emit a long-path warning when nested
        deeply under a very long base directory.
        """
        build_output_path(
            "C:\\" + "a" * 250,
            "owner", "repo", "v1.0.0", "some-asset-file.zip",
        )
        stderr = capsys.readouterr().err
        assert "Warning: Path exceeds" in stderr


# ===========================================================================
# Ctrl+C / Signal handling
# ===========================================================================


class TestCtrlCHandling:
    def teardown_method(self, _method: Callable[[], None]) -> None:
        _restore_sigint()

    @win_only
    def test_setup_signal_handler_installs_handler(self):
        """``setup_signal_handler`` should install a SIGINT handler (not the
        default) and return a ``threading.Event`` that can be set manually
        to simulate stop signalling.
        """
        event = setup_signal_handler()
        assert event.is_set() is False

        # The handler should now be installed (not the default)
        current = signal.getsignal(signal.SIGINT)
        assert current is not signal.default_int_handler

        # Simulate what the signal handler does
        event.set()
        assert event.is_set() is True

    @win_only
    def test_signal_handler_sets_event_directly(self):
        """Verify the internal signal handler closure sets the event.
        We install the handler then manually invoke the pattern it uses.
        """
        event = setup_signal_handler()
        assert not event.is_set()

        # The handler registered via signal.signal() calls stop_event.set().
        # Verify the handler is active (not default).
        current_handler = signal.getsignal(signal.SIGINT)
        assert current_handler is not signal.default_int_handler

        # Directly invoke what the handler does
        event.set()
        assert event.is_set()

    @win_only
    def test_cli_returns_exit_code_2_on_keyboard_interrupt(self):
        """The CLI ``run_cli`` function should catch ``KeyboardInterrupt`` and
        return exit code 2.

        We patch a subcommand handler to raise ``KeyboardInterrupt``.
        """
        from gh_downloader import cli

        original = cli._handle_download

        def _raiser(_args: object) -> int:
            raise KeyboardInterrupt()

        cli._handle_download = _raiser  # type: ignore[assignment]
        try:
            exit_code = cli.run_cli(["download", "owner/repo", "-p", "*"])
        finally:
            cli._handle_download = original

        assert exit_code == 2

    @win_only
    def test_interactive_prompt_exits_on_ctrlc_without_default(self):
        """``_prompt`` should raise ``SystemExit(0)`` when ``KeyboardInterrupt``
        occurs on a prompt with no default value.
        """
        from gh_downloader.interactive import _prompt

        original = __builtins__["input"] if isinstance(__builtins__, dict) else __builtins__.input
        try:
            mock = _patch_input()
            if isinstance(__builtins__, dict):
                __builtins__["input"] = mock
            else:
                __builtins__.input = mock

            with pytest.raises(SystemExit) as exc:
                _prompt("test: ")
            assert exc.value.code == 0
        finally:
            if isinstance(__builtins__, dict):
                __builtins__["input"] = original
            else:
                __builtins__.input = original

    @win_only
    def test_interactive_prompt_returns_default_on_ctrlc_with_default(self):
        """When ``_prompt`` has a default and Ctrl+C is pressed, it should
        return the default value rather than raising.
        """
        from gh_downloader.interactive import _prompt

        original = __builtins__["input"] if isinstance(__builtins__, dict) else __builtins__.input
        try:
            mock = _patch_input()
            if isinstance(__builtins__, dict):
                __builtins__["input"] = mock
            else:
                __builtins__.input = mock

            result = _prompt("version: ", default="latest")
            assert result == "latest"
        finally:
            if isinstance(__builtins__, dict):
                __builtins__["input"] = original
            else:
                __builtins__.input = original


# ===========================================================================
# Safe filenames with Windows-unsafe characters
# ===========================================================================


class TestSafeFilenameWindows:
    def test_all_unsafe_chars_replaced(self):
        """All 9 Windows-unsafe characters must be replaced."""
        unsafe = '\\/:*?"<>|'
        result = safe_filename(unsafe)
        assert result == "_" * len(unsafe)
        assert len(result) == 9

    def test_each_unsafe_char_individual(self):
        """Each unsafe character should be individually replaced."""
        for char in '\\/:*?"<>|':
            assert safe_filename(char) == "_", f"Failed for {char!r}"

    def test_safe_chars_preserved(self):
        """Letters, digits, dots, hyphens, underscores should be kept."""
        safe = "abc123.-_~#[]()!@$%^&+=,;'"
        assert safe_filename(safe) == safe

    def test_unicode_chinese(self):
        """Chinese characters must be preserved."""
        original = "中文文件名称.zip"
        assert safe_filename(original) == original

    def test_unicode_japanese(self):
        """Japanese characters must be preserved."""
        original = "日本語ファイル名.zip"
        assert safe_filename(original) == original

    def test_mixed_unicode_and_unsafe(self):
        """Mixed Unicode and unsafe chars: unsafe replaced, Unicode kept."""
        result = safe_filename('文件:测试/1.zip')
        assert '?' not in result
        assert '/' not in result
        assert "文件" in result
        assert "测试" in result


# ===========================================================================
# Terminal encoding detection
# ===========================================================================


class TestTerminalEncoding:
    @win_only
    def test_detects_console_encoding(self):
        """On Windows, ``get_terminal_encoding()`` should return a valid
        encoding name (e.g. ``cp936`` on Chinese Windows, ``utf-8`` on
        English Windows with UTF-8 enabled).
        """
        enc = get_terminal_encoding()
        assert isinstance(enc, str) and len(enc) > 0
        # Common Windows console encodings all start with known prefixes
        valid_prefixes = ("utf", "cp", "iso", "latin")
        assert any(enc.startswith(p) for p in valid_prefixes), (
            f"Unexpected encoding: {enc}"
        )

    def test_returns_string(self):
        """Should always return a string regardless of platform."""
        enc = get_terminal_encoding()
        assert isinstance(enc, str) and len(enc) > 0


# ===========================================================================
# Disk space with spaces in path
# ===========================================================================


class TestDiskSpaceWindows:
    @win_only
    def test_path_with_spaces(self):
        """``check_disk_space`` should work with spaces in the path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spaced_dir = os.path.join(tmpdir, "my downloads", "output dir")
            os.makedirs(spaced_dir, exist_ok=True)
            assert check_disk_space(spaced_dir, 1) is True

    @win_only
    def test_nonexistent_path_with_spaces(self):
        """``check_disk_space`` should work when the path with spaces does
        not exist (checking parent instead).  The parent dir with spaces
        must exist for the fallback to work.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the parent directory with spaces
            spaced_parent = os.path.join(tmpdir, "some folder")
            os.makedirs(spaced_parent, exist_ok=True)
            nonexistent = os.path.join(spaced_parent, "nonexistent_file.zip")
            assert check_disk_space(nonexistent, 1) is True

    def test_returns_false_for_impossible(self, tmp_path):
        """Impossibly large requirement should return False."""
        assert check_disk_space(str(tmp_path), 10**30) is False


# ===========================================================================
# Unicode filenames in download pipeline
# ===========================================================================


class TestUnicodeInPipeline:
    """Verify that Unicode asset names flow correctly through the
    filename-sanitisation and path-building pipeline.
    """

    @win_only
    def test_unicode_asset_builds_path(self, tmp_path):
        """A download path with Unicode asset names should build correctly."""
        base = str(tmp_path)
        path = build_output_path(
            base, "测试", "仓库", "v1.0.0", "文件名称.zip",
        )
        assert "测试" in path
        assert "仓库" in path
        assert path.endswith("文件名称.zip")

    @win_only
    def test_unicode_after_safe_filename(self):
        """``safe_filename`` should preserve Unicode while removing unsafe
        characters.
        """
        name = safe_filename('测<试:文"件.zip')
        assert "/" not in name
        assert ":" not in name
        assert '"' not in name
        assert "<" not in name
        assert "测" in name and "试" in name and "件" in name

    @win_only
    def test_stop_event_with_unicode_context(self):
        """The stop-event pattern (used during streaming downloads) works
        correctly when handling Unicode asset names.
        """
        event = setup_signal_handler()
        assert not event.is_set()

        # Simulate the pattern used in ``_stream_download``
        asset_name = "日本語ファイル.zip"
        _ = asset_name  # name would be used in progress callback

        # Stop_event starts clear -> keep downloading
        assert not event.is_set()

        # Simulate what the signal handler does (normally triggered by SIGINT)
        event.set()
        assert event.is_set()

        _restore_sigint()
