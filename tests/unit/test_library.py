"""build-library: package layout, torch passes, library dependencies, and checks."""

from __future__ import annotations

import ast
import json
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from griptape_nodes_rez_build import library
from griptape_nodes_rez_build.common import PackageRequires
from griptape_nodes_rez_build.errors import BuildError, ResolveError
from griptape_nodes_rez_build.library import (
    LibraryBuildOptions,
    TorchPlan,
    build_library_dependencies,
    edit_package_content,
    exec_package_content,
    plan_torch_passes,
    validate_library,
    write_library_packages,
)
from griptape_nodes_rez_build.manifest import LibraryDependency, LibraryFamily, LibraryManifest
from griptape_nodes_rez_build.rez_uv import InstallReport, ResolvedPackage
from tests.unit.conftest import FakeRezTools, failed, ok
from tests.unit.rex_fake import run_commands

if TYPE_CHECKING:
    from pathlib import Path

AWKWARD_NAME = 'Rez\'s "Test" Library \\'
FAMILY = LibraryFamily(family="rez_test", source="folder 'rez-test'", folder="rez-test")


def _manifest(tmp_path: Path, **overrides: object) -> LibraryManifest:
    library_dir = tmp_path / "rez-test"
    library_dir.mkdir(exist_ok=True)
    manifest_path = library_dir / "griptape_nodes_library.json"
    manifest_path.write_text("{}", encoding="utf-8")
    (library_dir / "nodes.py").write_text("x = 1\n", encoding="utf-8")
    fields: dict[str, object] = {
        "path": manifest_path,
        "name": "Rez Test Library",
        "pip_dependencies": ["tabulate>=0.9"],
        "pip_dependencies_exec": ["humanize>=4.9"],
        "pip_install_flags": [],
        "library_dependencies": [],
        "library_version": None,
    }
    fields.update(overrides)
    return LibraryManifest(**fields)  # type: ignore[arg-type]


def _options(tmp_path: Path, tools: FakeRezTools, **overrides: object) -> LibraryBuildOptions:
    fields: dict[str, object] = {
        "store": tmp_path / "store",
        "tools": tools,
        "torch_backends": None,
        "rebuild": False,
        "interactive": False,
    }
    fields.update(overrides)
    return LibraryBuildOptions(**fields)  # type: ignore[arg-type]


