"""
Tests for the CLI argument parsing (``gh_downloader.cli``).
"""

from __future__ import annotations

import argparse
import sys

import pytest

from gh_downloader.cli import build_parser, run_cli


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
