"""build-engine: requires as ranges, package layout, and the check against the store."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from griptape_nodes_rez_build import engine
from griptape_nodes_rez_build.common import PackageRequires
from griptape_nodes_rez_build.engine import (
    EngineBuildOptions,
    EngineProject,
    build_engine,
    engine_package_content,
    engine_requires,
    fetch_published_engine,
    read_engine_project,
    validate_engine,
    write_engine_package,
)
from griptape_nodes_rez_build.errors import BuildError, ResolveError
from griptape_nodes_rez_build.rez_uv import InstallReport, ResolvedPackage
from griptape_nodes_rez_build.store import WrittenPackage
from tests.unit.conftest import FakeRezTools, failed, ok
from tests.unit.rex_fake import run_commands

PYPROJECT = """\
[project]
name = "griptape-nodes-engine"
version = "0.103.0"
requires-python = ">=3.12.0, <3.14"
dependencies = [
  "httpx>=0.28.0,<1.0.0",
  "mcp>=2.0,<3",
  "pydantic>=2.10.6",
  "colorama; sys_platform == 'win32'",
]
"""
RESOLVED = {
    "httpx": "0.28.1",
    "mcp": "2.1.0",
    "pydantic": "2.12.0",
    "griptape-nodes": "0.99.0",
    "griptape-nodes-editor-dist": "0.126.0",
}


# An engine package an earlier build (on another platform) left in the store.
KEPT_ENGINE = """\
name = 'griptape_nodes_engine'

version = '0.103.0'

