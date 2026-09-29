"""``build-engine``: the engine, the ``gtn`` app, the editor, and every pip dependency as rez packages.

The engine package's ``requires`` are the version ranges in the engine's ``pyproject.toml``,
not the versions this build happened to resolve, so the engine resolves together with node
libraries whose own ranges overlap. Reproducibility comes from released package sets (and
``rez env --time``), not from exact pins.

The ``gtn`` app and the prebuilt editor are pinned to the versions this build installed: the
app is released against specific engine versions and declares an exact engine requirement,
which the build drops (the engine comes from the checkout), so rez must not swap in another
app version later.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.panel import Panel

from griptape_nodes_rez_build.common import (
    PackageRequires,
    check_bindings,
    install_packages,
    package_requires,
)
from griptape_nodes_rez_build.console import console
from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.package_py import GENERATED_HEADER, requires_block
from griptape_nodes_rez_build.rez_uv import pip_normalize
from griptape_nodes_rez_build.store import (
    PACKAGE_FILE,
    copy_tree,
    move_into_place,
    package_exists,
    staging_dir,
    write_text_atomic,
)
from griptape_nodes_rez_build.versions import SpecifierConversionError, rez_requirement

if TYPE_CHECKING:
    from griptape_nodes_rez_build.rez_tools import RezTools

ENGINE_FAMILY = "griptape_nodes_engine"
ENGINE_PIP_NAME = "griptape-nodes-engine"
# The Griptape Nodes app: the `gtn` command that starts the engine and its workers.
APP_FAMILY = "griptape_nodes"
APP_PIP_NAME = "griptape-nodes"
# The app's extra that adds the prebuilt editor `gtn` serves locally.
APP_EDITOR_EXTRA = "editor"
EDITOR_FAMILY = "griptape_nodes_editor_dist"
EDITOR_PIP_NAME = "griptape-nodes-editor-dist"

# Run inside the engine's rez environment: where each piece was imported from.
_VALIDATE_SCRIPT = """\
import importlib.metadata, json, shutil
import griptape_nodes, griptape_nodes_app
found = {"griptape_nodes": griptape_nodes.__file__, "griptape_nodes_app": griptape_nodes_app.__file__, "gtn": shutil.which("gtn")}
try:
    found["editor"] = str(importlib.metadata.distribution("griptape-nodes-editor-dist").locate_file(""))
except importlib.metadata.PackageNotFoundError:
    found["editor"] = None
print(json.dumps(found))
"""


@dataclass(frozen=True)
class EngineProject:
    """What the engine checkout's ``pyproject.toml`` says about the engine."""

    repo: Path
    version: str
    dependencies: list[str]
    requires_python: str


@dataclass(frozen=True)
class EngineBuildOptions:
    engine_repo: Path
    store: Path
    tools: RezTools
    editor: bool
    app_version: str | None
    rebuild: bool
    interactive: bool


def build_engine(options: EngineBuildOptions) -> None:
    """Build the engine package and everything it needs, then check it resolves from the store."""
    project = read_engine_project(options.engine_repo)
    paths = options.tools.paths_with(options.store)
    check_bindings(options.tools, paths)
    app_request = _app_request(options.app_version, editor=options.editor)
    exists = package_exists(options.store, ENGINE_FAMILY, project.version)

    console.print()
    console.print("[bold]Summary:[/bold]")
    console.print(f"  Engine:       [cyan]{project.repo}[/cyan]")
    console.print(f"  Version:      [green]{project.version}[/green]")
    console.print(f"  Dependencies: [green]{len(project.dependencies)}[/green] direct, required as ranges")
    console.print(f"  gtn app:      [green]{app_request}[/green]")
    if options.editor:
        console.print("  Editor:       [green]included[/green] (--no-editor leaves it out)")
    else:
        console.print("  Editor:       [yellow]not included[/yellow] (--no-editor)")
    console.print(f"  Build into:   [cyan]{options.store}[/cyan]")
    if exists and options.rebuild:
        console.print(f"  [yellow]{ENGINE_FAMILY}-{project.version} exists and will be replaced (--rebuild).[/yellow]")
    elif exists:
        console.print(
            f"  {ENGINE_FAMILY}-{project.version} exists and is kept; this build adds what this machine needs."
        )
    console.print()
    if options.interactive and not typer.confirm("Proceed with build?", default=True):
        raise typer.Abort

    options.store.mkdir(parents=True, exist_ok=True)
    # The engine joins the resolve as this checkout, so the app's requirement on
    # griptape-nodes-engine is met by the engine being built, never by a PyPI release.
    requests = [*project.dependencies, app_request, f"{ENGINE_PIP_NAME} @ {project.repo.as_uri()}"]
    report = install_packages(
        "the engine's dependencies and the gtn app",
        requests,
        store=options.store,
        skip_installed=not options.rebuild,
        provided=frozenset({ENGINE_PIP_NAME}),
    )
    requires = engine_requires(project, report.resolved_versions, store=options.store, editor=options.editor)

    if exists and not options.rebuild:
        console.print(f"[bold]Kept[/bold] {ENGINE_FAMILY}-{project.version} (pass --rebuild to replace it).")
    else:
        write_engine_package(project, requires, store=options.store)
    validate_engine(options.tools, paths, project.version, store=options.store, editor=options.editor)


