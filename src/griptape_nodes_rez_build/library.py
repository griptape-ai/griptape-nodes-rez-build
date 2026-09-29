"""``build-library``: a node library as two rez packages.

``<family>_edit`` holds the library's source and manifest under ``python/<repository or folder
name>/`` (the engine recognises a library dependency by that folder name) and requires what the library needs
to load in the editor (its ``pip_dependencies``) plus the ``_edit`` packages of the libraries it
declares as dependencies. Its ``commands()`` publish the library to the engine:
``GTN_LIBRARY_PATHS`` gets the manifest, ``GTN_LIBRARY_WORKER_REQUESTS`` gets
``<manifest name>=<family>==<version>``, the request a worker for this library runs in.

``<family>`` requires ``<family>_edit==<version>`` plus what the library needs to run nodes
(``pip_dependencies_exec``). A library with no execution dependencies still gets it, so every
library's worker request looks the same.

Requirements are the manifest's version ranges, not the versions this build resolved, so
libraries resolve together with the engine and each other wherever their ranges overlap.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.panel import Panel

from griptape_nodes_rez_build import git
from griptape_nodes_rez_build.common import (
    PackageRequires,
    check_bindings,
    install_packages,
    package_requires,
    python_version,
)
from griptape_nodes_rez_build.console import console
from griptape_nodes_rez_build.errors import BuildError, ResolveError
from griptape_nodes_rez_build.manifest import (
    LibraryFamily,
    LibraryManifest,
    family_from_url,
    find_manifest,
    library_family,
    library_version,
    read_manifest,
)
from griptape_nodes_rez_build.package_py import GENERATED_HEADER, requires_block
from griptape_nodes_rez_build.rez_uv import (
    TORCH_BACKENDS,
    InstallReport,
    flags_for_torch_backend,
    torch_backend_request,
)
from griptape_nodes_rez_build.store import (
    PACKAGE_FILE,
    copy_tree,
    move_into_place,
    newest_version,
    package_exists,
    staging_dir,
    write_text_atomic,
)

if TYPE_CHECKING:
    from griptape_nodes_rez_build.rez_tools import RezTools

ALL_TORCH_BACKENDS = "all"
ENV_UV_TORCH_BACKEND = "UV_TORCH_BACKEND"

# Run inside the library's rez environment: what it publishes to the engine.
_VALIDATE_SCRIPT = """\
import json, os
print(json.dumps({name: os.environ.get(name, "") for name in ("GTN_LIBRARY_PATHS", "GTN_LIBRARY_WORKER_REQUESTS")}))
"""


@dataclass(frozen=True)
class TorchPass:
    """One install pass: the torch build it installs (None for the manifest's own sources) and its uv flags."""

    backend: str | None
    flags: list[str]


@dataclass(frozen=True)
class TorchPlan:
    passes: list[TorchPass]
    # With --torch-backend all: a build that does not publish the library's torch version is skipped.
    skip_unavailable: bool


@dataclass(frozen=True)
class LibraryBuildOptions:
    store: Path
    tools: RezTools
    torch_backends: list[str] | None
    rebuild: bool
    interactive: bool
    # Repository URLs already built in this run, so dependency cycles stop.
    visited: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DependencyInstall:
    report: InstallReport
    # The torch builds the resolved packages are for (``cu128``), empty when none are per build.
    torch_backends: list[str]


@dataclass(frozen=True)
class BuiltLibrary:
    family: LibraryFamily
    version: str
    torch_backends: list[str]


def build_library_from_git(url: str, ref: str | None, options: LibraryBuildOptions) -> BuiltLibrary:
    """Clone *url* (at *ref*) into a temporary folder and build it."""
    console.print(f"  Cloning [cyan]{url}[/cyan]" + (f" at [cyan]{ref}[/cyan]" if ref else ""))
    with tempfile.TemporaryDirectory(prefix="griptape_rez_library_") as temporary:
        clone_dir = Path(temporary) / "repo"
        git.clone(url, clone_dir, ref)
        return build_library_from_dir(clone_dir, replace(options, visited=options.visited | {url}))


def build_library_from_dir(library_dir: Path, options: LibraryBuildOptions) -> BuiltLibrary:
    """Build a library's dependencies, pip packages, and its ``_edit`` and full packages, then check them."""
    manifest_path = find_manifest(library_dir)
    if manifest_path is None:
        msg = (
            f"Attempted to build a node library from {library_dir}. Failed because it contains no "
            "griptape_nodes_library.json (or griptape-nodes-library.json)."
        )
        raise BuildError(msg)
    manifest = read_manifest(manifest_path)
    family = library_family(manifest_path)
    version = library_version(manifest)
    plan = plan_torch_passes(manifest.pip_install_flags, options.torch_backends, platform=sys.platform)
    paths = options.tools.paths_with(options.store)
    check_bindings(options.tools, paths)

    _print_summary(manifest, family, version, plan, options)
    if options.interactive and not typer.confirm("Proceed with build?", default=True):
        raise typer.Abort

    options.store.mkdir(parents=True, exist_ok=True)
    dependency_requires = build_library_dependencies(manifest, options)
    installed = _install_dependencies(manifest, plan, options)
    report = installed.report

    edit_requires = package_requires(
        f"'{manifest.name}' (pip_dependencies)",
        manifest.pip_dependencies,
        store=options.store,
        resolved_versions=report.resolved_versions,
    )
    exec_requires = package_requires(
        f"'{manifest.name}' (pip_dependencies_exec)",
        manifest.pip_dependencies_exec,
        store=options.store,
        resolved_versions=report.resolved_versions,
    )
    write_library_packages(
        manifest,
        family,
        version,
        edit_requires=_with(edit_requires, dependency_requires),
        exec_requires=_with(exec_requires, [f"{family.edit_family}=={version}"]),
        store=options.store,
        rebuild=options.rebuild,
    )
    validate_library(options.tools, paths, manifest, family, version=version, torch_backends=installed.torch_backends)
    return BuiltLibrary(family=family, version=version, torch_backends=installed.torch_backends)


def plan_torch_passes(manifest_flags: list[str], torch_backends: list[str] | None, *, platform: str) -> TorchPlan:
    """Work out one install pass per torch build, refusing flags that depend on the build machine.

    ``--torch-backend auto`` (in the manifest or ``UV_TORCH_BACKEND``) makes uv pick torch for
    the GPU of the machine running the build, which would put that machine's build into a store
    every workstation shares. Torch builds are chosen with ``--torch-backend`` instead, each
    becoming its own variant. macOS has a single torch build, so there nothing is chosen.
    """
    if os.environ.get(ENV_UV_TORCH_BACKEND, "").strip():
        msg = (
            f"Attempted to build with {ENV_UV_TORCH_BACKEND} set in this shell. Failed because it makes uv choose "
            "torch for this machine, and the store is shared by every workstation. Unset it and pass "
            "--torch-backend with the builds to include (for example cu128 cpu, or all)."
        )
        raise BuildError(msg)

    if platform == "darwin":
        if torch_backends:
            console.print("[yellow]--torch-backend is ignored on macOS, which has a single torch build.[/yellow]")
        return TorchPlan(
            passes=[TorchPass(backend=None, flags=_without_torch_backend(manifest_flags))], skip_unavailable=False
        )

    if torch_backends:
        skip_unavailable = ALL_TORCH_BACKENDS in torch_backends
        backends = list(TORCH_BACKENDS) if skip_unavailable else list(dict.fromkeys(torch_backends))
        unknown = [backend for backend in backends if backend not in TORCH_BACKENDS]
        if unknown:
            msg = (
                f"Attempted to build for --torch-backend {', '.join(unknown)}. Failed because it is not a torch build "
                f"uv knows ({', '.join(TORCH_BACKENDS)}, or {ALL_TORCH_BACKENDS})."
            )
            raise BuildError(msg)
        passes = [
            TorchPass(backend=backend, flags=flags_for_torch_backend(manifest_flags, backend)) for backend in backends
        ]
        return TorchPlan(passes=passes, skip_unavailable=skip_unavailable)

    if _asks_for_auto_torch_backend(manifest_flags):
        msg = (
            "Attempted to build a library whose pip_install_flags ask uv to choose torch for this machine "
            "(--torch-backend=auto). Failed because that would put this machine's GPU build into a store every "
            "workstation shares. Pass --torch-backend with the builds to include (for example cu128 cpu, or all); "
            "each becomes its own variant and each workstation selects its own (see gpu-config)."
        )
        raise BuildError(msg)
    return TorchPlan(passes=[TorchPass(backend=None, flags=list(manifest_flags))], skip_unavailable=False)


def build_library_dependencies(manifest: LibraryManifest, options: LibraryBuildOptions) -> list[str]:
    """Build the libraries *manifest* declares, when the store lacks them or with --rebuild, and return their requires.

    A required dependency becomes ``<family>_edit``; an optional one the weak ``~<family>_edit``,
    which constrains it only when something else brings it in.
    """
    requires: list[str] = []
    for dependency in manifest.library_dependencies:
        family = family_from_url(dependency.url)
        edit_family = f"{family}_edit"
        if dependency.required:
            requires.append(edit_family)
        else:
            requires.append(f"~{edit_family}")

        if dependency.url in options.visited:
            continue
        existing = newest_version([options.store], edit_family)
        if existing is not None and not options.rebuild:
            console.print(
                f"  Library dependency {edit_family}-{existing} is already in the store (--rebuild rebuilds it)."
            )
            continue
        console.print(f"[bold]Building library dependency[/bold] {dependency.url} ...")
        dependency_options = replace(options, interactive=False)
        try:
            build_library_from_git(dependency.url, None, dependency_options)
        except BuildError as exc:
            if dependency.required:
                msg = f"Attempted to build the required library dependency {dependency.url}. Failed because: {exc}"
                raise BuildError(msg) from exc
            console.print(f"[yellow]Optional library dependency {dependency.url} was not built: {exc}[/yellow]")
    return requires


def write_library_packages(  # noqa: PLR0913
    manifest: LibraryManifest,
    family: LibraryFamily,
    version: str,
    *,
    edit_requires: PackageRequires,
    exec_requires: PackageRequires,
    store: Path,
    rebuild: bool,
) -> None:
    """Write ``<family>_edit`` (source and manifest), then ``<family>``, each staged with ``package.py`` last."""
    for package_family, content, with_source in (
        (family.edit_family, edit_package_content(manifest, family, version, edit_requires), True),
        (family.family, exec_package_content(manifest, family, version, exec_requires), False),
    ):
        if package_exists(store, package_family, version) and not rebuild:
            console.print(f"  Kept {package_family}-{version} (pass --rebuild to replace it).")
            continue
        staged = staging_dir(store / package_family, version)
        try:
            if with_source:
                copy_tree(manifest.path.parent, staged / "python" / family.folder)
            write_text_atomic(staged / PACKAGE_FILE, content)
            move_into_place(staged, store / package_family / version)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
        console.print(f"  Wrote [cyan]{package_family}-{version}[/cyan]")


def edit_package_content(
    manifest: LibraryManifest, family: LibraryFamily, version: str, requires: PackageRequires
) -> str:
    """``<family>_edit``'s ``package.py``. Every value is written with ``repr()``."""
    library_root = f"{{root}}/python/{family.folder}"
    manifest_path = f"{library_root}/{manifest.path.name}"
    worker_request = f"{manifest.name}={family.family}=={version}"
    content = GENERATED_HEADER
    content += f"\nname = {family.edit_family!r}\n"
    content += f"\nversion = {version!r}\n"
    content += f"\ndescription = {f'Griptape Nodes library (source and edit-time dependencies): {manifest.name}'!r}\n"
    content += f"\nlibrary_name = {manifest.name!r}\n"
    content += requires_block(requires.common, requires.platform)
    content += f"""

def commands():
    env.PYTHONPATH.append({library_root!r})
    env.GTN_LIBRARY_PATHS.append({manifest_path!r})
    env.GTN_LIBRARY_WORKER_REQUESTS.append(literal({worker_request!r}))


format_version = 2
"""
    return content


def exec_package_content(
    manifest: LibraryManifest, family: LibraryFamily, version: str, requires: PackageRequires
) -> str:
    """``<family>``'s ``package.py``: the edit package plus execution dependencies."""
    content = GENERATED_HEADER
    content += f"\nname = {family.family!r}\n"
    content += f"\nversion = {version!r}\n"
    content += f"\ndescription = {f'Griptape Nodes library (execution environment): {manifest.name}'!r}\n"
    content += f"\nlibrary_name = {manifest.name!r}\n"
    content += requires_block(requires.common, requires.platform)
    content += "\nformat_version = 2\n"
    return content


def validate_library(  # noqa: PLR0913
    tools: RezTools,
    paths: list[Path],
    manifest: LibraryManifest,
    family: LibraryFamily,
    *,
    version: str,
    torch_backends: list[str],
) -> None:
    """Resolve both packages from the store and check what they publish to the engine."""
    expected_request = f"{manifest.name}={family.family}=={version}"
    backend_requests = [torch_backend_request(torch_backends[0])] if torch_backends else []
    for request in (f"{family.edit_family}=={version}", f"{family.family}=={version}"):
        console.print(f"[bold]Checking[/bold] rez env {request} ...")
        result = tools.env(
            [request, f"python-{python_version()}", *backend_requests], ["python", "-c", _VALIDATE_SCRIPT], paths=paths
        )
        if not result.succeeded:
            msg = f"Attempted to resolve {request}. Failed because rez reported:\n{result.failure_summary()}"
            raise BuildError(msg)
        try:
            published = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            msg = f"Attempted to check {request}. Failed because its python printed: {result.stdout!r}"
            raise BuildError(msg) from exc

        manifests = [Path(entry) for entry in published["GTN_LIBRARY_PATHS"].split(os.pathsep) if entry]
        own = [path for path in manifests if path.name == manifest.path.name and family.edit_family in path.parts]
        if not own or not own[0].is_file():
            msg = (
                f"Attempted to check that {request} publishes its manifest in GTN_LIBRARY_PATHS. Failed because "
                f"GTN_LIBRARY_PATHS is {published['GTN_LIBRARY_PATHS']!r}."
            )
            raise BuildError(msg)
        requests = published["GTN_LIBRARY_WORKER_REQUESTS"].split(os.pathsep)
        if expected_request not in requests:
            msg = (
                f"Attempted to check that {request} publishes '{expected_request}' in GTN_LIBRARY_WORKER_REQUESTS. "
                f"Failed because it is {published['GTN_LIBRARY_WORKER_REQUESTS']!r}."
            )
            raise BuildError(msg)
        console.print(f"  [green]rez env {request}: resolves; manifest {own[0]}[/green]")

    console.print(
        Panel(
            f"[bold green]Library packages built[/bold green]\n\n"
            f"  Library:        [cyan]{manifest.name}[/cyan]\n"
            f"  Edit package:   [cyan]{family.edit_family}-{version}[/cyan] (add to write-launch --library)\n"
            f"  Worker request: [cyan]{expected_request}[/cyan]",
            expand=False,
        )
    )


def _install_dependencies(
    manifest: LibraryManifest, plan: TorchPlan, options: LibraryBuildOptions
) -> DependencyInstall:
    """Install the library's pip dependencies once per torch pass."""
    specs = manifest.pip_dependencies + manifest.pip_dependencies_exec
    report = InstallReport()
    if not specs:
        console.print("  The library has no pip dependencies.")
        return DependencyInstall(report=report, torch_backends=[])
    for torch_pass in plan.passes:
        label = f"'{manifest.name}' dependencies"
        if torch_pass.backend is not None:
            label += f" (torch build {torch_pass.backend})"
        try:
            pass_report = install_packages(
                label, specs, store=options.store, skip_installed=not options.rebuild, extra_flags=torch_pass.flags
            )
        except ResolveError:
            if not plan.skip_unavailable:
                raise
            console.print(f"  Torch build {torch_pass.backend} skipped: it does not publish what the library pins.")
            continue
        report.merge(pass_report)

    # Whatever uv resolved decides the builds, including a torch index the manifest pins itself.
    backends = sorted({pkg.torch_backend for pkg in report.resolved if pkg.torch_backend is not None})
    if plan.skip_unavailable and not report.resolved:
        msg = (
            f"Attempted to build '{manifest.name}' for every torch build. Failed because none of them publish the "
            "torch version the library needs."
        )
        raise BuildError(msg)
    return DependencyInstall(report=report, torch_backends=backends)


def _print_summary(
    manifest: LibraryManifest, family: LibraryFamily, version: str, plan: TorchPlan, options: LibraryBuildOptions
) -> None:
    console.print()
    console.print("[bold]Summary:[/bold]")
    console.print(f"  Library:       [green]{manifest.name}[/green]")
    console.print(
        f"  Packages:      [cyan]{family.edit_family}-{version}[/cyan], [cyan]{family.family}-{version}[/cyan] (named after {family.source})"
    )
    console.print(f"  Manifest:      [cyan]{manifest.path}[/cyan]")
    console.print(
        f"  Pip deps:      [green]{len(manifest.pip_dependencies)} edit + {len(manifest.pip_dependencies_exec)} exec[/green], required as ranges"
    )
    backends = [torch_pass.backend for torch_pass in plan.passes if torch_pass.backend is not None]
    if backends:
        console.print(f"  Torch builds:  [green]{', '.join(backends)}[/green]")
    if manifest.library_dependencies:
        console.print(
            f"  Library deps:  [green]{len(manifest.library_dependencies)}[/green] (built first when missing)"
        )
    console.print(f"  Build into:    [cyan]{options.store}[/cyan]")
    console.print()


def _with(requires: PackageRequires, extra: list[str]) -> PackageRequires:
    if not extra:
        return requires
    platform = None
    if requires.platform is not None:
        platform = {key: sorted(value + extra) for key, value in requires.platform.items()}
    return PackageRequires(common=sorted(requires.common + extra), platform=platform)


def _asks_for_auto_torch_backend(flags: list[str]) -> bool:
    for index, flag in enumerate(flags):
        name, separator, value = flag.partition("=")
        if name != "--torch-backend":
            continue
        if not separator:
            value = flags[index + 1] if index + 1 < len(flags) else ""
        if value.strip() == "auto":
            return True
    return False


def _without_torch_backend(flags: list[str]) -> list[str]:
    result: list[str] = []
    skip_next = False
    for flag in flags:
        if skip_next:
            skip_next = False
            continue
        name, separator, _ = flag.partition("=")
        if name == "--torch-backend":
            skip_next = not separator
            continue
        result.append(flag)
    return result
