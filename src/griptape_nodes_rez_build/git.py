"""The few git operations library builds need: cloning, naming, and versions from tags."""

from __future__ import annotations

import subprocess
from pathlib import Path

from griptape_nodes_rez_build.errors import BuildError

_GIT_TIMEOUT_SECONDS = 600
_DESCRIBE_PARTS = 3


def clone(url: str, destination: Path, ref: str | None) -> None:
    """Clone *url* into *destination* and check out *ref* (a branch, tag, or commit) when given."""
    _run_or_fail(["git", "clone", "--quiet", url, str(destination)], f"clone {url}")
    if ref:
        _run_or_fail(["git", "-C", str(destination), "checkout", "--quiet", ref], f"check out {ref} in {url}")


def repository_root(directory: Path) -> Path | None:
    """The root folder of the git repository holding *directory*, or None outside one."""
    result = _run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip())


def remote_repository_name(directory: Path) -> str | None:
    """The repository name in the ``origin`` URL: ``https://github.com/org/my-repo.git`` -> ``my-repo``."""
    result = _run(["git", "-C", str(directory), "remote", "get-url", "origin"])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    return repository_name_from_url(result.stdout.strip())


def repository_name_from_url(url: str) -> str | None:
    """The repository name in a clone URL (https or ssh), or None when it names none."""
    name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    name = name.removesuffix(".git")
    return name or None


def describe_version(directory: Path) -> str | None:
    """``X.Y.Z`` on a tag, ``X.Y.Z.postN`` N commits after it, from ``git describe --tags``."""
    result = _run(["git", "-C", str(directory), "describe", "--tags", "--long"])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    raw = result.stdout.strip().lstrip("v")
    parts = raw.rsplit("-", 2)
    if len(parts) != _DESCRIBE_PARTS:
        return raw
    tag, count, _commit = parts
    tag = tag.lstrip("v")
    if count == "0":
        return tag
    return f"{tag}.post{count}"


def _run(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, check=False, timeout=_GIT_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _run_or_fail(command: list[str], action: str) -> None:
    result = _run(command)
    if result is None:
        msg = f"Attempted to {action}. Failed because git could not be run. Install git or put it on PATH."
        raise BuildError(msg)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"git exited with status {result.returncode}"
        msg = f"Attempted to {action}. Failed because: {reason}"
        raise BuildError(msg)
