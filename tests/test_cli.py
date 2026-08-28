"""
Tests for the CLI argument parsing (``gh_downloader.cli``).
"""

from __future__ import annotations

import argparse
import sys

import pytest

from gh_downloader.cli import (
    _coerce_implicit_download,
    build_parser,
    run_cli,
)


class TestBuildParser:
    def test_returns_argument_parser(self):
        parser = build_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_prog_name(self):
        parser = build_parser()
        assert parser.prog == "gh-dl"

    def test_has_version_action(self):
        parser = build_parser()
        for action in parser._actions:
            if action.option_strings and "--version" in action.option_strings:
                # Version string uses %(prog)s formatting
                assert "0.1.0" in action.version
                return
        pytest.fail("--version action not found")

    def test_has_subcommands(self):
        parser = build_parser()
        assert parser._subparsers is not None


class TestRunCli:
    def test_no_args_returns_zero(self, monkeypatch):
        """Without arguments, run_cli should start interactive mode and return 0."""
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert run_cli([]) == 0

    def test_returns_int(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert isinstance(run_cli([]), int)

    def test_help_flag(self):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["--help"])
        assert exc.value.code == 0


class TestImplicitDownload:
    def test_coerce_prepends_download_for_repo(self):
        assert _coerce_implicit_download(["stedolan/jq", "-p", "*.exe"]) == [
            "download",
            "stedolan/jq",
            "-p",
            "*.exe",
        ]

    def test_coerce_handles_full_url(self):
        url = "https://github.com/stedolan/jq/releases/tag/jq-1.8.1"
        assert _coerce_implicit_download([url]) == ["download", url]

    def test_coerce_leaves_known_subcommands_alone(self):
        for first in ("download", "config", "init", "list"):
            argv = [first, "x"]
            assert _coerce_implicit_download(argv) == argv

    def test_coerce_leaves_flags_and_empty_alone(self):
        assert _coerce_implicit_download([]) == []
        assert _coerce_implicit_download(["--version"]) == ["--version"]
        assert _coerce_implicit_download(["-o", "dir"]) == ["-o", "dir"]

    def test_coerce_ignores_single_word(self):
        assert _coerce_implicit_download(["jq"]) == ["jq"]

    def test_repo_first_routes_to_download_handler(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_handle(args: argparse.Namespace) -> int:
            captured["repo"] = args.repo
            captured["patterns"] = args.patterns
            return 0

        monkeypatch.setattr("gh_downloader.cli._handle_download", fake_handle)
        assert run_cli(["stedolan/jq", "-p", "*.exe"]) == 0
        assert captured["repo"] == "stedolan/jq"
        assert captured["patterns"] == ["*.exe"]

    def test_single_word_is_not_implicit_download(self):
        with pytest.raises(SystemExit):
            run_cli(["jq"])

    def test_bare_command_still_interactive(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert run_cli([]) == 0
