"""Writing into a rez package store so a reader never sees a half-written package.

Rez only lists a package version once its ``package.py`` exists, and only uses a variant once
``package.py`` names it. So every write here follows one order: the payload is written to a
staging folder next to its destination (same filesystem), renamed into place, and
``package.py`` is written last, itself through a temporary file and a rename.
"""

from __future__ import annotations

import shutil
import uuid
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from pathlib import Path

PACKAGE_FILE = "package.py"

# Source trees are copied into packages without these.
COPY_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".venv",
    ".venv-exec",
    ".git",
    ".git*",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".tox",
    "dist",
    "*.egg-info",
    "build",
)


def staging_dir(parent: Path, label: str) -> Path:
    """Create and return an empty staging folder inside *parent*.

    The name starts with a dot and contains no version, so rez never takes it for a package
    version, and a failed build leaves nothing rez reads.
    """
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f".staging-{label}-{uuid.uuid4().hex[:12]}"
    path.mkdir()
    return path


def move_into_place(staged: Path, target: Path) -> None:
    """Rename *staged* to *target*, replacing a folder already there.

    The old folder is renamed aside first and deleted only after the new one is in place.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        staged.rename(target)
        return
    retired = target.parent / f".retired-{target.name}-{uuid.uuid4().hex[:12]}"
    target.rename(retired)
    staged.rename(target)
    shutil.rmtree(retired, ignore_errors=True)


def write_text_atomic(path: Path, text: str) -> None:
    """Write *path* through a temporary file in the same folder and a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex[:12]}.tmp"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def copy_tree(source: Path, destination: Path) -> None:
    """Copy a source tree into a package payload, leaving out caches, VCS data, and virtualenvs."""
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(*COPY_EXCLUDE_PATTERNS), dirs_exist_ok=True)


def package_exists(store: Path, family: str, version: str) -> bool:
    """Whether *store* has ``<family>/<version>/package.py``."""
    return (store / family / version / PACKAGE_FILE).is_file()


def package_versions(stores: list[Path], family: str) -> list[str]:
    """Every version of *family* with a ``package.py`` in *stores*, oldest first."""
    found: set[str] = set()
    for store in stores:
        family_dir = store / family
        if not family_dir.is_dir():
            continue
        for version_dir in family_dir.iterdir():
            if version_dir.is_dir() and (version_dir / PACKAGE_FILE).is_file():
                found.add(version_dir.name)
    return sorted(found, key=version_sort_key)


def newest_version(stores: list[Path], family: str) -> str | None:
    """The newest version of *family* in *stores*, or None when there is none."""
    versions = package_versions(stores, family)
    if not versions:
        return None
    return versions[-1]


def version_sort_key(version: str) -> tuple[int, Version | tuple[int, ...]]:
    """Sort key for version folder names: PEP 440 order where it applies, numeric order otherwise."""
    try:
        return (1, Version(version))
    except InvalidVersion:
        numbers = tuple(int(part) if part.isdigit() else -1 for part in version.split("."))
        return (0, numbers)
