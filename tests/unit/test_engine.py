"""build-engine: requires as ranges, package layout, and the check against the store."""

from __future__ import annotations

import ast
import json
from typing import TYPE_CHECKING
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
    read_engine_project,
    validate_engine,
    write_engine_package,
)
from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.rez_uv import InstallReport, ResolvedPackage
from tests.unit.conftest import FakeRezTools, failed, ok
from tests.unit.rex_fake import run_commands

if TYPE_CHECKING:
    from pathlib import Path

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


class TestEnginePackage:
    def test_content_is_valid_python_with_escaped_values(self, tmp_path: Path) -> None:
        project = EngineProject(repo=tmp_path / "it's a \\", version="0.103.0", dependencies=[], requires_python="")
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

        version_dir = write_engine_package(project, PackageRequires(common=[], platform=None), store=store)

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
        assert f"griptape-nodes-engine @ {options.engine_repo.as_uri()}" in requests
        assert install.call_args.kwargs["provided"] == frozenset({"griptape-nodes-engine"})
        assert (tmp_path / "store" / "griptape_nodes_engine" / "0.103.0" / "package.py").is_file()
        validate.assert_called_once()

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
        package_file = tmp_path / "store" / "griptape_nodes_engine" / "0.103.0" / "package.py"
        package_file.parent.mkdir(parents=True)
        package_file.write_text("# from another platform's build\n", encoding="utf-8")
        with (
            patch.object(engine, "install_packages", return_value=self._report()),
            patch.object(engine, "validate_engine") as validate,
        ):
            build_engine(self._options(tmp_path, fake_tools))
        assert package_file.read_text(encoding="utf-8") == "# from another platform's build\n"
        validate.assert_called_once()

    def test_failed_check_fails_the_build(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        fake_tools.env_results = [failed("conflict")]
        with (
            patch.object(engine, "install_packages", return_value=self._report()),
            pytest.raises(BuildError, match="conflict"),
        ):
            build_engine(self._options(tmp_path, fake_tools))
