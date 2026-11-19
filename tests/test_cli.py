"""
Tests for the CLI argument parsing (``gh_downloader.cli``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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

    def test_help_escapes_literal_percent(self):
        """Help text with a literal % must be escaped as %% for argparse."""
        parser = build_parser()
        init_parser = parser._subparsers._group_actions[0].choices["init"]
        help_text = init_parser.format_help()
        assert "%APPDATA%" in help_text


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


# ---------------------------------------------------------------------------
# init --global / --force
# ---------------------------------------------------------------------------


@pytest.fixture
def global_config_target(monkeypatch, tmp_path) -> Path:
    """Redirect every user-level config lookup under *tmp_path*.

    Guarantees tests never touch the developer's real home / APPDATA.
    """
    target = tmp_path / "global" / "gh-dl" / "config.json"
    monkeypatch.setattr(
        "gh_downloader.config.global_user_config_path",
        lambda: target,
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return target


class TestInitGlobal:
    def test_parser_accepts_global_flag(self):
        args = build_parser().parse_args(["init", "--global"])
        assert args.global_config is True

    def test_parser_accepts_force_flag(self):
        args = build_parser().parse_args(["init", "--user", "--force"])
        assert args.force is True

    def test_parser_defaults(self):
        args = build_parser().parse_args(["init"])
        assert args.output is None
        assert args.user is False
        assert args.global_config is False
        assert args.force is False

    def test_run_cli_writes_only_to_global_path(
        self, global_config_target, tmp_path
    ):
        assert run_cli(["init", "--global"]) == 0

        assert global_config_target.is_file()
        data = json.loads(global_config_target.read_text(encoding="utf-8"))
        assert "github_token" in data

        # Nothing else was written anywhere under tmp_path.
        written = sorted(tmp_path.rglob("*.json"))
        assert written == [global_config_target]

    def test_second_run_without_force_returns_2_and_preserves_file(
        self, global_config_target, capsys
    ):
        assert run_cli(["init", "--global"]) == 0
        first = global_config_target.read_text(encoding="utf-8")

        assert run_cli(["init", "--global"]) == 2

        assert global_config_target.read_text(encoding="utf-8") == first
        err = capsys.readouterr().err
        assert "already exists" in err
        assert "--force" in err

    def test_force_overwrites_existing_global_config(self, global_config_target):
        global_config_target.parent.mkdir(parents=True)
        global_config_target.write_text('{"stale": true}\n', encoding="utf-8")

        assert run_cli(["init", "--global", "--force"]) == 0

        data = json.loads(global_config_target.read_text(encoding="utf-8"))
        assert "github_token" in data
        assert "stale" not in data

    def test_global_with_output_path_returns_2(
        self, global_config_target, capsys
    ):
        assert run_cli(["init", "--global", "foo.json"]) == 2
        assert not global_config_target.exists()
        assert "--global" in capsys.readouterr().err

    def test_user_still_writes_to_cwd(
        self, global_config_target, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)

        assert run_cli(["init", "--user"]) == 0

        assert (tmp_path / ".gh-dl.json").is_file()
        assert not global_config_target.exists()
