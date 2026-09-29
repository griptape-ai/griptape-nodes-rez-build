"""Shared test doubles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from griptape_nodes_rez_build.rez_tools import RezResult, RezTools


@dataclass
class EnvCall:
    request: list[str]
    command: list[str]
    paths: list[Path] | None


@dataclass
class FakeRezTools(RezTools):
    """Rez's tools, with every command answered from lists instead of run."""

    folder: Path = field(default_factory=lambda: Path("/rez/bin"))
    env_results: list[RezResult] = field(default_factory=list)
    configured_paths: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    env_calls: list[EnvCall] = field(default_factory=list)

    def search_paths(self) -> list[Path]:
        return list(self.configured_paths)

    def env(self, request: list[str], command: list[str], *, paths: list[Path] | None = None) -> RezResult:
        self.env_calls.append(EnvCall(request=request, command=command, paths=paths))
        return self.env_results.pop(0)

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
