"""Run a generated package.py's commands() the way rez would, with recording stand-ins for rez's objects."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


class Stopped(Exception):  # noqa: N818 - named after rez's stop()
    """commands() called stop()."""


@dataclass(frozen=True)
class Literal:
    """What rez's literal() returns: a string rez does not expand."""

    value: str


class _Variable:
    def __init__(self, environ: dict[str, str], name: str) -> None:
        self._environ = environ
        self._name = name

    def __str__(self) -> str:
        return self._environ.get(self._name, "")

    def append(self, value: object) -> None:
        text = _text(value, self._environ)
        current = self._environ.get(self._name)
        self._environ[self._name] = text if not current else current + os.pathsep + text

    def prepend(self, value: object) -> None:
        text = _text(value, self._environ)
        current = self._environ.get(self._name)
        self._environ[self._name] = text if not current else text + os.pathsep + current


class _Env:
    _environ: dict[str, str]

    def __init__(self, environ: dict[str, str]) -> None:
        object.__setattr__(self, "_environ", environ)

    def __getattr__(self, name: str) -> _Variable:
        return _Variable(self._environ, name)

    def __setattr__(self, name: str, value: object) -> None:
        self._environ[name] = _text(value, self._environ)


@dataclass
class RexRun:
    environ: dict[str, str] = field(default_factory=dict)
    literals: list[str] = field(default_factory=list)


def run_commands(
    content: str, *, platform: str = "osx", root: str = "/store/pkg/1.0", environ: dict[str, str] | None = None
) -> RexRun:
    """Execute *content* and call its commands(); ``{root}`` expands to *root* as rez expands it."""
    run = RexRun(environ=dict(environ or {}))
    run.environ["__root__"] = root

    def literal(value: str) -> Literal:
        run.literals.append(value)
        return Literal(value)

    def setenv(name: str, value: object) -> None:
        run.environ[name] = _text(value, run.environ)

    def stop(message: str) -> None:
        raise Stopped(message)

    namespace: dict[str, Any] = {
        "late": lambda: lambda function: function,
        "env": _Env(run.environ),
        "setenv": setenv,
        "literal": literal,
        "stop": stop,
        "system": SimpleNamespace(platform=platform, arch="x86_64"),
    }
    exec(compile(content, "package.py", "exec"), namespace)  # noqa: S102
    namespace["commands"]()
    run.environ.pop("__root__")
    return run


def _text(value: object, environ: dict[str, str]) -> str:
    if isinstance(value, Literal):
        return value.value
    return str(value).replace("{root}", environ["__root__"])
