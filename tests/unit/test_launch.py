"""write-launch: per-platform settings, the worker prefix, library requests, and the check."""

from __future__ import annotations

import ast
import json
import os
import sys
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from griptape_nodes_rez_build import launch
from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.launch import (
    LaunchOptions,
    LaunchPackage,
    PlatformSettings,
    launch_package_content,
    library_request,
    next_launch_version,
    parse_platform_values,
    parse_set_values,
    validate_launch,
    write_launch,
)
from griptape_nodes_rez_build.rez_tools import RezResult
from tests.unit.conftest import FakeRezTools, failed, ok
from tests.unit.rex_fake import Stopped, run_commands

if TYPE_CHECKING:
    from pathlib import Path


def _this_platform_tools() -> str:
    """A --tools value for the machine running the test, absolute on that platform."""
    if sys.platform == "win32":
        return "windows=C:/rez/bin/rez"
    if sys.platform == "darwin":
        return "osx=/opt/rez/bin/rez"
    return "linux=/opt/rez/bin/rez"


def _package(**overrides: object) -> LaunchPackage:
    fields: dict[str, object] = {
        "version": "1.0.0",
        "engine_version": "0.103.0",
        "library_requests": ["rez_test_edit==0.1.0"],
        "per_platform": {
            "osx": PlatformSettings(tools="/opt/rez/bin/rez", testing_store=None, config_file=None),
            "windows": PlatformSettings(tools="P:/rez/bin/rez/", testing_store="P:/testing", config_file="P:/gtn.py"),
        },
        "shared": {"XDG_CONFIG_HOME": "/studio/xdg"},
    }
    fields.update(overrides)
    return LaunchPackage(**fields)  # type: ignore[arg-type]


def _add_package(store: Path, family: str, version: str) -> None:
    package_file = store / family / version / "package.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")


class TestContent:
    def test_requires_the_engine_and_libraries_exactly(self) -> None:
        content = launch_package_content(_package())
        requires = next(
            ast.literal_eval(node.value)
            for node in ast.parse(content).body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "requires"
        )
        assert requires == ["griptape_nodes_engine==0.103.0", "rez_test_edit==0.1.0"]

    def test_settings_for_this_platform(self) -> None:
        run = run_commands(launch_package_content(_package()), platform="osx")

        assert run.environ["GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE"] == "environment"
        prefix = json.loads(run.environ["GTN_CONFIG_WORKER__COMMAND_PREFIX"])
        assert prefix == [
            "/opt/rez/bin/rez/rez",
            "env",
            "--norc",
            "griptape_nodes_engine=={engine_version}",
            "python-{python_version}",
            "{library_request}",
            "--",
        ]
        # Rez must leave the engine's placeholders alone.
        assert run.environ["GTN_CONFIG_WORKER__COMMAND_PREFIX"] in run.literals
        assert run.environ["XDG_CONFIG_HOME"] == "/studio/xdg"
        assert "REZ_PACKAGES_PATH" not in run.environ
        assert "REZ_CONFIG_FILE" not in run.environ

    def test_testing_store_is_recorded_and_config_file_is_layered(self) -> None:
        content = launch_package_content(_package())
        run = run_commands(content, platform="windows", environ={"REZ_CONFIG_FILE": "P:/studio.py"})

        prefix = json.loads(run.environ["GTN_CONFIG_WORKER__COMMAND_PREFIX"])
        assert prefix[0] == "P:/rez/bin/rez/rez"
        # Layered after the studio's configuration, never replacing it.
        assert run.environ["REZ_CONFIG_FILE"] == os.pathsep.join(["P:/studio.py", "P:/gtn.py"])
        # The engine reads no rez settings, so the testing store sets nothing; it is recorded.
        assert "REZ_PACKAGES_PATH" not in run.environ
        assert not any("P:/testing" in value for value in run.environ.values())
        recorded = next(
            ast.literal_eval(node.value)
            for node in ast.parse(content).body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "testing_stores"
        )
        assert recorded == {"windows": "P:/testing"}

    def test_config_file_without_a_studio_config(self) -> None:
        run = run_commands(launch_package_content(_package()), platform="windows")
        assert run.environ["REZ_CONFIG_FILE"] == "P:/gtn.py"

    def test_studio_config_survives_rez_replacing_appended_variables(self) -> None:
        # rex_fake replaces an inherited variable on the first append, as rez does; a plain
        # env.REZ_CONFIG_FILE.append() would leave only P:/gtn.py.
        run = run_commands(
            launch_package_content(_package()), platform="windows", environ={"REZ_CONFIG_FILE": "P:/a.py;P:/b.py"}
        )
        assert run.environ["REZ_CONFIG_FILE"] == "P:/a.py;P:/b.py" + os.pathsep + "P:/gtn.py"

    @pytest.mark.parametrize("platform", ["osx", "windows"])
    def test_allow_sandbox_is_set_on_every_platform(self, platform: str) -> None:
        run = run_commands(launch_package_content(_package(allow_sandbox=True)), platform=platform)
        assert run.environ["GTN_CONFIG_LIBRARY__ENVIRONMENT_ALLOWS_SANDBOX"] == "true"
        assert run.environ["GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE"] == "environment"

    def test_sandbox_setting_absent_by_default(self) -> None:
        run = run_commands(launch_package_content(_package()), platform="osx")
        assert "GTN_CONFIG_LIBRARY__ENVIRONMENT_ALLOWS_SANDBOX" not in run.environ

    def test_unconfigured_platform_stops(self) -> None:
        with pytest.raises(Stopped, match="set up for: osx, windows"):
            run_commands(launch_package_content(_package()), platform="linux")

    def test_awkward_values_round_trip(self) -> None:
        awkward = 'C:\\it\'s "here"\\'
        run = run_commands(launch_package_content(_package(shared={"NOTE": awkward})), platform="osx")
        assert run.environ["NOTE"] == awkward


class TestOptions:
    def test_platform_values(self) -> None:
        assert parse_platform_values(["osx=/a", "windows=P:/b"], "--tools") == {"osx": "/a", "windows": "P:/b"}

    @pytest.mark.parametrize("value", ["/just/a/path", "mac=/a", "osx="])
    def test_bad_platform_value(self, value: str) -> None:
        with pytest.raises(BuildError, match="PLATFORM=PATH"):
            parse_platform_values([value], "--tools")

    def test_set_values(self) -> None:
        assert parse_set_values(["XDG_CONFIG_HOME=/x=y"]) == {"XDG_CONFIG_HOME": "/x=y"}
        with pytest.raises(BuildError, match="NAME=VALUE"):
            parse_set_values(["1BAD=x"])

    def test_library_request_picks_the_newest_edit_package(self, tmp_path: Path) -> None:
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        _add_package(tmp_path, "rez_test_edit", "0.10.0")
        _add_package(tmp_path, "rez_test_edit", "0.9.0")
        assert library_request("rez_test", [tmp_path]) == "rez_test_edit==0.10.0"
        assert library_request("rez_test_edit", [tmp_path]) == "rez_test_edit==0.10.0"
        assert library_request("rez_test-0.9.0", [tmp_path]) == "rez_test_edit==0.9.0"

    def test_missing_library(self, tmp_path: Path) -> None:
        with pytest.raises(BuildError, match="build-library"):
            library_request("rez_test", [tmp_path])
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        with pytest.raises(BuildError, match="no store holds it"):
            library_request("rez_test-0.2.0", [tmp_path])

    def test_next_version(self, tmp_path: Path) -> None:
        assert next_launch_version(tmp_path) == "1.0.0"
        _add_package(tmp_path, "griptape_launch", "1.0.9")
        assert next_launch_version(tmp_path) == "1.0.10"


class TestWriteLaunch:
    def _options(self, tmp_path: Path, rez_tools: FakeRezTools | None, **overrides: object) -> LaunchOptions:
        fields: dict[str, object] = {
            "tools": ["osx=/opt/rez/bin/rez", "linux=/opt/rez/bin/rez"],
            "libraries": ["rez_test"],
            "store": tmp_path,
            "testing_stores": [],
            "config_files": [],
            "set_values": [],
            "engine_version": None,
            "version": None,
            "print_only": False,
            "rebuild": False,
            "rez_tools": rez_tools,
        }
        fields.update(overrides)
        return LaunchOptions(**fields)  # type: ignore[arg-type]

    def test_print_writes_nothing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")

        write_launch(self._options(tmp_path, None, print_only=True))

        assert "name = 'griptape_launch'" in capsys.readouterr().out
        assert not (tmp_path / "griptape_launch").exists()

    def test_engine_only_launch_package(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")

        write_launch(self._options(tmp_path, None, libraries=[], print_only=True))

        captured = capsys.readouterr()
        assert "'griptape_nodes_engine==0.103.0',\n]" in captured.out
        # --print keeps stdout for the package; notices go to stderr.
        assert captured.out.startswith("# -*- coding: utf-8 -*-")
        assert "no libraries will load" in " ".join(captured.err.split())

    def test_allow_sandbox_shows_in_print_output_and_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")

        write_launch(self._options(tmp_path, None, print_only=True, allow_sandbox=True))

        captured = capsys.readouterr()
        assert "setenv('GTN_CONFIG_LIBRARY__ENVIRONMENT_ALLOWS_SANDBOX', 'true')" in captured.out
        assert "Sandbox: allowed (--allow-sandbox)" in " ".join(captured.err.split())

    def test_sandbox_is_off_by_default(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")

        write_launch(self._options(tmp_path, None, print_only=True))

        captured = capsys.readouterr()
        assert "ENVIRONMENT_ALLOWS_SANDBOX" not in captured.out
        assert "Sandbox: not allowed" in " ".join(captured.err.split())

    def test_relative_tools_path_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(BuildError, match="not an absolute path on windows"):
            write_launch(self._options(tmp_path, None, tools=["windows=rez/bin"], print_only=True))

    def test_existing_version_is_refused_without_rebuild(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        _add_package(tmp_path, "griptape_launch", "1.0.0")
        with pytest.raises(BuildError, match="already exists"):
            write_launch(self._options(tmp_path, fake_tools, version="1.0.0"))

    def test_launch_package_that_fails_its_check_is_removed(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        fake_tools.env_results = [failed("conflict")]
        with pytest.raises(BuildError, match="was removed"):
            write_launch(self._options(tmp_path, fake_tools, tools=[_this_platform_tools()]))
        assert not (tmp_path / "griptape_launch" / "1.0.0").exists()

    def test_a_failed_rebuild_puts_the_previous_launch_package_back(
        self, tmp_path: Path, fake_tools: FakeRezTools
    ) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        previous = tmp_path / "griptape_launch" / "1.2.0" / "package.py"
        previous.parent.mkdir(parents=True)
        previous.write_text("# the launch package artists use today\n", encoding="utf-8")
        fake_tools.env_results = [failed("conflict")]

        with pytest.raises(BuildError, match="put back as it was before this build"):
            write_launch(
                self._options(tmp_path, fake_tools, tools=[_this_platform_tools()], version="1.2.0", rebuild=True)
            )

        assert previous.read_text(encoding="utf-8") == "# the launch package artists use today\n"
        assert [p.name for p in (tmp_path / "griptape_launch").iterdir()] == ["1.2.0"]

    def test_a_passing_rebuild_leaves_no_retired_folder(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        previous = tmp_path / "griptape_launch" / "1.2.0" / "package.py"
        previous.parent.mkdir(parents=True)
        previous.write_text("# old\n", encoding="utf-8")

        with patch.object(launch, "validate_launch"):
            write_launch(
                self._options(tmp_path, fake_tools, tools=[_this_platform_tools()], version="1.2.0", rebuild=True)
            )

        assert "name = 'griptape_launch'" in previous.read_text(encoding="utf-8")
        assert [p.name for p in (tmp_path / "griptape_launch").iterdir()] == ["1.2.0"]

    def test_a_write_error_leaves_no_staging_folder(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")

        with (
            patch.object(launch, "move_into_place", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            write_launch(self._options(tmp_path, fake_tools, tools=[_this_platform_tools()]))

        assert list((tmp_path / "griptape_launch").iterdir()) == []

    def test_missing_engine(self, tmp_path: Path) -> None:
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        with pytest.raises(BuildError, match="build-engine"):
            write_launch(self._options(tmp_path, None, print_only=True))


class TestValidateLaunch:
    def _found(self, store: Path, package: LaunchPackage, platform: str, **overrides: object) -> str:
        found: dict[str, object] = {
            "GTN_LIBRARY_PATHS": str(store / "rez_test_edit/0.1.0/python/rez-test/griptape_nodes_library.json"),
            "GTN_LIBRARY_WORKER_REQUESTS": "Rez Test Library=rez_test==0.1.0",
            "GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE": "environment",
            "GTN_CONFIG_WORKER__COMMAND_PREFIX": json.dumps(package.per_platform[platform].command_prefix),
            "griptape_nodes": str(store / "griptape_nodes_engine/0.103.0/python/griptape_nodes/__init__.py"),
            "griptape_nodes_app": str(store / "griptape_nodes/0.99.0/python/griptape_nodes_app/__init__.py"),
            "workers": [
                {
                    "name": "Rez Test Library",
                    "command": [
                        "/opt/rez/bin/rez/rez",
                        "env",
                        "--norc",
                        "griptape_nodes_engine==0.103.0",
                        "rez_test==0.1.0",
                        "--",
                    ],
                    "returncode": 0,
                    "output": "",
                }
            ],
        }
        found.update(overrides)
        return json.dumps(found)

    def _this_platform_package(self, **overrides: object) -> tuple[LaunchPackage, str]:
        platform = {"darwin": "osx", "win32": "windows"}.get(sys.platform, "linux")
        settings = PlatformSettings(tools="/opt/rez/bin/rez", testing_store=None, config_file=None)
        return _package(per_platform={platform: settings}, **overrides), platform

    def test_workers_are_started_from_inside_the_launch_environment(
        self, tmp_path: Path, fake_tools: FakeRezTools
    ) -> None:
        package, platform = self._this_platform_package()
        fake_tools.configured_paths = [tmp_path]
        fake_tools.env_results = [ok(self._found(tmp_path, package, platform))]

        validate_launch(fake_tools, package, store=tmp_path)

        call = fake_tools.env_calls[0]
        assert call.request == ["griptape_launch==1.0.0"]
        # Resolved from rez's configuration, as an artist would, not with --paths.
        assert call.paths is None
        assert call.extra_env is None
        # The script inside starts the workers itself, with the engine version to fill in.
        assert call.command[-1] == "0.103.0"
        assert call.script is not None
        assert "subprocess.run(command" in call.script

    def test_store_rez_does_not_search_is_added_for_the_workers_too(
        self, tmp_path: Path, fake_tools: FakeRezTools
    ) -> None:
        package, platform = self._this_platform_package()
        fake_tools.env_results = [ok(self._found(tmp_path, package, platform))]
        validate_launch(fake_tools, package, store=tmp_path)
        assert fake_tools.env_calls[0].extra_env == {"REZ_PACKAGES_PATH": str(tmp_path)}

    def test_worker_that_fails_inside_the_launch_environment_fails_the_build(
        self, tmp_path: Path, fake_tools: FakeRezTools
    ) -> None:
        package, platform = self._this_platform_package()
        worker = {
            "name": "Rez Test Library",
            "command": ["rez", "env", "--norc", "griptape_nodes_engine==0.103.0", "rez_test==0.1.0", "--"],
            "returncode": 1,
            "output": "package family not found: python (searched: ~/packages)",
        }
        fake_tools.env_results = [ok(self._found(tmp_path, package, platform, workers=[worker]))]
        with pytest.raises(BuildError, match="package family not found: python"):
            validate_launch(fake_tools, package, store=tmp_path)

    def test_check_expects_the_sandbox_setting_when_allowed(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        package, platform = self._this_platform_package(allow_sandbox=True)
        fake_tools.env_results = [ok(self._found(tmp_path, package, platform))]
        with pytest.raises(BuildError, match="ENVIRONMENT_ALLOWS_SANDBOX is '', expected 'true'"):
            validate_launch(fake_tools, package, store=tmp_path)

        fake_tools.env_results = [
            ok(self._found(tmp_path, package, platform, GTN_CONFIG_LIBRARY__ENVIRONMENT_ALLOWS_SANDBOX="true"))
        ]
        validate_launch(fake_tools, package, store=tmp_path)

    def test_wrong_prefix_fails(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        package, platform = self._this_platform_package()
        fake_tools.env_results = [ok(self._found(tmp_path, package, platform, GTN_CONFIG_WORKER__COMMAND_PREFIX="[]"))]
        with pytest.raises(BuildError, match="GTN_CONFIG_WORKER__COMMAND_PREFIX"):
            validate_launch(fake_tools, package, store=tmp_path)

    def test_conflict_names_the_libraries_on_both_sides(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        libraries = ["conflict_a_edit==1.0.0", "conflict_b_edit==1.0.0", "rez_test_edit==0.1.0"]
        package, _ = self._this_platform_package(library_requests=libraries)
        conflict = "The following package conflicts occurred: (tabulate<0.9 <--!--> tabulate-0.9+)"
        fake_tools.env_results = [failed(conflict)]

        def answer(request: list[str]) -> RezResult:
            # The engine plus a pair: conflict_a conflicts with either other library.
            if "conflict_a_edit==1.0.0" in request and request != [request[0], "conflict_a_edit==1.0.0"]:
                return RezResult(1, conflict, "")
            return RezResult(0, "", "")

        fake_tools.resolve_answer = answer
        with pytest.raises(BuildError) as raised:
            validate_launch(fake_tools, package, store=tmp_path)

        message = str(raised.value)
        assert "conflict_a_edit==1.0.0 and conflict_b_edit==1.0.0 conflict: " + conflict in message
        assert "conflict_a_edit==1.0.0 and rez_test_edit==0.1.0 conflict" in message
        assert "conflict_b_edit==1.0.0 and rez_test_edit==0.1.0" not in message

    def test_library_that_cannot_resolve_with_the_engine_is_named(
        self, tmp_path: Path, fake_tools: FakeRezTools
    ) -> None:
        package, _ = self._this_platform_package()
        fake_tools.env_results = [failed("conflict")]
        fake_tools.resolve_answer = lambda _request: RezResult(1, "", "Package could not be found: humanize-9+")
        with pytest.raises(
            BuildError, match=r"rez_test_edit==0\.1\.0 cannot resolve with the engine: Package could not be found"
        ):
            validate_launch(fake_tools, package, store=tmp_path)

    def test_platform_without_settings_is_not_checked(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        settings = PlatformSettings(tools="/opt/rez/bin/rez", testing_store=None, config_file=None)
        validate_launch(fake_tools, _package(per_platform={"nowhere": settings}), store=tmp_path)
        assert fake_tools.env_calls == []
