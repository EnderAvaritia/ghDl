"""
Tests for progress rendering (``gh_downloader.cli.ProgressTracker`` and
``gh_downloader.interactive._ProgressTracker``).
"""

from __future__ import annotations

import io
import sys

from gh_downloader.cli import ProgressTracker
from gh_downloader.interactive import _ProgressTracker
from gh_downloader.utils import display_width


class _FakeTty(io.StringIO):
    """StringIO that pretends to be an interactive terminal."""

    def isatty(self) -> bool:
        return True


def _segments(output: str) -> list[str]:
    """Split the captured output on carriage returns (screen rewrites)."""
    return output.split("\r")


class TestCliProgressTracker:
    def test_long_name_never_wraps(self, monkeypatch):
        buf = _FakeTty()
        monkeypatch.setattr(sys, "stdout", buf)
        monkeypatch.setattr("gh_downloader.cli.terminal_width", lambda: 40)

        tracker = ProgressTracker()
        tracker.update("a" * 200, 50, 100, 1024.0)

        for segment in _segments(buf.getvalue()):
            assert display_width(segment) <= 39

    def test_long_cjk_name_never_wraps(self, monkeypatch):
        buf = _FakeTty()
        monkeypatch.setattr(sys, "stdout", buf)
        monkeypatch.setattr("gh_downloader.cli.terminal_width", lambda: 40)

        tracker = ProgressTracker()
        tracker.update("下载" * 100, 50, 100, 1024.0)

        for segment in _segments(buf.getvalue()):
            assert display_width(segment) <= 39

    def test_completion_line_never_wraps(self, monkeypatch):
        buf = _FakeTty()
        monkeypatch.setattr(sys, "stdout", buf)
        monkeypatch.setattr("gh_downloader.cli.terminal_width", lambda: 40)

        tracker = ProgressTracker()
        tracker.update("a" * 200, 100, 100, 1024.0)

        for segment in _segments(buf.getvalue()):
            assert display_width(segment) <= 39

    def test_non_tty_emits_only_completion_lines(self, monkeypatch):
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)

        tracker = ProgressTracker()
        tracker.update("file.zip", 50, 100, 1024.0)
        assert buf.getvalue() == ""

        tracker.update("file.zip", 100, 100, 1024.0)
        output = buf.getvalue()
        assert "\r" not in output
        assert "file.zip" in output
        assert "100%" in output


class TestInteractiveProgressTracker:
    def test_long_name_never_wraps(self, monkeypatch):
        buf = _FakeTty()
        monkeypatch.setattr(sys, "stdout", buf)
        monkeypatch.setattr("gh_downloader.interactive.terminal_width", lambda: 40)

        tracker = _ProgressTracker()
        tracker("a" * 200, 50, 100, 1024.0)

        for segment in _segments(buf.getvalue()):
            assert display_width(segment) <= 39

    def test_completion_line_never_wraps(self, monkeypatch):
        buf = _FakeTty()
        monkeypatch.setattr(sys, "stdout", buf)
        monkeypatch.setattr("gh_downloader.interactive.terminal_width", lambda: 40)

        tracker = _ProgressTracker()
        tracker("a" * 200, 100, 100, 1024.0)

        for segment in _segments(buf.getvalue()):
            assert display_width(segment) <= 39

    def test_non_tty_emits_only_completion_lines(self, monkeypatch):
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)

        tracker = _ProgressTracker()
        tracker("file.zip", 50, 100, 1024.0)
        assert buf.getvalue() == ""

        tracker("file.zip", 100, 100, 1024.0)
        output = buf.getvalue()
        assert "\r" not in output
        assert "file.zip" in output
        assert "100%" in output