def _assignments(content: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for node in ast.parse(content).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            values[node.targets[0].id] = ast.literal_eval(node.value)
    return values


class TestEditPackage:
    def test_publishes_manifest_and_worker_request(self, tmp_path: Path) -> None:
        requires = PackageRequires(common=["griptape_nodes_library_standard_edit", "tabulate-0.9+"], platform=None)
        content = edit_package_content(_manifest(tmp_path), FAMILY, "0.1.0", requires)

        values = _assignments(content)
        assert values["name"] == "rez_test_edit"
        assert values["version"] == "0.1.0"
        assert values["requires"] == ["griptape_nodes_library_standard_edit", "tabulate-0.9+"]
        run = run_commands(content, root="/store/rez_test_edit/0.1.0")
        # Under the repository/folder name, which is how the engine recognises a library dependency.
        assert run.environ["PYTHONPATH"] == "/store/rez_test_edit/0.1.0/python/rez-test"
        assert (
            run.environ["GTN_LIBRARY_PATHS"] == "/store/rez_test_edit/0.1.0/python/rez-test/griptape_nodes_library.json"
        )
        assert run.environ["GTN_LIBRARY_WORKER_REQUESTS"] == "Rez Test Library=rez_test==0.1.0"
        # Written with literal(), so rez expands nothing in the library's name.
        assert run.literals == ["Rez Test Library=rez_test==0.1.0"]

    def test_awkward_library_name_round_trips(self, tmp_path: Path) -> None:
        content = edit_package_content(
            _manifest(tmp_path, name=AWKWARD_NAME), FAMILY, "0.1.0", PackageRequires(common=[], platform=None)
        )

        assert _assignments(content)["library_name"] == AWKWARD_NAME
        run = run_commands(content)
        assert run.environ["GTN_LIBRARY_WORKER_REQUESTS"] == f"{AWKWARD_NAME}=rez_test==0.1.0"

    def test_platform_requires_are_chosen_when_rez_resolves(self, tmp_path: Path) -> None:
        requires = PackageRequires(common=["a"], platform={"*": ["a"], "windows-AMD64": ["a", "pywin32"]})
        content = edit_package_content(_manifest(tmp_path), FAMILY, "0.1.0", requires)

        assert _assignments(content)["platform_requires"] == {"*": ["a"], "windows-AMD64": ["a", "pywin32"]}
        assert "@late()" in content


class TestExecPackage:
    def test_requires_the_edit_package_and_execution_dependencies(self, tmp_path: Path) -> None:
        requires = PackageRequires(common=["humanize-4.9+", "rez_test_edit==0.1.0"], platform=None)
        content = exec_package_content(_manifest(tmp_path), FAMILY, "0.1.0", requires)

        values = _assignments(content)
        assert values["name"] == "rez_test"
        assert values["requires"] == ["humanize-4.9+", "rez_test_edit==0.1.0"]
        assert "def commands" not in content


class TestWriteLibraryPackages:
    def test_edit_package_holds_the_source_and_the_full_package_only_package_py(self, tmp_path: Path) -> None:
        manifest = _manifest(tmp_path)
        store = tmp_path / "store"
        requires = PackageRequires(common=[], platform=None)

        write_library_packages(
            manifest, FAMILY, "0.1.0", edit_requires=requires, exec_requires=requires, store=store, rebuild=False
        )

        edit_dir = store / "rez_test_edit" / "0.1.0"
        assert (edit_dir / "python" / "rez-test" / "griptape_nodes_library.json").is_file()
        assert (edit_dir / "python" / "rez-test" / "nodes.py").is_file()
        assert sorted(p.name for p in (store / "rez_test" / "0.1.0").iterdir()) == ["package.py"]
        # Staging folders are gone.
        assert [p.name for p in (store / "rez_test_edit").iterdir()] == ["0.1.0"]

    def test_existing_packages_are_kept_unless_rebuilt(self, tmp_path: Path) -> None:
        manifest = _manifest(tmp_path)
        store = tmp_path / "store"
        package_file = store / "rez_test_edit" / "0.1.0" / "package.py"
        package_file.parent.mkdir(parents=True)
        package_file.write_text("name = 'rez_test_edit'\n", encoding="utf-8")
        requires = PackageRequires(common=[], platform=None)

        write_library_packages(
            manifest, FAMILY, "0.1.0", edit_requires=requires, exec_requires=requires, store=store, rebuild=False
        )
        assert package_file.read_text(encoding="utf-8") == "name = 'rez_test_edit'\n"
        assert (store / "rez_test" / "0.1.0" / "package.py").is_file()

        write_library_packages(
            manifest, FAMILY, "0.1.0", edit_requires=requires, exec_requires=requires, store=store, rebuild=True
        )
        assert "GTN_LIBRARY_PATHS" in package_file.read_text(encoding="utf-8")


class TestTorchPlan:
    def test_manifest_auto_backend_is_refused(self) -> None:
        with pytest.raises(BuildError, match="--torch-backend=auto"):
            plan_torch_passes(["--torch-backend=auto"], None, platform="linux")

    def test_separate_auto_value_is_refused(self) -> None:
        with pytest.raises(BuildError, match="choose torch for this machine"):
            plan_torch_passes(["--torch-backend", "auto"], None, platform="win32")

    def test_uv_torch_backend_in_the_shell_is_refused(self) -> None:
        with patch.dict(os.environ, {"UV_TORCH_BACKEND": "auto"}), pytest.raises(BuildError, match="UV_TORCH_BACKEND"):
            plan_torch_passes([], ["cu128"], platform="linux")

    def test_explicit_backends_replace_the_manifest_source(self) -> None:
        plan = plan_torch_passes(["--torch-backend=auto"], ["cu128", "cpu", "cu128"], platform="linux")

        assert [torch_pass.backend for torch_pass in plan.passes] == ["cu128", "cpu"]
        assert "--torch-backend=auto" not in plan.passes[0].flags
        assert "https://download.pytorch.org/whl/cu128" in plan.passes[0].flags
        assert plan.skip_unavailable is False

    def test_all_tries_every_build(self) -> None:
        plan = plan_torch_passes([], ["all"], platform="linux")
        assert len(plan.passes) == len(library.TORCH_BACKENDS)
        assert plan.skip_unavailable is True

    def test_unknown_backend_is_refused(self) -> None:
        with pytest.raises(BuildError, match="cu999"):
            plan_torch_passes([], ["cu999"], platform="linux")

    def test_macos_has_one_build_and_drops_torch_backend_flags(self) -> None:
        plan = plan_torch_passes(["--torch-backend", "auto", "--preview"], ["cu128"], platform="darwin")
        assert plan == TorchPlan(passes=[library.TorchPass(backend=None, flags=["--preview"])], skip_unavailable=False)

    def test_manifest_flags_without_auto_are_kept(self) -> None:
        flags = ["--extra-index-url", "https://download.pytorch.org/whl/cu128"]
        plan = plan_torch_passes(flags, None, platform="linux")
        assert plan.passes == [library.TorchPass(backend=None, flags=flags)]


class TestInstallDependencies:
    def test_builds_come_from_what_uv_resolved(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        # A manifest that pins the cu128 index gets the cu128 variant, even without --torch-backend.
        report = InstallReport(resolved=[ResolvedPackage("torch", "2.7.0", local_version="cu128")])
        plan = TorchPlan(passes=[library.TorchPass(backend=None, flags=[])], skip_unavailable=False)
        with patch.object(library, "install_packages", return_value=report):
            installed = library._install_dependencies(_manifest(tmp_path), plan, _options(tmp_path, fake_tools))
        assert installed.torch_backends == ["cu128"]

    def test_all_skips_builds_that_do_not_resolve(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        plan = plan_torch_passes([], ["cu118", "cu128"], platform="linux")
        plan = TorchPlan(passes=plan.passes, skip_unavailable=True)
        cu128 = InstallReport(resolved=[ResolvedPackage("torch", "2.7.0", local_version="cu128")])
        with patch.object(library, "install_packages", side_effect=[ResolveError("no cu118 wheel"), cu128]):
            installed = library._install_dependencies(_manifest(tmp_path), plan, _options(tmp_path, fake_tools))
        assert installed.torch_backends == ["cu128"]

    def test_resolve_failure_without_all_stops(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        plan = TorchPlan(passes=[library.TorchPass(backend="cu118", flags=[])], skip_unavailable=False)
        with (
            patch.object(library, "install_packages", side_effect=ResolveError("no cu118 wheel")),
            pytest.raises(ResolveError),
        ):
            library._install_dependencies(_manifest(tmp_path), plan, _options(tmp_path, fake_tools))


class TestLibraryDependencies:
    STANDARD = "https://github.com/griptape-ai/griptape-nodes-library-standard.git"
    EXTRA = "https://github.com/example/extra-nodes.git"

    def test_required_and_optional_dependencies_become_requires(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        manifest = _manifest(
            tmp_path,
            library_dependencies=[
                LibraryDependency(self.STANDARD, required=True),
                LibraryDependency(self.EXTRA, required=False),
            ],
        )
        with patch.object(library, "build_library_from_git") as build:
            requires = build_library_dependencies(manifest, _options(tmp_path, fake_tools))

        assert requires == ["griptape_nodes_library_standard_edit", "~extra_nodes_edit"]
        assert [call.args[0] for call in build.call_args_list] == [self.STANDARD, self.EXTRA]
        assert all(call.args[2].interactive is False for call in build.call_args_list)

    def test_dependency_already_in_the_store_is_built_only_with_rebuild(
        self, tmp_path: Path, fake_tools: FakeRezTools
    ) -> None:
        existing = tmp_path / "store" / "griptape_nodes_library_standard_edit" / "0.86.0" / "package.py"
        existing.parent.mkdir(parents=True)
        existing.write_text("", encoding="utf-8")
        manifest = _manifest(tmp_path, library_dependencies=[LibraryDependency(self.STANDARD, required=True)])

        with patch.object(library, "build_library_from_git") as build:
            build_library_dependencies(manifest, _options(tmp_path, fake_tools))
        build.assert_not_called()

        with patch.object(library, "build_library_from_git") as build:
            build_library_dependencies(manifest, _options(tmp_path, fake_tools, rebuild=True))
        assert build.call_args.args[2].rebuild is True

    def test_failed_required_dependency_stops(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        manifest = _manifest(tmp_path, library_dependencies=[LibraryDependency(self.STANDARD, required=True)])
        with (
            patch.object(library, "build_library_from_git", side_effect=BuildError("clone failed")),
            pytest.raises(BuildError, match="required library dependency"),
        ):
            build_library_dependencies(manifest, _options(tmp_path, fake_tools))

    def test_failed_optional_dependency_continues(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        manifest = _manifest(tmp_path, library_dependencies=[LibraryDependency(self.EXTRA, required=False)])
        with patch.object(library, "build_library_from_git", side_effect=BuildError("clone failed")):
            assert build_library_dependencies(manifest, _options(tmp_path, fake_tools)) == ["~extra_nodes_edit"]

    def test_cycle_is_not_followed(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        manifest = _manifest(tmp_path, library_dependencies=[LibraryDependency(self.STANDARD, required=True)])
        options = _options(tmp_path, fake_tools, visited=frozenset({self.STANDARD}))
        with patch.object(library, "build_library_from_git") as build:
            assert build_library_dependencies(manifest, options) == ["griptape_nodes_library_standard_edit"]
        build.assert_not_called()


class TestValidateLibrary:
    def _published(self, store: Path, request: str) -> str:
        manifest = store / "rez_test_edit" / "0.1.0" / "python" / "rez-test" / "griptape_nodes_library.json"
        return json.dumps({"GTN_LIBRARY_PATHS": str(manifest), "GTN_LIBRARY_WORKER_REQUESTS": request})

    def _store_with_manifest(self, tmp_path: Path) -> Path:
        store = tmp_path / "store"
        manifest = store / "rez_test_edit" / "0.1.0" / "python" / "rez-test" / "griptape_nodes_library.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}", encoding="utf-8")
        return store

    def test_both_packages_are_resolved_and_checked(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        store = self._store_with_manifest(tmp_path)
        published = self._published(store, "Rez Test Library=rez_test==0.1.0")
        fake_tools.env_results = [ok(published), ok(published)]

        validate_library(
            fake_tools, [store], _manifest(tmp_path), FAMILY, version="0.1.0", torch_backends=["cu118", "cu128"]
        )

        requests = [call.request for call in fake_tools.env_calls]
        assert requests[0][0] == "rez_test_edit==0.1.0"
        assert requests[1][0] == "rez_test==0.1.0"
        # A check on a machine without implicit_packages names the build, as the guard asks.
        assert requests[1][-1] == ".torch_backend-cu118"

    def test_failed_resolve_is_a_failed_build(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        fake_tools.env_results = [failed("package not found: tabulate-0.9+")]
        with pytest.raises(BuildError, match="tabulate"):
            validate_library(fake_tools, [tmp_path], _manifest(tmp_path), FAMILY, version="0.1.0", torch_backends=[])

    def test_missing_worker_request_is_a_failed_build(self, tmp_path: Path, fake_tools: FakeRezTools) -> None:
        store = self._store_with_manifest(tmp_path)
        fake_tools.env_results = [ok(self._published(store, "Other=other==1.0"))]
        with pytest.raises(BuildError, match="GTN_LIBRARY_WORKER_REQUESTS"):
            validate_library(fake_tools, [store], _manifest(tmp_path), FAMILY, version="0.1.0", torch_backends=[])