def read_engine_project(repo: Path) -> EngineProject:
    """Read the engine's version, dependencies, and Python range from its checkout, as files."""
    pyproject = repo / "pyproject.toml"
    source = repo / "src" / "griptape_nodes"
    if not pyproject.is_file() or not source.is_dir():
        msg = (
            f"Attempted to read the engine checkout at {repo}. Failed because it has no pyproject.toml or "
            "src/griptape_nodes. Pass --engine-repo with the root of a griptape-nodes-engine checkout."
        )
        raise BuildError(msg)
    try:
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"Attempted to read {pyproject}. Failed because: {exc}"
        raise BuildError(msg) from exc
    project = data.get("project", {})
    version = project.get("version")
    if not version:
        msg = f"Attempted to read the engine version from {pyproject}. Failed because [project] has no version."
        raise BuildError(msg)
    return EngineProject(
        repo=repo,
        version=str(version),
        dependencies=[str(dependency) for dependency in project.get("dependencies", [])],
        requires_python=str(project.get("requires-python", "")),
    )


def engine_requires(
    project: EngineProject, resolved_versions: dict[str, str], *, store: Path, editor: bool
) -> PackageRequires:
    """The engine package's requires: pyproject ranges, Python, and the app (and editor) this build installed."""
    requires = package_requires("the engine", project.dependencies, store=store, resolved_versions=resolved_versions)
    extra = [_pinned_to_build(APP_FAMILY, APP_PIP_NAME, resolved_versions, requested=APP_PIP_NAME)]
    if editor:
        extra.append(_pinned_to_build(EDITOR_FAMILY, EDITOR_PIP_NAME, resolved_versions, requested=_editor_request()))
    if project.requires_python:
        try:
            extra.append(rez_requirement("python", project.requires_python))
        except SpecifierConversionError as exc:
            msg = f"Attempted to write the engine's requires-python as a rez range. Failed because {exc}."
            raise BuildError(msg) from exc

    common = sorted(requires.common + extra)
    if requires.platform is None:
        return PackageRequires(common=common, platform=None)
    platform = {key: sorted(value + extra) for key, value in requires.platform.items()}
    return PackageRequires(common=common, platform=platform)


def write_engine_package(project: EngineProject, requires: PackageRequires, *, store: Path) -> Path:
    """Write the engine package: source and ``.dist-info`` staged, ``package.py`` last, renamed into place."""
    version_dir = store / ENGINE_FAMILY / project.version
    console.print(f"[bold]Writing[/bold] {ENGINE_FAMILY}-{project.version}...")
    staged = staging_dir(store / ENGINE_FAMILY, project.version)
    try:
        python_dir = staged / "python"
        copy_tree(project.repo / "src", python_dir)
        _write_dist_info(python_dir, project.version)
        write_text_atomic(staged / PACKAGE_FILE, engine_package_content(project, requires))
        move_into_place(staged, version_dir)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    console.print(
        Panel(
            f"[bold green]Engine package written[/bold green]\n\n"
            f"  Package:  [cyan]{ENGINE_FAMILY}-{project.version}[/cyan]\n"
            f"  Location: [cyan]{version_dir}[/cyan]",
            expand=False,
        )
    )
    return version_dir


