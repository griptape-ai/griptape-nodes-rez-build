"""write-launch: per-platform settings, the worker prefix, library requests, and the check."""

from __future__ import annotations

import ast
import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

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
from tests.unit.conftest import FakeRezTools, failed, ok
from tests.unit.rex_fake import Stopped, run_commands

if TYPE_CHECKING:
    from pathlib import Path


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
            "{library_request}",
            "--",
        ]
        # Rez must leave the engine's placeholders alone.
        assert run.environ["GTN_CONFIG_WORKER__COMMAND_PREFIX"] in run.literals
        assert run.environ["XDG_CONFIG_HOME"] == "/studio/xdg"
        assert "REZ_PACKAGES_PATH" not in run.environ
        assert "REZ_CONFIG_FILE" not in run.environ

    def test_testing_store_and_config_file(self) -> None:
        run = run_commands(
            launch_package_content(_package()),
            platform="windows",
            environ={"REZ_USED_PACKAGES_PATH": "P:/released", "REZ_CONFIG_FILE": "P:/studio.py"},
        )

        prefix = json.loads(run.environ["GTN_CONFIG_WORKER__COMMAND_PREFIX"])
        assert prefix[0] == "P:/rez/bin/rez/rez"
        assert run.environ["REZ_PACKAGES_PATH"] == os.pathsep.join(["P:/testing", "P:/released"])
        # Layered after the studio's configuration, never replacing it.
        assert run.environ["REZ_CONFIG_FILE"] == os.pathsep.join(["P:/studio.py", "P:/gtn.py"])

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

    def test_relative_tools_path_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(BuildError, match="not an absolute path on windows"):
            write_launch(self._options(tmp_path, None, tools=["windows=rez/bin"], print_only=True))

    def test_existing_version_is_refused_without_rebuild(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        _add_package(tmp_path, "griptape_nodes_engine", "0.103.0")
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        _add_package(tmp_path, "griptape_launch", "1.0.0")
        with pytest.raises(BuildError, match="already exists"):
            write_launch(self._options(tmp_path, fake_tools, version="1.0.0"))

    def test_missing_engine(self, tmp_path: Path) -> None:
        _add_package(tmp_path, "rez_test_edit", "0.1.0")
        with pytest.raises(BuildError, match="build-engine"):
            write_launch(self._options(tmp_path, None, print_only=True))


class TestValidateLaunch:
    def _found(self, store: Path, package: LaunchPackage, platform: str, **overrides: str) -> str:
        found = {
            "GTN_LIBRARY_PATHS": str(store / "rez_test_edit/0.1.0/python/griptape_nodes_library.json"),
            "GTN_LIBRARY_WORKER_REQUESTS": "Rez Test Library=rez_test==0.1.0",
            "GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE": "environment",
            "GTN_CONFIG_WORKER__COMMAND_PREFIX": json.dumps(package.per_platform[platform].command_prefix),
            "griptape_nodes": str(store / "griptape_nodes_engine/0.103.0/python/griptape_nodes/__init__.py"),
            "griptape_nodes_app": str(store / "griptape_nodes/0.99.0/python/griptape_nodes_app/__init__.py"),
        }
        found.update(overrides)
        return json.dumps(found)

    def _this_platform_package(self) -> tuple[LaunchPackage, str]:
        platform = {"darwin": "osx", "win32": "windows"}.get(sys.platform, "linux")
        settings = PlatformSettings(tools="/opt/rez/bin/rez", testing_store=None, config_file=None)
        return _package(per_platform={platform: settings}), platform

    def test_launch_and_each_worker_are_resolved(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        package, platform = self._this_platform_package()
        fake_tools.env_results = [ok(self._found(tmp_path, package, platform)), ok("")]

        validate_launch(fake_tools, package, store=tmp_path)

        assert fake_tools.env_calls[0].request == ["griptape_launch==1.0.0"]
        # The worker's resolve, exactly as the prefix builds it.
        assert fake_tools.env_calls[1].request == ["griptape_nodes_engine==0.103.0", "rez_test==0.1.0"]

    def test_wrong_prefix_fails(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        package, platform = self._this_platform_package()
        fake_tools.env_results = [ok(self._found(tmp_path, package, platform, GTN_CONFIG_WORKER__COMMAND_PREFIX="[]"))]
        with pytest.raises(BuildError, match="GTN_CONFIG_WORKER__COMMAND_PREFIX"):
            validate_launch(fake_tools, package, store=tmp_path)

    def test_worker_that_does_not_resolve_fails(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        package, platform = self._this_platform_package()
        fake_tools.env_results = [ok(self._found(tmp_path, package, platform)), failed("conflict: huggingface_hub")]
        with pytest.raises(BuildError, match="worker environment of 'Rez Test Library'"):
            validate_launch(fake_tools, package, store=tmp_path)

    def test_platform_without_settings_is_not_checked(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        settings = PlatformSettings(tools="/opt/rez/bin/rez", testing_store=None, config_file=None)
        validate_launch(fake_tools, _package(per_platform={"nowhere": settings}), store=tmp_path)
        assert fake_tools.env_calls == []
