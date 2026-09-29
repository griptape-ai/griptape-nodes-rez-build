"""``build-engine``: the engine, the ``gtn`` app, the editor, and every pip dependency as rez packages.

The engine is the published ``griptape-nodes-engine`` release by default (the newest one the app
accepts, or ``--engine-version``), or a local checkout with ``--engine-repo`` for studios that
change the engine themselves.

The engine package's ``requires`` are the version ranges the engine declares (the release's
metadata, or the checkout's ``pyproject.toml``), not the versions this build happened to resolve, so the engine resolves together with node
libraries whose own ranges overlap. Reproducibility comes from released package sets (and
``rez env --time``), not from exact pins.

The ``gtn`` app and the prebuilt editor are pinned to the versions this build installed: the
app is released against a range of engine versions, and the build resolves it against the one
engine it packages, so rez must not swap in another app version later.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.panel import Panel
from uv import find_uv_bin

from griptape_nodes_rez_build.common import (
    PackageRequires,
    check_bindings,
    install_packages,
    package_requires,
    python_version,
    rolled_back,
    uv_error_tail,
)
from griptape_nodes_rez_build.console import console
from griptape_nodes_rez_build.errors import BuildError, ResolveError
from griptape_nodes_rez_build.package_py import GENERATED_HEADER, requires_block
from griptape_nodes_rez_build.rez_uv import pip_normalize, read_wheel_info, resolve_full
from griptape_nodes_rez_build.store import (
    PACKAGE_FILE,
    WrittenPackage,
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
# The engine's import package: the only folder the package payload holds.
ENGINE_IMPORT_NAME = "griptape_nodes"
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

# A Requires-Dist entry that only an extra of the engine brings in (``; extra == 'profiling'``).
_EXTRA_MARKER = re.compile(r"\bextra\s*==")


@dataclass(frozen=True)
class EngineProject:
    """The engine being packaged: where its source is, and what it declares."""

    # Folder holding the griptape_nodes package: the checkout's src/, or the unpacked release.
    source: Path
    # Where the engine came from, for the summary and the package's build_source.
    origin: str
    # How uv's resolve names this engine, so the app's engine requirement is met by it.
    pip_request: str
    version: str
    dependencies: list[str]
    requires_python: str


@dataclass(frozen=True)
class EngineBuildOptions:
    # A local checkout to build instead of the published engine.
    engine_repo: Path | None
    # The published engine version to build; the newest the app accepts when None.
    engine_version: str | None
    store: Path
    tools: RezTools
    editor: bool
    app_version: str | None
    rebuild: bool
    interactive: bool


def build_engine(options: EngineBuildOptions) -> None:
    """Build the engine package and everything it needs, then check it resolves from the store."""
    paths = options.tools.paths_with(options.store)
    app_request = _app_request(options.app_version, editor=options.editor)
    if options.engine_repo is not None:
        project = read_engine_project(options.engine_repo)
        check_bindings(options.tools, paths)
        _build_engine_project(project, app_request, paths, options)
        return
    check_bindings(options.tools, paths)
    with tempfile.TemporaryDirectory(prefix="griptape-nodes-engine-") as work_dir:
        project = fetch_published_engine(options.engine_version, app_request, Path(work_dir))
        _build_engine_project(project, app_request, paths, options)


def _build_engine_project(
    project: EngineProject, app_request: str, paths: list[Path], options: EngineBuildOptions
) -> None:
    exists = package_exists(options.store, ENGINE_FAMILY, project.version)

    console.print()
    console.print("[bold]Summary:[/bold]")
    console.print(f"  Engine:       [cyan]{project.origin}[/cyan]")
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
    # The engine joins the resolve as the one being built, so the app's requirement on
    # griptape-nodes-engine is met by it and never by another release.
    requests = [*project.dependencies, app_request, project.pip_request]
    report = install_packages(
        "the engine's dependencies and the gtn app",
        requests,
        store=options.store,
        skip_installed=not options.rebuild,
        provided=frozenset({ENGINE_PIP_NAME}),
    )
    requires = engine_requires(project, report.resolved_versions, store=options.store, editor=options.editor)

    written = list(report.written)
    if exists and not options.rebuild:
        console.print(f"[bold]Kept[/bold] {ENGINE_FAMILY}-{project.version} (pass --rebuild to replace it).")
    else:
        version_dir = write_engine_package(project, requires, store=options.store)
        written.append(WrittenPackage(version_dir=version_dir, variant=None))
    try:
        validate_engine(options.tools, paths, project.version, store=options.store, editor=options.editor)
    except BuildError as exc:
        raise rolled_back(exc, written) from exc


def fetch_published_engine(engine_version: str | None, app_request: str, work_dir: Path) -> EngineProject:
    """Unpack the published engine into *work_dir* and read what its metadata declares.

    Without *engine_version* the engine is the newest release the app accepts, chosen by
    resolving the two together.
    """
    engine_request = ENGINE_PIP_NAME
    if engine_version:
        engine_request += f"=={engine_version}"
    console.print(f"[bold]Choosing[/bold] the published engine for {app_request}...")
    try:
        resolved = resolve_full([engine_request, app_request], python_version=python_version())
    except subprocess.CalledProcessError as exc:
        msg = (
            f"Attempted to find a published {engine_request} that works with {app_request}. "
            f"Failed because uv reported:\n{uv_error_tail(exc)}"
        )
        raise ResolveError(msg) from exc
    version = next((p.version for p in resolved if pip_normalize(p.pip_name) == ENGINE_PIP_NAME), None)
    if version is None:
        msg = f"Attempted to find a published {engine_request}. Failed because uv's resolve did not include it."
        raise ResolveError(msg)

    pinned = f"{ENGINE_PIP_NAME}=={version}"
    download = subprocess.run(  # noqa: S603
        [find_uv_bin(), "pip", "install", "--no-deps", "--target", str(work_dir), pinned],
        capture_output=True,
        text=True,
        check=False,
    )
    if download.returncode != 0:
        details = uv_error_tail(subprocess.CalledProcessError(download.returncode, "uv", stderr=download.stderr))
        msg = f"Attempted to download {pinned}. Failed because uv reported:\n{details}"
        raise ResolveError(msg)
    if not (work_dir / ENGINE_IMPORT_NAME).is_dir():
        msg = f"Attempted to unpack {pinned}. Failed because the release has no {ENGINE_IMPORT_NAME} package."
        raise BuildError(msg)
    try:
        info = read_wheel_info(work_dir, ENGINE_PIP_NAME, version)
    except (OSError, RuntimeError) as exc:
        msg = f"Attempted to read the metadata of {pinned}. Failed because: {exc}"
        raise BuildError(msg) from exc
    return EngineProject(
        source=work_dir,
        origin=f"{pinned} (published release)",
        pip_request=pinned,
        version=version,
        dependencies=[requirement for requirement in info.requires_dist if not _EXTRA_MARKER.search(requirement)],
        requires_python=info.requires_python or "",
    )


def read_engine_project(repo: Path) -> EngineProject:
    """Read the engine's version, dependencies, and Python range from its checkout, as files."""
    pyproject = repo / "pyproject.toml"
    source = repo / "src" / ENGINE_IMPORT_NAME
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
        source=source.parent,
        origin=str(repo),
        pip_request=f"{ENGINE_PIP_NAME} @ {repo.as_uri()}",
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
        copy_tree(project.source / ENGINE_IMPORT_NAME, python_dir / ENGINE_IMPORT_NAME)
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
    content += f"\nbuild_source = {project.origin!r}\n"
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
    result = tools.run_python([request], _VALIDATE_SCRIPT, paths=paths)
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
