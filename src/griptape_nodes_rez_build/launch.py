"""``write-launch``: the ``griptape_launch`` package artists start Griptape Nodes with.

``rez env griptape_launch -- gtn`` resolves the engine (with the ``gtn`` app and editor) and the
chosen libraries' ``_edit`` packages together, and the package's ``commands()`` configure the
engine for that environment:

- ``GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE=environment``: the engine loads exactly the libraries
  the ``_edit`` packages publish in ``GTN_LIBRARY_PATHS`` and never builds virtualenvs.
- ``--config-file``: appended to ``REZ_CONFIG_FILE``, after the studio's configuration, so the
  worker's ``rez env`` reads it too. ``--testing-store`` is recorded in the package
  (``testing_stores``) and sets nothing: the engine reads no rez settings, so rez itself has to
  find the store through ``--config-file``.
- ``GTN_CONFIG_WORKER__COMMAND_PREFIX``: each library's worker starts inside
  ``rez env --norc griptape_nodes_engine==<engine> <library request> --``, with the absolute rez
  path of that platform. The engine fills ``{engine_version}`` and ``{library_request}``.

Values are chosen per ``system.platform`` when the package resolves, so one package serves every
platform listed with ``--tools``; on any other platform it stops with a message.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from griptape_nodes_rez_build.console import console
from griptape_nodes_rez_build.engine import ENGINE_FAMILY
from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.manifest import EDIT_SUFFIX
from griptape_nodes_rez_build.package_py import GENERATED_HEADER, list_literal
from griptape_nodes_rez_build.store import (
    PACKAGE_FILE,
    move_into_place,
    newest_version,
    package_exists,
    staging_dir,
    write_text_atomic,
)

if TYPE_CHECKING:
    from griptape_nodes_rez_build.rez_tools import RezTools

LAUNCH_FAMILY = "griptape_launch"
REZ_PLATFORMS = ("linux", "osx", "windows")
DEFAULT_LAUNCH_VERSION = "1.0.0"

ENV_DEPENDENCY_SOURCE = "GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE"
ENV_COMMAND_PREFIX = "GTN_CONFIG_WORKER__COMMAND_PREFIX"
DEPENDENCY_SOURCE_ENVIRONMENT = "environment"
ENGINE_VERSION_PLACEHOLDER = "{engine_version}"
LIBRARY_REQUEST_PLACEHOLDER = "{library_request}"

_PLATFORM_KEYS = {"darwin": "osx", "win32": "windows"}

# Run inside the launch environment: what the engine will see.
_VALIDATE_SCRIPT = """\
import json, os
import griptape_nodes, griptape_nodes_app
names = ("GTN_LIBRARY_PATHS", "GTN_LIBRARY_WORKER_REQUESTS", "GTN_CONFIG_LIBRARY__DEPENDENCY_SOURCE", "GTN_CONFIG_WORKER__COMMAND_PREFIX")
found = {name: os.environ.get(name, "") for name in names}
found["griptape_nodes"] = griptape_nodes.__file__
found["griptape_nodes_app"] = griptape_nodes_app.__file__
print(json.dumps(found))
"""
_WORKER_SCRIPT = "import griptape_nodes, griptape_nodes_app"


@dataclass(frozen=True)
class PlatformSettings:
    """What the launch package sets on one platform."""

    tools: str
    testing_store: str | None
    config_file: str | None

    @property
    def command_prefix(self) -> list[str]:
        rez = self.tools.rstrip("/\\") + "/rez"
        return [
            rez,
            "env",
            "--norc",
            f"{ENGINE_FAMILY}=={ENGINE_VERSION_PLACEHOLDER}",
            LIBRARY_REQUEST_PLACEHOLDER,
            "--",
        ]


@dataclass(frozen=True)
class LaunchPackage:
    version: str
    engine_version: str
    # Rez requests for the chosen libraries' edit packages: ``rez_test_edit==0.1.0``.
    library_requests: list[str]
    per_platform: dict[str, PlatformSettings]
    shared: dict[str, str]


@dataclass(frozen=True)
class LaunchOptions:
    tools: list[str]
    libraries: list[str]
    store: Path
    testing_stores: list[str]
    config_files: list[str]
    set_values: list[str]
    engine_version: str | None
    version: str | None
    print_only: bool
    rebuild: bool
    rez_tools: RezTools | None


def write_launch(options: LaunchOptions) -> None:
    """Work out the launch package, then print it, or write it into the store and check it."""
    tool_paths = parse_platform_values(options.tools, "--tools")
    if not tool_paths:
        msg = "Attempted to write a launch package. Failed because no --tools PLATFORM=PATH was given."
        raise BuildError(msg)
    testing_stores = parse_platform_values(options.testing_stores, "--testing-store")
    config_files = parse_platform_values(options.config_files, "--config-file")
    _check_absolute(tool_paths, "--tools")
    _check_absolute(testing_stores, "--testing-store")
    _check_absolute(config_files, "--config-file")
    shared = parse_set_values(options.set_values)
    if not options.libraries:
        console.print(
            "[yellow]No --library given: the launch package provides the engine only, and no libraries will load.[/yellow]"
        )
    if testing_stores and not config_files:
        console.print(
            "[yellow]--testing-store is recorded in the package only. Give a rezconfig that adds the store with "
            "--config-file so workers find it.[/yellow]"
        )
    stores = [options.store, *_search_paths(options.rez_tools)]

    package = LaunchPackage(
        version=options.version or next_launch_version(options.store),
        engine_version=options.engine_version or _newest(stores, ENGINE_FAMILY, "build-engine"),
        library_requests=[library_request(value, stores) for value in options.libraries],
        per_platform={
            platform: PlatformSettings(
                tools=path, testing_store=testing_stores.get(platform), config_file=config_files.get(platform)
            )
            for platform, path in tool_paths.items()
        },
        shared=shared,
    )
    content = launch_package_content(package)
    if options.print_only:
        # Plain text: the console would wrap long lines and break the generated code.
        sys.stdout.write(content)
        return

    if package_exists(options.store, LAUNCH_FAMILY, package.version) and not options.rebuild:
        msg = (
            f"Attempted to write {LAUNCH_FAMILY}-{package.version}. Failed because it already exists in {options.store}. "
            "Pass --version for a new version, or --rebuild to replace it."
        )
        raise BuildError(msg)
    staged = staging_dir(options.store / LAUNCH_FAMILY, package.version)
    write_text_atomic(staged / PACKAGE_FILE, content)
    move_into_place(staged, options.store / LAUNCH_FAMILY / package.version)
    console.print(f"  Wrote [cyan]{options.store / LAUNCH_FAMILY / package.version / PACKAGE_FILE}[/cyan]")

    if options.rez_tools is None:
        msg = f"Attempted to check {LAUNCH_FAMILY}-{package.version}. Failed because rez's tools were not found."
        raise BuildError(msg)
    validate_launch(options.rez_tools, package, store=options.store)


def launch_package_content(package: LaunchPackage) -> str:
    """The ``package.py`` text. Every value is written with ``repr()``; the prefix with ``literal()``."""
    settings: dict[str, dict[str, str]] = {}
    for platform in sorted(package.per_platform):
        platform_settings = package.per_platform[platform]
        values = {"command_prefix": json.dumps(platform_settings.command_prefix)}
        if platform_settings.config_file is not None:
            values["config_file"] = platform_settings.config_file
        settings[platform] = values
    settings_lines = "".join(f"        {platform!r}: {values!r},\n" for platform, values in settings.items())
    shared_lines = "".join(f"    setenv({name!r}, {value!r})\n" for name, value in sorted(package.shared.items()))
    platforms = ", ".join(sorted(package.per_platform))
    requires = [f"{ENGINE_FAMILY}=={package.engine_version}", *package.library_requests]
    built = datetime.now(UTC).isoformat(timespec="seconds")
    testing_stores = {
        platform: configured.testing_store
        for platform, configured in sorted(package.per_platform.items())
        if configured.testing_store is not None
    }
    testing_block = ""
    if testing_stores:
        testing_block = (
            "\n# Testing launch package: the local stores it was tested with, per platform. Recorded only; the engine\n"
            "# reads no rez settings. Rez finds these stores through the rezconfig given with --config-file.\n"
            f"testing_stores = {testing_stores!r}\n"
        )
    stop_message = f"{LAUNCH_FAMILY} is not set up for this platform (set up for: {platforms}). Platform: "

    return f"""{GENERATED_HEADER}# Start Griptape Nodes with: rez env {LAUNCH_FAMILY} -- gtn