requires = [
    'griptape_nodes-0.98.0',
    'griptape_nodes_editor_dist-0.125.0',
    'httpx-0.28+<1',
]
"""


def _kept_engine(tmp_path: Path, content: str = KEPT_ENGINE) -> Path:
    package_file = tmp_path / "store" / "griptape_nodes_engine" / "0.103.0" / "package.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text(content, encoding="utf-8")
    return package_file


def _engine_repo(tmp_path: Path, pyproject: str = PYPROJECT) -> Path:
    repo = tmp_path / "engine"
    (repo / "src" / "griptape_nodes").mkdir(parents=True)
    (repo / "src" / "griptape_nodes" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "griptape_nodes" / "__pycache__").mkdir()
    (repo / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return repo


class TestReadEngineProject:
    def test_reads_version_dependencies_and_python(self, tmp_path: Path) -> None:
        project = read_engine_project(_engine_repo(tmp_path))
        assert project.version == "0.103.0"
        assert project.dependencies[0] == "httpx>=0.28.0,<1.0.0"
        assert project.requires_python == ">=3.12.0, <3.14"

    def test_not_an_engine_checkout(self, tmp_path: Path) -> None:
        with pytest.raises(BuildError, match="--engine-repo"):
            read_engine_project(tmp_path)


class TestEngineRequires:
    def test_ranges_from_pyproject_not_the_resolved_versions(self, tmp_path: Path) -> None:
        project = read_engine_project(_engine_repo(tmp_path))

        requires = engine_requires(project, RESOLVED, store=tmp_path, editor=True)

        assert requires.platform is not None
        osx = requires.platform["osx-arm64"]
        assert "httpx-0.28+<1" in osx
        assert "mcp-2+<3" in osx
        assert "pydantic-2.10.6+" in osx
        assert "python-3.12+<3.14" in osx
        # Only the app and editor are pinned, to the versions this build installed.
        assert "griptape_nodes-0.99.0" in osx
        assert "griptape_nodes_editor_dist-0.126.0" in osx
        assert not any("==" in entry for entry in osx)
        assert "colorama" in requires.platform["windows-AMD64"]
        assert "colorama" not in osx

    def test_without_editor(self, tmp_path: Path) -> None:
        project = read_engine_project(
            _engine_repo(tmp_path, PYPROJECT.replace("  \"colorama; sys_platform == 'win32'\",\n", ""))
        )
        requires = engine_requires(project, RESOLVED, store=tmp_path, editor=False)
        assert requires.platform is None
        assert not any(entry.startswith("griptape_nodes_editor_dist") for entry in requires.common)

    def test_app_without_editor_extra_says_what_to_do(self, tmp_path: Path) -> None:
        project = read_engine_project(_engine_repo(tmp_path))
        resolved = {name: version for name, version in RESOLVED.items() if name != "griptape-nodes-editor-dist"}
        with pytest.raises(BuildError, match="--no-editor"):
            engine_requires(project, resolved, store=tmp_path, editor=True)

    def test_unconvertible_dependency_fails(self, tmp_path: Path) -> None:
        project = read_engine_project(_engine_repo(tmp_path, PYPROJECT.replace("mcp>=2.0,<3", "mcp===2.0")))
        with pytest.raises(BuildError, match="version ranges"):
            engine_requires(project, RESOLVED, store=tmp_path, editor=True)


PUBLISHED_METADATA = """\
Metadata-Version: 2.4
Name: griptape-nodes-engine
Version: 0.102.1
Requires-Dist: httpx>=0.28.0,<1.0.0
Requires-Dist: colorama ; sys_platform == 'win32'
Requires-Dist: austin-dist>=3.7.0 ; extra == 'profiling'
Requires-Python: >=3.12.0, <3.14
"""


def _unpack_release(target: Path, *, with_package: bool = True) -> None:
    """What ``uv pip install --no-deps --target`` leaves for the engine release."""
    if with_package:
        (target / "griptape_nodes").mkdir(parents=True)
        (target / "griptape_nodes" / "__init__.py").write_text("", encoding="utf-8")
    dist_info = target / "griptape_nodes_engine-0.102.1.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(PUBLISHED_METADATA, encoding="utf-8")
    (dist_info / "WHEEL").write_text("Tag: py3-none-any\n", encoding="utf-8")


def _fake_download(*, with_package: bool = True, returncode: int = 0) -> object:
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if returncode == 0:
            _unpack_release(Path(command[command.index("--target") + 1]), with_package=with_package)
        return subprocess.CompletedProcess(command, returncode, stdout="", stderr="error: no such version")

    return run


class TestFetchPublishedEngine:
    def _resolved(self, engine_version: str = "0.102.1") -> list[ResolvedPackage]:
        return [ResolvedPackage("griptape-nodes-engine", engine_version), ResolvedPackage("griptape-nodes", "0.99.0")]

    def test_newest_engine_the_app_accepts(self, tmp_path: Path) -> None:
        with (
            patch.object(engine, "resolve_full", return_value=self._resolved()) as resolve,
            patch.object(engine.subprocess, "run", side_effect=_fake_download()) as download,
        ):
            project = fetch_published_engine(None, "griptape-nodes[editor]", tmp_path)

        assert resolve.call_args.args[0] == ["griptape-nodes-engine", "griptape-nodes[editor]"]
        assert download.call_args.args[0][-1] == "griptape-nodes-engine==0.102.1"
        assert project.version == "0.102.1"
        assert project.pip_request == "griptape-nodes-engine==0.102.1"
        assert project.source == tmp_path
        assert project.requires_python == ">=3.12.0, <3.14"

    def test_extras_are_left_out_and_markers_kept(self, tmp_path: Path) -> None:
        with (
            patch.object(engine, "resolve_full", return_value=self._resolved()),
            patch.object(engine.subprocess, "run", side_effect=_fake_download()),
        ):
            project = fetch_published_engine(None, "griptape-nodes", tmp_path)
        assert project.dependencies == ["httpx>=0.28.0,<1.0.0", "colorama ; sys_platform == 'win32'"]

    def test_engine_version_is_requested(self, tmp_path: Path) -> None:
        with (
            patch.object(engine, "resolve_full", return_value=self._resolved()) as resolve,
            patch.object(engine.subprocess, "run", side_effect=_fake_download()),
        ):
            fetch_published_engine("0.102.1", "griptape-nodes", tmp_path)
        assert resolve.call_args.args[0][0] == "griptape-nodes-engine==0.102.1"

    def test_app_and_engine_that_do_not_resolve_fail(self, tmp_path: Path) -> None:
        error = subprocess.CalledProcessError(1, "uv", stderr="No solution found")
        with (
            patch.object(engine, "resolve_full", side_effect=error),
            pytest.raises(ResolveError, match="No solution found"),
        ):
            fetch_published_engine("0.1.0", "griptape-nodes==0.99.0", tmp_path)

    def test_failed_download_fails(self, tmp_path: Path) -> None:
        with (
            patch.object(engine, "resolve_full", return_value=self._resolved()),
            patch.object(engine.subprocess, "run", side_effect=_fake_download(returncode=2)),
            pytest.raises(ResolveError, match="no such version"),
        ):
            fetch_published_engine(None, "griptape-nodes", tmp_path)

    def test_release_without_the_package_fails(self, tmp_path: Path) -> None:
        with (
            patch.object(engine, "resolve_full", return_value=self._resolved()),
            patch.object(engine.subprocess, "run", side_effect=_fake_download(with_package=False)),
            pytest.raises(BuildError, match="no griptape_nodes package"),
        ):
            fetch_published_engine(None, "griptape-nodes", tmp_path)

    def test_package_holds_only_the_engine_and_records_the_release(self, tmp_path: Path) -> None:
        _unpack_release(tmp_path / "release")
        project = EngineProject(
            source=tmp_path / "release",
            origin="griptape-nodes-engine==0.102.1 (published release)",
            pip_request="griptape-nodes-engine==0.102.1",
            version="0.102.1",
            dependencies=[],
            requires_python="",
        )
        version_dir = write_engine_package(
            project, PackageRequires(common=[], platform=None), store=tmp_path / "s"
        ).version_dir
        assert sorted(p.name for p in (version_dir / "python").iterdir()) == [
            "griptape_nodes",
            "griptape_nodes_engine-0.102.1.dist-info",
        ]
        assert "build_source = 'griptape-nodes-engine==0.102.1 (published release)'" in (
            version_dir / "package.py"
        ).read_text(encoding="utf-8")


class TestEnginePackage:
    def test_content_is_valid_python_with_escaped_values(self, tmp_path: Path) -> None:
        project = EngineProject(
            source=tmp_path,
            origin=str(tmp_path / "it's a \\"),
            pip_request="griptape-nodes-engine==0.103.0",
            version="0.103.0",
            dependencies=[],
            requires_python="",
        )
        content = engine_package_content(project, PackageRequires(common=["a-1+"], platform=None))

        values = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in ast.parse(content).body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        }
        assert values["build_source"] == str(tmp_path / "it's a \\")
        assert values["requires"] == ["a-1+"]
        assert run_commands(content, root="/s/e/1").environ["PYTHONPATH"] == "/s/e/1/python"

    def test_written_with_source_and_dist_info(self, tmp_path: Path) -> None:
        project = read_engine_project(_engine_repo(tmp_path))
        store = tmp_path / "store"

        version_dir = write_engine_package(project, PackageRequires(common=[], platform=None), store=store).version_dir

        assert (version_dir / "package.py").is_file()
        assert (version_dir / "python" / "griptape_nodes" / "__init__.py").is_file()
        assert not (version_dir / "python" / "griptape_nodes" / "__pycache__").exists()
        metadata = version_dir / "python" / "griptape_nodes_engine-0.103.0.dist-info" / "METADATA"
        assert "Version: 0.103.0" in metadata.read_text(encoding="utf-8")
        assert [p.name for p in (store / "griptape_nodes_engine").iterdir()] == ["0.103.0"]


class TestValidateEngine:
    def _found(self, root: Path, **overrides: str | None) -> str:
        found: dict[str, str | None] = {
            "griptape_nodes": str(root / "griptape_nodes_engine/0.103.0/python/griptape_nodes/__init__.py"),
            "griptape_nodes_app": str(root / "griptape_nodes/0.99.0/python/griptape_nodes_app/__init__.py"),
            "gtn": str(root / "griptape_nodes/0.99.0/bin/gtn"),
            "editor": str(root / "griptape_nodes_editor_dist/0.126.0/python"),
        }
        found.update(overrides)
        return json.dumps(found)

    def test_everything_from_the_store(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        fake_tools.env_results = [ok(self._found(tmp_path))]
        validate_engine(fake_tools, [tmp_path], "0.103.0", store=tmp_path, editor=True)
        assert fake_tools.env_calls[0].request == ["griptape_nodes_engine==0.103.0"]

    def test_gtn_from_elsewhere_fails(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        fake_tools.env_results = [ok(self._found(tmp_path, gtn="/usr/local/bin/gtn"))]
        with pytest.raises(BuildError, match="gtn: /usr/local/bin/gtn"):
            validate_engine(fake_tools, [tmp_path], "0.103.0", store=tmp_path, editor=True)

    def test_unresolvable_store_fails(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        fake_tools.env_results = [failed("package not found: griptape_nodes-0.99.0")]
        with pytest.raises(BuildError, match="package not found"):
            validate_engine(fake_tools, [tmp_path], "0.103.0", store=tmp_path, editor=True)


class TestBuildEngine:
    def _options(self, tmp_path: Path, tools: FakeRezTools, **overrides: object) -> EngineBuildOptions:
        fields: dict[str, object] = {
            "engine_repo": _engine_repo(tmp_path),
            "engine_version": None,
            "store": tmp_path / "store",
            "tools": tools,
            "editor": True,
            "app_version": "0.99.0",
            "rebuild": False,
            "interactive": False,
        }
        fields.update(overrides)
        return EngineBuildOptions(**fields)  # type: ignore[arg-type]

    def _report(self) -> InstallReport:
        return InstallReport(resolved=[ResolvedPackage(name, version) for name, version in RESOLVED.items()])

    def test_resolves_with_the_checkout_and_the_app(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        options = self._options(tmp_path, fake_tools)
        with (
            patch.object(engine, "install_packages", return_value=self._report()) as install,
            patch.object(engine, "validate_engine") as validate,
        ):
            build_engine(options)

        requests = install.call_args.args[1]
        assert "griptape-nodes[editor]==0.99.0" in requests
        assert f"griptape-nodes-engine @ {(tmp_path / 'engine').as_uri()}" in requests
        assert install.call_args.kwargs["provided"] == frozenset({"griptape-nodes-engine"})
        assert (tmp_path / "store" / "griptape_nodes_engine" / "0.103.0" / "package.py").is_file()
        validate.assert_called_once()

    def test_published_engine_by_default(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        options = self._options(tmp_path, fake_tools, engine_repo=None)
        with (
            patch.object(engine, "resolve_full", return_value=[ResolvedPackage("griptape-nodes-engine", "0.102.1")]),
            patch.object(engine.subprocess, "run", side_effect=_fake_download()),
            patch.object(engine, "install_packages", return_value=self._report()) as install,
            patch.object(engine, "validate_engine") as validate,
        ):
            build_engine(options)

        requests = install.call_args.args[1]
        assert "griptape-nodes-engine==0.102.1" in requests
        assert "httpx>=0.28.0,<1.0.0" in requests
        assert not any("profiling" in request or "austin" in request for request in requests)
        assert (tmp_path / "store" / "griptape_nodes_engine" / "0.102.1" / "python" / "griptape_nodes").is_dir()
        assert validate.call_args.args[2] == "0.102.1"

    def test_missing_bindings_stop_before_building_even_with_yes(
        self, tmp_path: Path, fake_tools: FakeRezTools
    ) -> None:
        fake_tools.missing = ["python-3.12", "platform"]
        with (
            patch.object(engine, "install_packages") as install,
            patch.object(engine, "check_bindings", wraps=engine.check_bindings),
            patch("griptape_nodes_rez_build.common.python_version", return_value="3.12"),
            pytest.raises(BuildError, match="rez-bind"),
        ):
            build_engine(self._options(tmp_path, fake_tools))
        install.assert_not_called()

    def test_existing_engine_is_kept_and_still_checked(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        package_file = _kept_engine(tmp_path)
        with (
            patch.object(engine, "install_packages", return_value=self._report()) as install,
            patch.object(engine, "validate_engine") as validate,
        ):
            build_engine(self._options(tmp_path, fake_tools, app_version=None))
        assert package_file.read_text(encoding="utf-8") == KEPT_ENGINE
        validate.assert_called_once()
        # This machine installs the app and editor the kept package pins, not the newest ones.
        requests = install.call_args.args[1]
        assert "griptape-nodes[editor]==0.98.0" in requests
        assert "griptape-nodes-editor-dist==0.125.0" in requests

    def test_kept_engine_refuses_another_app_version(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        _kept_engine(tmp_path)
        with (
            patch.object(engine, "install_packages") as install,
            pytest.raises(BuildError, match=r"pinned to gtn app 0\.98\.0. Pass --app-version 0\.98\.0, or --rebuild"),
        ):
            build_engine(self._options(tmp_path, fake_tools, app_version="0.99.0"))
        install.assert_not_called()

    def test_kept_engine_refuses_a_different_editor_choice(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        _kept_engine(tmp_path)
        with pytest.raises(BuildError, match="includes it"):
            build_engine(self._options(tmp_path, fake_tools, app_version=None, editor=False))

    def test_kept_engine_without_an_app_pin_asks_for_rebuild(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        _kept_engine(tmp_path, "# not written by this tool\n")
        with pytest.raises(BuildError, match="does not name the gtn app version"):
            build_engine(self._options(tmp_path, fake_tools))

    def test_kept_pins_are_read_from_platform_requires(self, tmp_path: Path) -> None:
        package_file = _kept_engine(
            tmp_path,
            "platform_requires = {\n    '*': ['griptape_nodes-0.98.0'],\n    'linux-x86_64': ['griptape_nodes-0.98.0'],\n}\n",
        )
        assert engine.read_kept_pins(package_file) == engine.KeptPins(app="0.98.0", editor=None)

    def test_failed_check_fails_the_build(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        fake_tools.env_results = [failed("conflict")]
        with (
            patch.object(engine, "install_packages", return_value=self._report()),
            pytest.raises(BuildError, match="conflict"),
        ):
            build_engine(self._options(tmp_path, fake_tools))

    def test_failed_check_removes_what_this_build_wrote(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        store = tmp_path / "store"
        new_dependency = store / "httpx" / "0.28.1"
        (new_dependency / "python-3.12").mkdir(parents=True)
        (new_dependency / "package.py").write_text("name = 'httpx'\n", encoding="utf-8")
        existing = store / "pydantic" / "2.12.0" / "package.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("name = 'pydantic'\n", encoding="utf-8")
        report = self._report()
        report.written.append(WrittenPackage(version_dir=new_dependency, variant=None))
        fake_tools.env_results = [failed("package not found: griptape_nodes-0.99.0")]

        with patch.object(engine, "install_packages", return_value=report), pytest.raises(BuildError) as raised:
            build_engine(self._options(tmp_path, fake_tools))

        message = str(raised.value)
        assert "so a plain rerun builds them again" in message
        assert "griptape_nodes_engine-0.103.0" in message
        assert not (store / "griptape_nodes_engine" / "0.103.0").exists()
        assert not new_dependency.exists()
        # Not written by this build: kept.
        assert existing.is_file()

    def test_a_failed_rebuild_puts_the_previous_engine_package_back(
        self, tmp_path: Path, fake_tools: FakeRezTools
    ) -> None:
        package_file = tmp_path / "store" / "griptape_nodes_engine" / "0.103.0" / "package.py"
        package_file.parent.mkdir(parents=True)
        package_file.write_text("# the engine package artists use today\n", encoding="utf-8")
        fake_tools.env_results = [failed("conflict")]

        with (
            patch.object(engine, "install_packages", return_value=self._report()),
            pytest.raises(BuildError, match="the version it replaced is back in place"),
        ):
            build_engine(self._options(tmp_path, fake_tools, rebuild=True))

        assert package_file.read_text(encoding="utf-8") == "# the engine package artists use today\n"
        assert [p.name for p in package_file.parent.parent.iterdir()] == ["0.103.0"]

    def test_failed_check_on_kept_packages_says_to_rebuild(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        package_file = _kept_engine(tmp_path)
        fake_tools.env_results = [failed("conflict")]
        with (
            patch.object(engine, "install_packages", return_value=self._report()),
            pytest.raises(BuildError, match="Pass --rebuild"),
        ):
            build_engine(self._options(tmp_path, fake_tools, app_version=None))
        assert package_file.is_file()