def engine_package_content(project: EngineProject, requires: PackageRequires) -> str:
    """The engine's ``package.py``. Every value is written with ``repr()``."""
    built = datetime.now(UTC).isoformat(timespec="seconds")
    content = GENERATED_HEADER
    content += f"\nname = {ENGINE_FAMILY!r}\n"
    content += f"\nversion = {project.version!r}\n"
    content += "\ndescription = 'Griptape Nodes engine'\n"
    content += f"\nbuild_source = {str(project.repo)!r}\n"
    content += f"\nbuild_time = {built!r}\n"
    content += requires_block(requires.common, requires.platform)
    content += """

def commands():
    env.PYTHONPATH.append('{root}/python')


format_version = 2
"""
    return content


def validate_engine(tools: RezTools, paths: list[Path], version: str, *, store: Path, editor: bool) -> None:
    """Resolve the engine from the store and check the engine, app, editor, and ``gtn`` all come from it."""
    request = f"{ENGINE_FAMILY}=={version}"
    console.print(f"[bold]Checking[/bold] rez env {request} ...")
    result = tools.env([request], ["python", "-c", _VALIDATE_SCRIPT], paths=paths)
    if not result.succeeded:
        msg = f"Attempted to resolve {request} from {store}. Failed because rez reported:\n{result.failure_summary()}"
        raise BuildError(msg)
    try:
        found = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        msg = f"Attempted to check {request}. Failed because its python printed: {result.stdout!r}"
        raise BuildError(msg) from exc

    checked = ["griptape_nodes", "griptape_nodes_app", "gtn"]
    if editor:
        checked.append("editor")
    outside = [name for name in checked if not _inside(found.get(name), store)]
    if outside:
        details = ", ".join(f"{name}: {found.get(name) or 'not found'}" for name in outside)
        msg = f"Attempted to check that {request} provides everything from {store}. Failed because {details}."
        raise BuildError(msg)
    console.print(
        f"  [green]rez env {request}: griptape_nodes, griptape_nodes_app, and gtn come from the store[/green]"
    )
    console.print(f"  gtn: {found['gtn']}")


def _app_request(app_version: str | None, *, editor: bool) -> str:
    """The pip request for the gtn app: ``griptape-nodes[editor]``, pinned when a version is given."""
    request = APP_PIP_NAME
    if editor:
        request += f"[{APP_EDITOR_EXTRA}]"
    if app_version:
        request += f"=={app_version}"
    return request


def _editor_request() -> str:
    return f"{APP_PIP_NAME}[{APP_EDITOR_EXTRA}]"


def _pinned_to_build(family: str, pip_name: str, resolved_versions: dict[str, str], *, requested: str) -> str:
    version = resolved_versions.get(pip_normalize(pip_name))
    if version is None:
        msg = (
            f"Attempted to include {pip_name} (from {requested}) in the engine package. Failed because the resolve "
            f"did not include it. If this gtn app version has no '{APP_EDITOR_EXTRA}' extra, pass --no-editor or an "
            "--app-version that has one."
        )
        raise BuildError(msg)
    return f"{family}-{version}"


def _write_dist_info(python_dir: Path, version: str) -> None:
    """A minimal ``.dist-info`` so ``importlib.metadata.version("griptape-nodes-engine")`` works in rez."""
    dist_info = python_dir / f"griptape_nodes_engine-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {ENGINE_PIP_NAME}\nVersion: {version}\n", encoding="utf-8"
    )
    (dist_info / "top_level.txt").write_text("griptape_nodes\n", encoding="utf-8")
    (dist_info / "INSTALLER").write_text("griptape-nodes-rez-build\n", encoding="utf-8")


def _inside(path: str | None, store: Path) -> bool:
    if not path:
        return False
    return Path(path).resolve().is_relative_to(store.resolve())