name = {LAUNCH_FAMILY!r}

version = {package.version!r}

description = 'Starts Griptape Nodes with the engine and node libraries from rez.'

build_time = {built!r}

requires = {list_literal(requires)}
{testing_block}

def commands():
    settings = {{
{settings_lines}    }}
    if system.platform not in settings:
        stop({stop_message!r} + system.platform)
    platform_settings = settings[system.platform]
    setenv({ENV_DEPENDENCY_SOURCE!r}, {DEPENDENCY_SOURCE_ENVIRONMENT!r})
    # literal(): the engine fills the {{...}} placeholders, rez must leave them alone.
    setenv({ENV_COMMAND_PREFIX!r}, literal(platform_settings['command_prefix']))
    if 'config_file' in platform_settings:
        env.REZ_CONFIG_FILE.append(literal(platform_settings['config_file']))
{shared_lines}

format_version = 2
"""


def validate_launch(tools: RezTools, package: LaunchPackage, *, store: Path) -> None:
    """Resolve the launch package here, then run each library's worker request the way the engine will."""
    this_platform = _PLATFORM_KEYS.get(sys.platform, "linux")
    if this_platform not in package.per_platform:
        console.print(
            f"  [yellow]Not checked on this machine: the package has no --tools for {this_platform}.[/yellow]"
        )
        return
    paths = tools.paths_with(store)
    request = f"{LAUNCH_FAMILY}=={package.version}"
    console.print(f"[bold]Checking[/bold] rez env {request} ...")
    result = tools.env([request], ["python", "-c", _VALIDATE_SCRIPT], paths=paths)
    if not result.succeeded:
        msg = f"Attempted to resolve {request}. Failed because rez reported:\n{result.failure_summary()}"
        raise BuildError(msg)
    try:
        found = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        msg = f"Attempted to check {request}. Failed because its python printed: {result.stdout!r}"
        raise BuildError(msg) from exc

    problems = _launch_environment_problems(found, package, platform=this_platform, paths=paths)
    if problems:
        msg = f"Attempted to check {request}. Failed because " + "; ".join(problems) + "."
        raise BuildError(msg)
    library_count = len(_split(found["GTN_LIBRARY_PATHS"]))
    console.print(
        f"  [green]rez env {request}: resolves; engine and app from the store; {library_count} library manifest(s)[/green]"
    )

    for entry in _split(found["GTN_LIBRARY_WORKER_REQUESTS"]):
        _validate_worker(tools, entry, package.engine_version, paths=paths)
    console.print(f"  Start Griptape Nodes with: [cyan]rez env {LAUNCH_FAMILY} -- gtn[/cyan]")


