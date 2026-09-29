"""Reading a node library's manifest, and naming and versioning its rez packages.

A library's packages are named after the repository or folder it was built from
(``griptape-nodes-library-standard`` -> ``griptape_nodes_library_standard``), never its
display name, so an administrator can match packages to their sources.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from griptape_nodes_rez_build import git
from griptape_nodes_rez_build.errors import BuildError

if TYPE_CHECKING:
    from pathlib import Path

MANIFEST_PATTERN = re.compile(r"^griptape[-_]nodes[-_]library\.json$")
EDIT_SUFFIX = "_edit"
DEFAULT_LIBRARY_VERSION = "1.0.0"

# GTN_LIBRARY_WORKER_REQUESTS entries are `<name>=<request>` joined by the platform's path
# separator, so a library name must not contain either separator or `=`.
_FORBIDDEN_NAME_CHARACTERS = (":", ";", "=")
# rez expands {name} and $NAME in commands() values, and these become part of package paths.
_FORBIDDEN_FOLDER_CHARACTERS = ("{", "}", "$")
_REZ_VERSION = re.compile(r"^[A-Za-z0-9_]+([.\-][A-Za-z0-9_]+)*$")
_SKIPPED_FOLDERS = {".git", ".venv", ".venv-exec", "node_modules", "__pycache__", "build", "dist"}
_VERSION_SEARCH_DEPTH = 4


@dataclass(frozen=True)
class LibraryDependency:
    """Another node library this one declares (``library_dependency`` in ``metadata.declarations``)."""

    url: str
    required: bool


@dataclass(frozen=True)
class LibraryManifest:
    path: Path
    name: str
    pip_dependencies: list[str]
    pip_dependencies_exec: list[str]
    pip_install_flags: list[str]
    library_dependencies: list[LibraryDependency]
    library_version: str | None


@dataclass(frozen=True)
class LibraryFamily:
    """A library's rez family (``<family>``; the edit package is ``<family>_edit``) and where the name came from.

    ``folder`` is the repository or folder name itself (``griptape-nodes-library-standard``). The
    edit package keeps the library under ``python/<folder>/``, because the engine matches a
    ``library_dependency`` URL to an installed library by that name appearing as a folder in the
    manifest's path.
    """

    family: str
    source: str
    folder: str

    @property
    def edit_family(self) -> str:
        return f"{self.family}{EDIT_SUFFIX}"


def find_manifest(directory: Path) -> Path | None:
    """The library manifest in *directory*, or the shallowest one below it.

    Matches both spellings used across library repositories: ``griptape_nodes_library.json``
    and ``griptape-nodes-library.json``.
    """
    candidates: list[Path] = []
    pending = [directory]
    while pending:
        folder = pending.pop()
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and entry.name not in _SKIPPED_FOLDERS:
                pending.append(entry)
            elif entry.is_file() and MANIFEST_PATTERN.match(entry.name):
                candidates.append(entry)
    if not candidates:
        return None
    return min(candidates, key=lambda path: (len(path.relative_to(directory).parts), str(path)))


def read_manifest(path: Path) -> LibraryManifest:
    """Read the parts of a library manifest the build uses, refusing names the engine cannot parse."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"Attempted to read the library manifest {path}. Failed because: {exc}"
        raise BuildError(msg) from exc
    if not isinstance(data, dict):
        msg = f"Attempted to read the library manifest {path}. Failed because it is not a JSON object."
        raise BuildError(msg)

    name = str(data.get("name", "")).strip()
    if not name:
        msg = f"Attempted to read the library manifest {path}. Failed because it has no name."
        raise BuildError(msg)
    bad = [character for character in _FORBIDDEN_NAME_CHARACTERS if character in name]
    if bad:
        msg = (
            f"Attempted to package the library '{name}'. Failed because its name contains {' '.join(bad)}, which "
            "separate entries in GTN_LIBRARY_WORKER_REQUESTS. Rename the library in its manifest."
        )
        raise BuildError(msg)

    metadata = data.get("metadata") or {}
    dependencies = metadata.get("dependencies") or {}
    declarations = metadata.get("declarations") or []
    library_version = metadata.get("library_version")
    return LibraryManifest(
        path=path,
        name=name,
        pip_dependencies=[str(spec) for spec in dependencies.get("pip_dependencies") or []],
        pip_dependencies_exec=[str(spec) for spec in dependencies.get("pip_dependencies_exec") or []],
        pip_install_flags=[str(flag) for flag in dependencies.get("pip_install_flags") or []],
        library_dependencies=[
            # The engine treats a declaration without "required" as required.
            LibraryDependency(url=str(declaration["url"]), required=bool(declaration.get("required", True)))
            for declaration in declarations
            if isinstance(declaration, dict)
            and declaration.get("type") == "library_dependency"
            and declaration.get("url")
        ],
        library_version=str(library_version) if library_version else None,
    )


def library_family(manifest_path: Path) -> LibraryFamily:
    """Name a library's packages after its git remote, git root folder, or folder, in that order."""
    library_dir = manifest_path.parent
    if git.repository_root(library_dir) is not None:
        remote = git.remote_repository_name(library_dir)
        if remote:
            return _family_for(remote, f"git remote repository '{remote}'")
        root = git.repository_root(library_dir)
        if root is not None:
            return _family_for(root.name, f"git repository folder '{root.name}'")
    return _family_for(library_dir.name, f"folder '{library_dir.name}'")


def _family_for(name: str, source: str) -> LibraryFamily:
    bad = [character for character in _FORBIDDEN_FOLDER_CHARACTERS if character in name]
    if bad:
        msg = (
            f"Attempted to package the library from '{name}'. Failed because the name contains {' '.join(bad)}, "
            "which rez would expand in the package's paths. Rename the folder or repository."
        )
        raise BuildError(msg)
    return LibraryFamily(family=family_name(name), source=source, folder=name)


def family_from_url(url: str) -> str:
    """The family a library dependency's package gets, from its repository URL."""
    name = git.repository_name_from_url(url)
    if name is None:
        msg = f"Attempted to name the library dependency {url}. Failed because the URL names no repository."
        raise BuildError(msg)
    return family_name(name)


def family_name(name: str) -> str:
    """Lowercase, with hyphens, dots, and spaces as underscores."""
    return re.sub(r"[-._\s]+", "_", name).lower().strip("_")


def library_version(manifest: LibraryManifest) -> str:
    """The version the library's packages get.

    In order: ``pyproject.toml`` next to the manifest or up to four folders above it,
    ``git describe --tags``, the manifest's ``library_version``, then ``1.0.0``.
    """
    version = _pyproject_version(manifest.path.parent)
    if version is None:
        version = git.describe_version(manifest.path.parent)
    if version is None:
        version = manifest.library_version
    if version is None:
        version = DEFAULT_LIBRARY_VERSION
    if not _REZ_VERSION.match(version):
        msg = (
            f"Attempted to version the rez packages of '{manifest.name}' as '{version}'. Failed because rez versions "
            "use only letters, digits, and underscores separated by '.' or '-'."
        )
        raise BuildError(msg)
    return version


def _pyproject_version(start: Path) -> str | None:
    folder = start
    for _ in range(_VERSION_SEARCH_DEPTH):
        pyproject = folder / "pyproject.toml"
        if pyproject.is_file():
            try:
                with pyproject.open("rb") as handle:
                    data = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError):
                data = {}
            version = data.get("project", {}).get("version") or data.get("tool", {}).get("poetry", {}).get("version")
            if version:
                return str(version)
        if folder.parent == folder:
            break
        folder = folder.parent
    return None
