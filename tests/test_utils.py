"""
Tests for utility functions (``gh_downloader.utils``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gh_downloader.utils import (
    build_output_path,
    check_disk_space,
    ensure_dir,
    format_size,
    format_speed,
    is_long_path,
    parse_repo_string,
    safe_filename,
    setup_signal_handler,
)


# -- safe_filename -----------------------------------------------------------


class TestSafeFilename:
    @pytest.mark.parametrize(
        ("input_name", "expected"),
        [
            ("simple.zip", "simple.zip"),
            ("file:name", "file_name"),
            ('a<b>c|d?e*f"g', "a_b_c_d_e_f_g"),
            ("no/slash\\here", "no_slash_here"),
            ("", ""),
            ("normal-file-v1.0.0.tar.gz", "normal-file-v1.0.0.tar.gz"),
        ],
    )
    def test_replaces_unsafe_chars(self, input_name: str, expected: str):
        assert safe_filename(input_name) == expected

    def test_keeps_unicode(self):
        name = "文件.zip"
        assert safe_filename(name) == name

    def test_all_unsafe_chars_replaced(self):
        unsafe = '\\/:*?"<>|'
        result = safe_filename(unsafe)
        assert result == "_" * len(unsafe)


# -- format_size -------------------------------------------------------------


class TestFormatSize:
    @pytest.mark.parametrize(
        ("bytes_in", "expected"),
        [
            (0, "0 B"),
            (1, "1 B"),
            (1023, "1023 B"),
            (1024, "1.00 KB"),
            (2048, "2.00 KB"),
            (1_048_576, "1.00 MB"),
            (1_073_741_824, "1.00 GB"),
            (1_099_511_627_776, "1.00 TB"),
        ],
    )
    def test_formats_correctly(self, bytes_in: int, expected: str):
        assert format_size(bytes_in) == expected

    def test_negative_raises_value_error(self):
        with pytest.raises(ValueError, match="non-negative"):
            format_size(-1)


# -- format_speed ------------------------------------------------------------


class TestFormatSpeed:
    @pytest.mark.parametrize(
        ("bps", "expected"),
        [
            (0, "0 B/s"),
            (500, "500.0 B/s"),
            (1023, "1023.0 B/s"),
            (1024, "1.0 KB/s"),
            (51_200, "50.0 KB/s"),
            (5_242_880, "5.0 MB/s"),
        ],
    )
    def test_formats_speed(self, bps: float, expected: str):
        assert format_speed(bps) == expected

    def test_negative_raises_value_error(self):
        with pytest.raises(ValueError, match="non-negative"):
            format_speed(-0.1)


# -- parse_repo_string -------------------------------------------------------


class TestParseRepoString:
    @pytest.mark.parametrize(
        ("input_str", "expected"),
        [
            ("owner/repo", ("owner", "repo")),
            ("stedolan/jq", ("stedolan", "jq")),
            ("https://github.com/owner/repo", ("owner", "repo")),
            ("https://github.com/owner/repo.git", ("owner", "repo")),
            ("http://github.com/owner/repo", ("owner", "repo")),
            ("  owner/repo  ", ("owner", "repo")),
            ("owner/repo/", ("owner", "repo")),
        ],
    )
    def test_parses_valid_strings(self, input_str: str, expected: tuple[str, str]):
        assert parse_repo_string(input_str) == expected

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            "no-slash",
            "https://github.com/",
            "/onlyowner",
        ],
    )
    def test_raises_on_invalid(self, invalid: str):
        with pytest.raises(ValueError):
            parse_repo_string(invalid)

    def test_nested_path_repo_only(self):
        """If URL has extra path segments, only first is taken as repo."""
        result = parse_repo_string("https://github.com/org/repo/extra/path")
        assert result == ("org", "repo")


# -- build_output_path -------------------------------------------------------


class TestBuildOutputPath:
    def test_nested_structure(self):
        path = build_output_path(
            "/base", "owner", "repo", "v1", "asset.zip", flat=False
        )
        # os.path.join on Windows keeps forward-slash prefix but uses
        # backslashes for the rest, so we verify structure rather than
        # exact string on all platforms.
        assert path.endswith("asset.zip")
        assert "owner" in path
        assert "repo" in path
        assert "v1" in path

    def test_flat_structure(self):
        path = build_output_path(
            "/base", "owner", "repo", "v1", "asset.zip", flat=True
        )
        assert path.endswith("asset.zip")
        assert "owner" not in path
        assert "repo" not in path


# -- is_long_path ------------------------------------------------------------


class TestIsLongPath:
    def test_short_path_returns_false(self):
        # On Windows: 260 limit; on other platforms: always False
        result = is_long_path("C:\\short\\path" if sys.platform == "win32" else "/short/path")
        # Non-Windows always False, Windows depends on length
        import os
        if os.name != "nt":
            assert result is False

    def test_absurdly_long_path(self):
        long_path = "/" + "a" * 500
        import os
        if os.name == "nt":
            assert is_long_path(long_path) is True
        else:
            assert is_long_path(long_path) is False


# -- ensure_dir --------------------------------------------------------------


class TestEnsureDir:
    def test_creates_directory_and_returns_path(self, tmp_path):
        d = str(tmp_path / "new_dir")
        result = ensure_dir(d)
        assert result == d
        assert Path(d).is_dir()

    def test_existing_directory_no_error(self, tmp_path):
        d = str(tmp_path / "existing")
        Path(d).mkdir()
        result = ensure_dir(d)
        Path(d).rmdir()  # cleanup
        assert result == d


# -- check_disk_space --------------------------------------------------------


class TestCheckDiskSpace:
    def test_returns_true_for_small_requirement(self, tmp_path):
        # Any real filesystem has at least 1 byte free
        assert check_disk_space(str(tmp_path), 1) is True

    def test_returns_false_for_impossible_requirement(self, tmp_path):
        assert check_disk_space(str(tmp_path), 10**30) is False

    def test_nonexistent_path_checks_parent(self, tmp_path):
        # Only the leaf component should be non-existent so that
        # os.path.dirname points to a real directory.
        child = str(tmp_path / "nonexistent_file")
        assert check_disk_space(child, 1) is True


# -- setup_signal_handler ----------------------------------------------------


class TestSetupSignalHandler:
    def test_returns_event(self):
        event = setup_signal_handler()
        assert event is not None
        assert hasattr(event, "is_set")
        assert event.is_set() is False
