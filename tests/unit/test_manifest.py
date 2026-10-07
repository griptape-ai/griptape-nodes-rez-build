"""Library manifests, family names, and versions."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.manifest import (
    LibraryDependency,
    family_from_url,
    family_name,
    find_manifest,
    library_family,
    library_version,
    read_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_manifest(path: Path, data: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestFindManifest:
    @pytest.mark.parametrize("name", ["griptape_nodes_library.json", "griptape-nodes-library.json"])
    def test_both_spellings(self, tmp_path: Path, name: str) -> None:
        manifest = _write_manifest(tmp_path / name, {})
        assert find_manifest(tmp_path) == manifest

    def test_shallowest_wins_and_virtualenvs_are_skipped(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path / ".venv" / "griptape_nodes_library.json", {})
        _write_manifest(tmp_path / "a" / "b" / "griptape_nodes_library.json", {})
        shallow = _write_manifest(tmp_path / "lib" / "griptape_nodes_library.json", {})
        assert find_manifest(tmp_path) == shallow

    def test_none(self, tmp_path: Path) -> None:
        assert find_manifest(tmp_path) is None


class TestReadManifest:
    def test_reads_dependencies_and_declarations(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path / "griptape_nodes_library.json",
            {
                "name": "Rez Test Library",
                "metadata": {
                    "library_version": "0.1.0",
                    "dependencies": {
                        "pip_dependencies": ["tabulate>=0.9"],
                        "pip_dependencies_exec": ["humanize>=4.9"],
                        "pip_install_flags": ["--preview"],
                    },
                    "declarations": [
                        {"type": "library_dependency", "url": "https://x/a.git"},
                        {"type": "library_dependency", "url": "https://x/b.git", "required": False},
                        {"type": "suggested_worker_mode", "mode": "worker"},
                    ],
                },
            },
        )

        manifest = read_manifest(path)

        assert manifest.name == "Rez Test Library"
        assert manifest.pip_dependencies == ["tabulate>=0.9"]
        assert manifest.pip_dependencies_exec == ["humanize>=4.9"]
        assert manifest.pip_install_flags == ["--preview"]
        assert manifest.library_version == "0.1.0"
        # Like the engine, a declaration without "required" is required.
        assert manifest.library_dependencies == [
            LibraryDependency("https://x/a.git", required=True),
            LibraryDependency("https://x/b.git", required=False),
        ]

    @pytest.mark.parametrize("name", ["A: B", "A;B", "A=B"])
    def test_names_that_break_worker_requests_are_refused(self, tmp_path: Path, name: str) -> None:
        path = _write_manifest(tmp_path / "griptape_nodes_library.json", {"name": name})
        with pytest.raises(BuildError, match="GTN_LIBRARY_WORKER_REQUESTS"):
            read_manifest(path)

    def test_unreadable_manifest(self, tmp_path: Path) -> None:
        path = tmp_path / "griptape_nodes_library.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(BuildError, match="Attempted to read the library manifest"):
            read_manifest(path)

    def test_manifest_without_name(self, tmp_path: Path) -> None:
        path = _write_manifest(tmp_path / "griptape_nodes_library.json", {"metadata": {}})
        with pytest.raises(BuildError, match="no name"):
            read_manifest(path)


class TestFamily:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [("griptape-nodes-library-standard", "griptape_nodes_library_standard"), ("My Library.v2", "my_library_v2")],
    )
    def test_family_name(self, name: str, expected: str) -> None:
        assert family_name(name) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/griptape-ai/griptape-nodes-library-standard.git",
            "git@github.com:griptape-ai/griptape-nodes-library-standard.git",
            "https://github.com/griptape-ai/griptape-nodes-library-standard/",
        ],
    )
    def test_family_from_url(self, url: str) -> None:
        assert family_from_url(url) == "griptape_nodes_library_standard"

    def test_folder_name_outside_git(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path / "griptape-nodes-library-rez-test" / "griptape_nodes_library.json", {})
        family = library_family(manifest)
        assert family.family == "griptape_nodes_library_rez_test"
        assert family.edit_family == "griptape_nodes_library_rez_test_edit"
        assert family.folder == "griptape-nodes-library-rez-test"

    def test_folder_names_rez_would_expand_are_refused(self, tmp_path: Path) -> None:
        manifest = _write_manifest(tmp_path / "nodes-${USER}" / "griptape_nodes_library.json", {})
        with pytest.raises(BuildError, match="rez would expand"):
            library_family(manifest)

    def test_git_remote_name_wins(self, tmp_path: Path) -> None:
        repo = tmp_path / "checkout"
        manifest = _write_manifest(repo / "nested" / "griptape_nodes_library.json", {})
        subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S603, S607
        subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/org/My-Nodes.git"],  # noqa: S607
            check=True,
        )
        family = library_family(manifest)
        assert family.family == "my_nodes"
        assert family.folder == "My-Nodes"
        assert "My-Nodes" in family.source


class TestLibraryVersion:
    def _manifest(self, tmp_path: Path, library_version_value: str | None = None) -> Path:
        metadata: dict[str, object] = {}
        if library_version_value is not None:
            metadata["library_version"] = library_version_value
        return _write_manifest(tmp_path / "lib" / "griptape_nodes_library.json", {"name": "L", "metadata": metadata})

    def test_pyproject_version(self, tmp_path: Path) -> None:
        path = self._manifest(tmp_path, "9.9.9")
        (path.parent / "pyproject.toml").write_text('[project]\nversion = "0.4.1"\n', encoding="utf-8")
        assert library_version(read_manifest(path)) == "0.4.1"

    def test_pyproject_up_to_three_folders_above(self, tmp_path: Path) -> None:
        path = _write_manifest(tmp_path / "a" / "b" / "c" / "griptape_nodes_library.json", {"name": "L"})
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.5.0"\n', encoding="utf-8")
        assert library_version(read_manifest(path)) == "0.5.0"

    def test_pyproject_four_folders_above_is_not_used(self, tmp_path: Path) -> None:
        path = _write_manifest(tmp_path / "a" / "b" / "c" / "d" / "griptape_nodes_library.json", {"name": "L"})
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n', encoding="utf-8")
        assert library_version(read_manifest(path)) == "1.0.0"

    def test_search_stops_at_the_repository_root(self, tmp_path: Path) -> None:
        # A library repository inside a workspace never takes the workspace's version.
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.0.0"\n', encoding="utf-8")
        repo = tmp_path / "library-repo"
        path = _write_manifest(repo / "nodes" / "griptape_nodes_library.json", {"name": "L"})
        subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S603, S607
        assert library_version(read_manifest(path)) == "1.0.0"

    def test_manifest_version_when_nothing_else(self, tmp_path: Path) -> None:
        assert library_version(read_manifest(self._manifest(tmp_path, "0.2.0"))) == "0.2.0"

    def test_default(self, tmp_path: Path) -> None:
        assert library_version(read_manifest(self._manifest(tmp_path))) == "1.0.0"

    def test_version_rez_cannot_use_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(BuildError, match="rez versions"):
            library_version(read_manifest(self._manifest(tmp_path, "1.0+local")))
