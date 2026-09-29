"""Commands given to rez env never carry Python code inline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from griptape_nodes_rez_build import engine, launch, library
from griptape_nodes_rez_build.rez_tools import RezResult, RezTools
from tests.unit.conftest import FakeRezTools, failed


class TestNoInlineCode:
    def test_rez_env_refuses_python_dash_c(self) -> None:
        # On Windows rez passes the command through a shell that strips double quotes.
        with pytest.raises(ValueError, match="run_python"):
            RezTools(Path("/rez")).env(["x"], ["python", "-c", "print('hi')"])

    def test_run_python_runs_the_script_from_a_file_and_removes_it(self, tmp_path: Path) -> None:
        tools = RezTools(tmp_path)
        seen: dict[str, object] = {}

        def run(args: list[str], *, timeout: int, extra_env: dict[str, str] | None = None) -> RezResult:  # noqa: ARG001
            script_file = Path(args[args.index("--") + 2])
            seen["args"] = args
            seen["script"] = script_file.read_text(encoding="utf-8")
            seen["file"] = script_file
            return RezResult(0, "", "")

        script = 'import importlib.metadata\nprint(importlib.metadata.distribution("x").locate_file(""))\n'
        with patch.object(tools, "run", side_effect=run):
            tools.run_python(["pkg"], script, ["0.103.0"])

        args = seen["args"]
        assert isinstance(args, list)
        assert args[args.index("--") + 1 :][0] == "python"
        assert args[-1] == "0.103.0"
        assert "-c" not in args
        assert seen["script"] == script
        assert not Path(str(seen["file"])).exists()

    def test_every_validation_runs_python_from_a_file(self, tmp_path: Path) -> None:
        # Each check fails at once; what matters is how rez env was called.
        tools = FakeRezTools(env_results=[failed("x"), failed("x"), failed("x")])
        with pytest.raises(engine.BuildError):
            engine.validate_engine(tools, [tmp_path], "0.103.0", store=tmp_path, editor=True)
        manifest = library.LibraryManifest(
            path=tmp_path / "griptape_nodes_library.json",
            name="L",
            pip_dependencies=[],
            pip_dependencies_exec=[],
            pip_install_flags=[],
            library_dependencies=[],
            library_version=None,
        )
        family = library.LibraryFamily(family="l", source="folder 'l'", folder="l")
        with pytest.raises(library.BuildError):
            library.validate_library(tools, [tmp_path], manifest, family, version="1.0.0", torch_backends=[])
        package = launch.LaunchPackage(
            version="1.0.0",
            engine_version="0.103.0",
            library_requests=[],
            per_platform={
                platform: launch.PlatformSettings(tools="/opt/rez", testing_store=None, config_file=None)
                for platform in ("osx", "linux", "windows")
            },
            shared={},
        )
        with pytest.raises(launch.BuildError):
            launch.validate_launch(tools, package, store=tmp_path)

        assert len(tools.env_calls) == len(("engine", "library", "launch"))
        for call in tools.env_calls:
            assert "-c" not in call.command
            assert call.command[1].endswith(".py")
            assert call.script is not None

    def test_worker_probe_inside_the_launch_check_runs_from_a_file(self) -> None:
        assert '"-c"' not in launch._VALIDATE_SCRIPT
        assert "worker_probe.py" in launch._VALIDATE_SCRIPT
