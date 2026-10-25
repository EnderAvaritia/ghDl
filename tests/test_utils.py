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
    display_width,
    ensure_dir,
    fit_line,
    format_size,
    format_speed,
    is_long_path,
    parse_full_repo_url,
    parse_repo_string,
    safe_filename,
    setup_signal_handler,
    terminal_width,
    truncate_to_width,
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


# -- parse_full_repo_url ------------------------------------------------------


class TestParseFullRepoUrl:
    def test_owner_repo_returns_no_version(self):
        assert parse_full_repo_url("owner/repo") == ("owner", "repo", None)

    def test_release_tag_url(self):
        assert parse_full_repo_url(
            "https://github.com/jeessy2/ddns-go/releases/tag/v6.17.0"
        ) == ("jeessy2", "ddns-go", "v6.17.0")

    def test_release_latest_url(self):
        assert parse_full_repo_url(
            "https://github.com/owner/repo/releases/latest"
        ) == ("owner", "repo", "latest")

    def test_plain_github_url(self):
        assert parse_full_repo_url(
            "https://github.com/owner/repo"
        ) == ("owner", "repo", None)

    def test_trailing_slash(self):
        assert parse_full_repo_url(
            "https://github.com/owner/repo/"
        ) == ("owner", "repo", None)


# -- display_width / truncate_to_width / fit_line ---------------------------


class TestDisplayWidth:
    def test_empty_string(self):
        assert display_width("") == 0

    def test_ascii(self):
        assert display_width("abc") == 3

    def test_cjk_characters_are_double_width(self):
        assert display_width("下载") == 4

    def test_combining_mark_has_zero_width(self):
        assert display_width("e\u0301") == 1


class TestTruncateToWidth:
    def test_short_text_is_unchanged(self):
        assert truncate_to_width("hello", 10) == "hello"

    def test_long_text_ends_with_ellipsis_within_budget(self):
        result = truncate_to_width("a" * 100, 20)
        assert result.endswith("...")
        assert display_width(result) <= 20

    def test_cjk_text_never_exceeds_budget(self):
        result = truncate_to_width("下载" * 50, 21)
        assert display_width(result) <= 21

    def test_zero_width_returns_empty(self):
        assert truncate_to_width("hello", 0) == ""

    def test_budget_smaller_than_ellipsis(self):
        result = truncate_to_width("hello", 2)
        assert display_width(result) <= 2


class TestFitLine:
    def test_short_line_is_unchanged(self):
        assert fit_line("  ", "file.zip", ": 100%", 40) == "  file.zip: 100%"

    def test_long_name_is_truncated_but_suffix_survives(self):
        line = fit_line("  ", "x" * 200, ": 100% 1.00 MB/s", 40)
        assert display_width(line) <= 40
        assert line.startswith("  ")
        assert line.endswith(": 100% 1.00 MB/s")
        assert "..." in line

    def test_cjk_name_never_exceeds_budget(self):
        line = fit_line("  ", "下载" * 100, ": 50%", 40)
        assert display_width(line) <= 40

    def test_narrow_width_falls_back_to_hard_cut(self):
        line = fit_line("prefix", "body", "suffix", 5)
        assert display_width(line) <= 5

    def test_zero_width_returns_empty(self):
        assert fit_line("a", "b", "c", 0) == ""


class TestTerminalWidth:
    def test_returns_at_least_minimum(self):
        assert terminal_width() >= 20
