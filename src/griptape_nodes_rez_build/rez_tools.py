"""Running rez's own tools to check what a build wrote.

The build tool never changes rez configuration. It runs rez with the environment it was
started in (so a studio's ``REZ_CONFIG_FILE`` applies unchanged) and points each check at the
store being built with ``--paths``: the store first, then rez's configured package search
path, which holds the platform, arch, os, and python bindings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from griptape_nodes_rez_build.errors import BuildError

# Where to find rez's tools when --rez-tools is not given.
ENV_REZ_TOOLS = "GRIPTAPE_NODES_REZ_TOOLS"

_RESOLVE_TIMEOUT_SECONDS = 600
_QUERY_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class RezResult:
    """What a rez command printed, and whether it succeeded."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0

    def failure_summary(self, lines: int = 8) -> str:
        """The last lines rez printed, which name the conflict or missing package."""
        text = (self.stderr or self.stdout).strip()
        if not text:
            return f"rez exited with status {self.returncode}"
        return "\n".join(text.splitlines()[-lines:])


class RezTools:
    """The folder holding rez's command-line tools (``rez``, ``rez-env``, ``rez-config``)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @property
    def rez(self) -> Path:
        return self._tool("rez")

    def search_paths(self) -> list[Path]:
        """Rez's configured package search path (``packages_path``), read-only."""
        result = self.run(["rez-config", "--json", "packages_path"], timeout=_QUERY_TIMEOUT_SECONDS)
        if not result.succeeded:
            msg = (
                "Attempted to read rez's package search path with rez-config. Failed because rez reported:\n"
                f"{result.failure_summary()}"
            )
            raise BuildError(msg)
        try:
            paths = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            msg = f"Attempted to read rez's package search path. Failed because rez-config printed: {result.stdout!r}"
            raise BuildError(msg) from exc
        return [Path(path) for path in paths]

    def paths_with(self, store: Path) -> list[Path]:
        """*store* followed by rez's configured search path, without duplicates."""
        return [store, *(path for path in self.search_paths() if not _same_path(path, store))]

    def searches(self, store: Path) -> bool:
        """Whether rez's configured search path includes *store*."""
        return any(_same_path(path, store) for path in self.search_paths())

    def env(
        self,
        request: list[str],
        command: list[str],
        *,
        paths: list[Path] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> RezResult:
        """``rez env --norc [--paths ...] <request> -- <command>``: resolve and run one command inside."""
        args = ["env", "--norc"]
        if paths is not None:
            args += ["--paths", os.pathsep.join(str(path) for path in paths)]
        return self.run(["rez", *args, *request, "--", *command], timeout=_RESOLVE_TIMEOUT_SECONDS, extra_env=extra_env)

    def resolves(self, request: list[str], *, paths: list[Path]) -> RezResult:
        """Resolve *request* without running anything in it (``rez env --output``)."""
        with tempfile.TemporaryDirectory(prefix="griptape_rez_resolve_") as temporary:
            context = Path(temporary) / "context.rxt"
            args = ["rez", "env", "--norc", "--paths", os.pathsep.join(str(path) for path in paths), *request]
            return self.run([*args, "--output", str(context)], timeout=_RESOLVE_TIMEOUT_SECONDS)

    def missing_families(self, families: list[str], *, paths: list[Path]) -> list[str]:
        """Which of *families* (``python-3.12``) rez cannot find on *paths*."""
        missing: list[str] = []
        for family in families:
            result = self.run(
                ["rez-search", "--paths", os.pathsep.join(str(path) for path in paths), family],
                timeout=_QUERY_TIMEOUT_SECONDS,
            )
            if not result.succeeded or not result.stdout.strip():
                missing.append(family)
        return missing

    def run(self, args: list[str], *, timeout: int, extra_env: dict[str, str] | None = None) -> RezResult:
        """Run a rez tool from this folder with the environment the build tool was started in (plus *extra_env*)."""
        command = [str(self._tool(args[0])), *args[1:]]
        environment = None
        if extra_env:
            environment = {**os.environ, **extra_env}
        try:
            completed = subprocess.run(  # noqa: S603
                command, capture_output=True, text=True, check=False, timeout=timeout, env=environment
            )
        except (OSError, subprocess.SubprocessError) as exc:
            msg = f"Attempted to run {args[0]} from {self.folder}. Failed because: {exc}"
            raise BuildError(msg) from exc
        return RezResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)

    def _tool(self, name: str) -> Path:
        for candidate in _executable_names(name):
            path = self.folder / candidate
            if path.is_file():
                return path
        return self.folder / name

    @classmethod
    def locate(cls, explicit: str | None) -> RezTools:
        """Rez's tools from ``--rez-tools``, else ``GRIPTAPE_NODES_REZ_TOOLS``, else ``rez-env`` on ``PATH``."""
        value = explicit or os.environ.get(ENV_REZ_TOOLS, "").strip()
        if value:
            folder = Path(value).expanduser()
            tools = cls(folder)
            if not tools._tool("rez-env").is_file():
                msg = (
                    f"Attempted to use rez's tools in {folder}. Failed because there is no rez-env there. "
                    "Pass --rez-tools with the folder that holds rez-env (in a standard rez install, <prefix>/bin/rez)."
                )
                raise BuildError(msg)
            return tools
        found = shutil.which("rez-env")
        if found is None:
            msg = (
                "Attempted to find rez's tools. Failed because rez-env is not on PATH and neither --rez-tools nor "
                f"{ENV_REZ_TOOLS} is set. Every build is checked with rez before it counts as done, so rez is required."
            )
            raise BuildError(msg)
        return cls(Path(found).parent)


def _executable_names(name: str) -> list[str]:
    if os.name != "nt":
        return [name]
    return [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name]


def _same_path(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except OSError:
        return first == second