def parse_platform_values(values: list[str], option: str) -> dict[str, str]:
    """Parse repeatable ``PLATFORM=PATH`` options (platform is linux, osx, or windows)."""
    result: dict[str, str] = {}
    for raw in values:
        platform, separator, path = raw.partition("=")
        if not separator or platform not in REZ_PLATFORMS or not path.strip():
            msg = (
                f"Attempted to read {option} {raw}. Failed because it is not PLATFORM=PATH with PLATFORM one of "
                f"{', '.join(REZ_PLATFORMS)}."
            )
            raise BuildError(msg)
        result[platform] = path.strip()
    return result


def parse_set_values(values: list[str]) -> dict[str, str]:
    """Parse repeatable ``NAME=VALUE`` options."""
    result: dict[str, str] = {}
    for raw in values:
        name, separator, value = raw.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
            msg = f"Attempted to read --set {raw}. Failed because it is not NAME=VALUE with a valid variable name."
            raise BuildError(msg)
        result[name.strip()] = value
    return result


def library_request(value: str, stores: list[Path]) -> str:
    """``FAMILY[-VERSION]`` as the exact request for the library's edit package: ``rez_test_edit==0.1.0``.

    Without a version, the newest ``<family>_edit`` in the store or on rez's search path.
    """
    family, _, version = value.partition("-")
    family = family.removesuffix(EDIT_SUFFIX)
    edit_family = f"{family}{EDIT_SUFFIX}"
    if not version:
        version = _newest(stores, edit_family, "build-library")
    elif not any(package_exists(store, edit_family, version) for store in stores):
        msg = (
            f"Attempted to add the library {edit_family}-{version} to the launch package. Failed because no store "
            "holds it. Build it with build-library first."
        )
        raise BuildError(msg)
    return f"{edit_family}=={version}"


