"""Writing into a rez package store so a reader never sees a half-written package.

Rez only lists a package version once its ``package.py`` exists, and only uses a variant once
``package.py`` names it. So every write here follows one order: the payload is written to a
staging folder next to its destination (same filesystem), renamed into place, and
``package.py`` is written last, itself through a temporary file and a rename.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

PACKAGE_FILE = "package.py"

_VARIANTS_LINE = re.compile(r"^variants = (.*)$", re.MULTILINE)


@dataclass(frozen=True)
class WrittenPackage:
    """Something one build wrote into a store: a whole package version, or one variant of it.

    ``variant`` is None when the build created the version (or replaced it with --rebuild); a
    variant is recorded when the build added it to a version another build had created, or
    replaced this machine's variant of it (--rebuild).
    """

    version_dir: Path
    variant: list[str] | None
    # The version's package.py before this build added the variant, restored on roll back.
    previous_package_file: str | None = None
    # The version (or variant) this build replaced (--rebuild), kept aside until the build's check
    # passes: moved back on roll back, deleted by discard_retired.
    retired: Path | None = None

    @property
    def label(self) -> str:
        name = f"{self.version_dir.parent.name}-{self.version_dir.name}"
        if self.variant is None:
            return name
        return f"{name} variant {self.variant}"


def roll_back(written: list[WrittenPackage]) -> list[str]:
    """Remove what one build wrote, newest first; returns what was removed.

    Variants another build added, and versions another build created, are left alone: a variant
    this build added is removed and its version's ``package.py`` is put back as it was before the
    build (or, when that is not known, the variant is dropped from it; the version goes only when
    no variant is left). A version or variant this build replaced is moved back into place.
    """
    removed: list[str] = []
    for item in reversed(written):
        if item.variant is None and item.retired is not None and item.retired.exists():
            shutil.rmtree(item.version_dir, ignore_errors=True)
            item.retired.rename(item.version_dir)
            removed.append(f"{item.label} (the version it replaced is back in place)")
            continue
        if item.variant is not None and item.retired is not None and item.retired.exists():
            variant_dir = item.version_dir.joinpath(*item.variant)
            if item.previous_package_file is not None:
                write_text_atomic(item.version_dir / PACKAGE_FILE, item.previous_package_file)
            shutil.rmtree(variant_dir, ignore_errors=True)
            item.retired.rename(variant_dir)
            removed.append(f"{item.label} (the variant it replaced is back in place)")
            continue
        if not item.version_dir.exists():
            continue
        if item.variant is None:
            shutil.rmtree(item.version_dir, ignore_errors=True)
            removed.append(item.label)
            continue
        if item.previous_package_file is not None:
            write_text_atomic(item.version_dir / PACKAGE_FILE, item.previous_package_file)
            shutil.rmtree(item.version_dir.joinpath(*item.variant), ignore_errors=True)
        else:
            _remove_variant(item.version_dir, item.variant)
        _remove_empty_parents(item.version_dir.joinpath(*item.variant).parent, stop=item.version_dir)
        removed.append(item.label)
    for family_dir in {item.version_dir.parent for item in written}:
        if family_dir.is_dir() and not any(family_dir.iterdir()):
            family_dir.rmdir()
    return removed


# Source trees are copied into packages without these, at any depth.
COPY_EXCLUDE_PATTERNS: tuple[str, ...] = (
    ".venv",
    ".venv-exec",
    ".git",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".tox",
)
# Left out only at the top of the tree, where they are build output and repository files. Deeper
# down a folder named build or dist can be part of the library (nodes/build/, data/dist/).
COPY_EXCLUDE_TOP_LEVEL_PATTERNS: tuple[str, ...] = (
    ".git*",
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


def move_into_place(staged: Path, target: Path) -> Path | None:
    """Rename *staged* to *target*, replacing a folder already there.

    The old folder is renamed aside first and returned, not deleted: a build keeps it until its
    check passes, so a failed check can put it back (see ``roll_back`` and ``discard_retired``).
    The name starts with a dot, so rez never reads it. Returns None when nothing was replaced.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        staged.rename(target)
        return None
    retired = target.parent / f".retired-{target.name}-{uuid.uuid4().hex[:12]}"
    target.rename(retired)
    staged.rename(target)
    return retired


def discard_retired(written: list[WrittenPackage]) -> None:
    """Delete the versions a build replaced, once its check has passed."""
    for item in written:
        if item.retired is not None:
            shutil.rmtree(item.retired, ignore_errors=True)


def write_text_atomic(path: Path, text: str) -> None:
    """Write *path* through a temporary file in the same folder and a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex[:12]}.tmp"
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def copy_tree(source: Path, destination: Path) -> None:
    """Copy a source tree into a package payload, leaving out caches, VCS data, virtualenvs, and build output."""
    everywhere = shutil.ignore_patterns(*COPY_EXCLUDE_PATTERNS)
    top_level = shutil.ignore_patterns(*COPY_EXCLUDE_TOP_LEVEL_PATTERNS)
    source_key = os.path.normcase(str(source.absolute()))

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(everywhere(directory, names))
        if os.path.normcase(str(Path(directory).absolute())) == source_key:
            ignored |= set(top_level(directory, names))
        return ignored

    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)


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
        return (0, tuple(_numeric_part(part) for part in version.split(".")))


def _numeric_part(part: str) -> int:
    """A version part as a number for sorting; a part that is not a number sorts first."""
    if part.isdigit():
        return int(part)
    return -1


def _remove_variant(version_dir: Path, variant: list[str]) -> None:
    """Drop *variant* from a generated ``package.py`` (written first) and delete its folder."""
    package_file = version_dir / PACKAGE_FILE
    content = ""
    if package_file.is_file():
        content = package_file.read_text(encoding="utf-8")
    match = _VARIANTS_LINE.search(content)
    if match is None:
        shutil.rmtree(version_dir, ignore_errors=True)
        return
    remaining = [list(entry) for entry in ast.literal_eval(match.group(1)) if list(entry) != variant]
    if not remaining:
        shutil.rmtree(version_dir, ignore_errors=True)
        return
    write_text_atomic(package_file, content[: match.start()] + f"variants = {remaining!r}" + content[match.end() :])
    shutil.rmtree(version_dir.joinpath(*variant), ignore_errors=True)


def _remove_empty_parents(folder: Path, *, stop: Path) -> None:
    """Remove *folder* and its parents up to (not including) *stop* while they are empty."""
    current = folder
    while current != stop and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent
