"""The griptape-nodes-rez command line."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from typer.testing import CliRunner

from griptape_nodes_rez_build import cli
from griptape_nodes_rez_build.errors import BuildError

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _flat(output: str) -> str:
    """Output with the console's line wrapping undone."""
    return " ".join(output.split())


class TestCommandLine:
    def test_help_lists_the_four_commands(self) -> None:
        result = runner.invoke(cli.app, ["--help"])
        assert result.exit_code == 0
        for command in ("build-engine", "build-library", "write-launch", "gpu-config"):
            assert command in result.output

    def test_build_library_needs_exactly_one_source(self, tmp_path: Path) -> None:
        result = runner.invoke(cli.app, ["build-library", "--local-packages-path", str(tmp_path)])
        assert result.exit_code == 1
        assert "exactly one of --git-url or --local-path" in _flat(result.output)

    def test_branch_needs_a_git_url(self, tmp_path: Path) -> None:
        result = runner.invoke(
            cli.app,
            ["build-library", "--local-packages-path", str(tmp_path), "--local-path", str(tmp_path), "--branch", "x"],
        )
        assert result.exit_code == 1

    def test_build_error_exits_non_zero_with_the_message(self, tmp_path: Path) -> None:
        (tmp_path / "rez-env").write_text("", encoding="utf-8")
        with patch.object(cli, "build_engine", side_effect=BuildError("Attempted to X. Failed because Y.")):
            result = runner.invoke(
                cli.app,
                [
                    "build-engine",
                    "--yes",
                    "--engine-repo",
                    str(tmp_path),
                    "--local-packages-path",
                    str(tmp_path),
                    "--rez-tools",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 1
        assert "Attempted to X. Failed because Y." in _flat(result.output)

    def test_missing_rez_tools_fail_before_building(self, tmp_path: Path) -> None:
        result = runner.invoke(
            cli.app,
            [
                "build-engine",
                "--yes",
                "--engine-repo",
                str(tmp_path),
                "--local-packages-path",
                str(tmp_path),
                "--rez-tools",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
        assert "no rez-env there" in _flat(result.output)

    def test_build_engine_defaults_to_the_published_engine(self, tmp_path: Path) -> None:
        (tmp_path / "rez-env").write_text("", encoding="utf-8")
        with patch.object(cli, "build_engine") as build:
            result = runner.invoke(
                cli.app,
                ["build-engine", "--yes", "--local-packages-path", str(tmp_path), "--rez-tools", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output
        options = build.call_args.args[0]
        assert options.engine_repo is None
        assert options.engine_version is None

    def test_engine_version_and_engine_repo_are_exclusive(self, tmp_path: Path) -> None:
        result = runner.invoke(
            cli.app,
            [
                "build-engine",
                "--engine-repo",
                str(tmp_path),
                "--engine-version",
                "0.102.1",
                "--local-packages-path",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 1
        assert "Pass one of them" in _flat(result.output)

    def test_yes_is_passed_as_non_interactive(self, tmp_path: Path) -> None:
        (tmp_path / "rez-env").write_text("", encoding="utf-8")
        with patch.object(cli, "build_engine") as build:
            result = runner.invoke(
                cli.app,
                [
                    "build-engine",
                    "--yes",
                    "--engine-repo",
                    str(tmp_path),
                    "--local-packages-path",
                    str(tmp_path),
                    "--rez-tools",
                    str(tmp_path),
                    "--no-editor",
                ],
            )
        assert result.exit_code == 0, result.output
        options = build.call_args.args[0]
        assert options.interactive is False
        assert options.editor is False
