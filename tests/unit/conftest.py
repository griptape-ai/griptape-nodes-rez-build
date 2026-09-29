"""Shared test doubles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from griptape_nodes_rez_build.rez_tools import RezResult, RezTools

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class EnvCall:
    request: list[str]
    command: list[str]
    paths: list[Path] | None
    extra_env: dict[str, str] | None = None


@dataclass
class FakeRezTools(RezTools):
    """Rez's tools, with every command answered from lists instead of run."""

    folder: Path = field(default_factory=lambda: Path("/rez/bin"))
    env_results: list[RezResult] = field(default_factory=list)
    configured_paths: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    env_calls: list[EnvCall] = field(default_factory=list)
    resolve_calls: list[list[str]] = field(default_factory=list)
    # Answers resolves(): every request resolves unless the test replaces this.
    resolve_answer: Callable[[list[str]], RezResult] = field(default=lambda _request: RezResult(0, "", ""))

    def search_paths(self) -> list[Path]:
        return list(self.configured_paths)

    def env(
        self,
        request: list[str],
        command: list[str],
        *,
        paths: list[Path] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> RezResult:
        self.env_calls.append(EnvCall(request=request, command=command, paths=paths, extra_env=extra_env))
        return self.env_results.pop(0)

    def searches(self, store: Path) -> bool:
        return store in self.configured_paths

    def resolves(self, request: list[str], *, paths: list[Path]) -> RezResult:  # noqa: ARG002
        self.resolve_calls.append(request)
        return self.resolve_answer(request)

    def missing_families(self, families: list[str], *, paths: list[Path]) -> list[str]:  # noqa: ARG002
        return [family for family in families if family in self.missing]


@pytest.fixture
def fake_tools() -> FakeRezTools:
    """Rez's tools with nothing scripted yet."""
    return FakeRezTools()


def ok(stdout: str) -> RezResult:
    """A rez command that succeeded and printed *stdout*."""
    return RezResult(returncode=0, stdout=stdout, stderr="")


def failed(stderr: str) -> RezResult:
    """A rez command that failed and printed *stderr*."""
    return RezResult(returncode=1, stdout="", stderr=stderr)
