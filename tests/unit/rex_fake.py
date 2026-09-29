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
    """Rez's append/prepend: the first change in a context replaces the value inherited from the shell."""

    def __init__(self, environ: dict[str, str], touched: set[str], name: str) -> None:
        self._environ = environ
        self._touched = touched
        self._name = name

    def __str__(self) -> str:
        return self._environ.get(self._name, "")

    def append(self, value: object) -> None:
        text = _text(value, self._environ)
        current = self._current()
        self._environ[self._name] = text if not current else current + os.pathsep + text

    def prepend(self, value: object) -> None:
        text = _text(value, self._environ)
        current = self._current()
        self._environ[self._name] = text if not current else text + os.pathsep + current

    def _current(self) -> str | None:
        if self._name not in self._touched:
            self._touched.add(self._name)
            return None
        return self._environ.get(self._name)


class _Env:
    _environ: dict[str, str]
    _touched: set[str]

    def __init__(self, environ: dict[str, str], touched: set[str]) -> None:
        object.__setattr__(self, "_environ", environ)
        object.__setattr__(self, "_touched", touched)

    def __getattr__(self, name: str) -> _Variable:
        return _Variable(self._environ, self._touched, name)

    def __setattr__(self, name: str, value: object) -> None:
        self._touched.add(name)
        self._environ[name] = _text(value, self._environ)


@dataclass
class RexRun:
    environ: dict[str, str] = field(default_factory=dict)
    literals: list[str] = field(default_factory=list)


def run_commands(
    content: str, *, platform: str = "osx", root: str = "/store/pkg/1.0", environ: dict[str, str] | None = None
) -> RexRun:
    """Execute *content* and call its commands(); ``{root}`` expands to *root* as rez expands it.

    *environ* is the shell ``rez env`` was started in. As in rez, a package's first append or
    prepend to one of its variables replaces the inherited value.
    """
    run = RexRun(environ=dict(environ or {}))
    run.environ["__root__"] = root

    def literal(value: str) -> Literal:
        run.literals.append(value)
        return Literal(value)

    touched: set[str] = set()

    def setenv(name: str, value: object) -> None:
        touched.add(name)
        run.environ[name] = _text(value, run.environ)

    def stop(message: str) -> None:
        raise Stopped(message)

    parent = dict(run.environ)

    def defined(name: str) -> bool:
        return name in parent

    def getenv(name: str) -> str:
        return parent[name]

    namespace: dict[str, Any] = {
        "defined": defined,
        "getenv": getenv,
        "late": lambda: lambda function: function,
        "env": _Env(run.environ, touched),
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