def next_launch_version(store: Path) -> str:
    """One patch above the newest launch package in the store, or 1.0.0."""
    newest = newest_version([store], LAUNCH_FAMILY)
    if newest is None:
        return DEFAULT_LAUNCH_VERSION
    parts = newest.split(".")
    if not parts[-1].isdigit():
        return f"{newest}.1"
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def _newest(stores: list[Path], family: str, build_command: str) -> str:
    version = newest_version(stores, family)
    if version is None:
        searched = ", ".join(str(store) for store in stores)
        msg = (
            f"Attempted to find a {family} package to require. Failed because none was found (searched: {searched}). "
            f"Build it with {build_command}, or name the version."
        )
        raise BuildError(msg)
    return version


def _search_paths(tools: RezTools | None) -> list[Path]:
    if tools is None:
        return []
    return tools.search_paths()


def _check_absolute(values: dict[str, str], option: str) -> None:
    for platform, path in values.items():
        if _is_absolute_for(path, platform):
            continue
        msg = (
            f"Attempted to use {option} {platform}={path}. Failed because it is not an absolute path on {platform}; "
            "workers start from any folder, so relative paths would break."
        )
        raise BuildError(msg)


def _is_absolute_for(path: str, platform: str) -> bool:
    """Whether *path* is absolute on *platform*, checked by shape so any machine can write any platform's package."""
    if platform == "windows":
        return re.match(r"^[A-Za-z]:[\\/]", path) is not None or path.startswith(("\\\\", "//"))
    return path.startswith("/")


def _in_any(path: str, roots: list[Path]) -> bool:
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in roots)


def _launch_environment_problems(
    found: dict[str, str], package: LaunchPackage, *, platform: str, paths: list[Path]
) -> list[str]:
    """What is wrong with the environment the launch package gave the check's python."""
    problems: list[str] = []
    if found[ENV_DEPENDENCY_SOURCE] != DEPENDENCY_SOURCE_ENVIRONMENT:
        problems.append(f"{ENV_DEPENDENCY_SOURCE} is {found[ENV_DEPENDENCY_SOURCE]!r}")
    expected_prefix = json.dumps(package.per_platform[platform].command_prefix)
    if found[ENV_COMMAND_PREFIX] != expected_prefix:
        problems.append(f"{ENV_COMMAND_PREFIX} is {found[ENV_COMMAND_PREFIX]!r}, expected {expected_prefix!r}")
    problems.extend(
        f"{name} was imported from {found[name]}, outside the package stores"
        for name in ("griptape_nodes", "griptape_nodes_app")
        if not _in_any(found[name], paths)
    )
    library_count = len(_split(found["GTN_LIBRARY_PATHS"]))
    if library_count < len(package.library_requests):
        problems.append(
            f"GTN_LIBRARY_PATHS lists {library_count} manifest(s) for {len(package.library_requests)} libraries"
        )
    return problems


def _validate_worker(tools: RezTools, entry: str, engine_version: str, *, paths: list[Path]) -> None:
    """Resolve one library's worker environment the way the prefix will, and import the engine inside it."""
    name, _, library = entry.partition("=")
    worker_request = [f"{ENGINE_FAMILY}=={engine_version}", library]
    worker = tools.env(worker_request, ["python", "-c", _WORKER_SCRIPT], paths=paths)
    if not worker.succeeded:
        msg = (
            f"Attempted to resolve the worker environment of '{name}' (rez env {' '.join(worker_request)}). "
            f"Failed because rez reported:\n{worker.failure_summary()}"
        )
        raise BuildError(msg)
    console.print(f"  [green]worker for '{name}': rez env {' '.join(worker_request)} resolves[/green]")


def _split(value: str) -> list[str]:
    return [entry for entry in value.split(os.pathsep) if entry]
