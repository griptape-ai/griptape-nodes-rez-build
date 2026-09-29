"""The ``griptape-nodes-rez`` command."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

import typer
from rich.logging import RichHandler
from rich.panel import Panel

from griptape_nodes_rez_build.common import admin_path, can_prompt
from griptape_nodes_rez_build.console import console
from griptape_nodes_rez_build.engine import EngineBuildOptions, build_engine
from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.gpu import gpu_config_snippet
from griptape_nodes_rez_build.launch import LaunchOptions, write_launch
from griptape_nodes_rez_build.library import LibraryBuildOptions, build_library_from_dir, build_library_from_git
from griptape_nodes_rez_build.rez_tools import ENV_REZ_TOOLS, RezTools

if TYPE_CHECKING:
    from collections.abc import Callable

app = typer.Typer(
    help="Build Griptape Nodes, its node libraries, and a launch package as rez packages.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

RezToolsOption = Annotated[
    str | None,
    typer.Option(
        "--rez-tools",
        help=f"Folder holding rez's tools (rez-env). Defaults to {ENV_REZ_TOOLS}, then rez-env on PATH.",
    ),
]
StoreOption = Annotated[
    str,
    typer.Option("--local-packages-path", help="Package store to build into. Never your released packages."),
]
RebuildOption = Annotated[
    bool,
    typer.Option("--rebuild", help="Reinstall this machine's variants of dependencies and replace existing packages."),
]
YesOption = Annotated[
    bool,
    typer.Option("--yes", "-y", help="Never prompt. A missing requirement still stops the build."),
]


@app.callback()
def main_options(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show each package as it is resolved and written.")
    ] = False,
) -> None:
    """Options for every command."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s", handlers=[RichHandler(console=console, show_path=False)])


@app.command("build-engine")
def build_engine_command(
    local_packages_path: StoreOption,
    engine_version: Annotated[
        str | None,
        typer.Option(
            "--engine-version",
            help="Published engine version to build. Default: the newest release the gtn app accepts.",
        ),
    ] = None,
    engine_repo: Annotated[
        str | None,
        typer.Option(
            "--engine-repo",
            help="Build this griptape-nodes-engine checkout instead of a published release, to package your own changes.",
        ),
    ] = None,
    no_editor: Annotated[
        bool, typer.Option("--no-editor", help="Leave out the prebuilt editor (griptape-nodes-editor-dist).")
    ] = False,
    app_version: Annotated[
        str | None, typer.Option("--app-version", help="Version of the gtn app (griptape-nodes) to include.")
    ] = None,
    rebuild: RebuildOption = False,
    yes: YesOption = False,
    rez_tools: RezToolsOption = None,
) -> None:
    """Build the engine, the gtn app, the editor, and every pip dependency as rez packages."""
    if engine_repo is not None and engine_version is not None:
        console.print(
            "[red]Attempted to build the engine. Failed because --engine-version picks a published release and "
            "--engine-repo builds a checkout, whose version comes from its pyproject.toml. Pass one of them.[/red]"
        )
        raise typer.Exit(1)
    console.print(Panel("[bold cyan]Griptape Nodes engine package[/bold cyan]", expand=False))

    def build() -> None:
        checkout = None
        if engine_repo is not None:
            checkout = admin_path(engine_repo)
        build_engine(
            EngineBuildOptions(
                engine_repo=checkout,
                engine_version=engine_version,
                store=admin_path(local_packages_path),
                tools=RezTools.locate(rez_tools),
                editor=not no_editor,
                app_version=app_version,
                rebuild=rebuild,
                interactive=can_prompt(yes=yes),
            )
        )

    _run(build)


@app.command("build-library")
def build_library_command(
    local_packages_path: StoreOption,
    git_url: Annotated[str | None, typer.Option("--git-url", help="Git URL of the library to clone and build.")] = None,
    branch: Annotated[str | None, typer.Option("--branch", help="Branch, tag, or commit to build.")] = None,
    local_path: Annotated[str | None, typer.Option("--local-path", help="Folder holding the library.")] = None,
    torch_backend: Annotated[
        list[str] | None,
        typer.Option(
            "--torch-backend",
            help=(
                "Torch build to include, e.g. cu128 or cpu (repeatable), or 'all' for every build that publishes "
                "the library's torch version. Each becomes its own variant. Linux and Windows only."
            ),
        ),
    ] = None,
    rebuild: RebuildOption = False,
    yes: YesOption = False,
    rez_tools: RezToolsOption = None,
) -> None:
    """Build a node library as <family>_edit and <family> packages, with the libraries it declares."""
    if (git_url is None) == (local_path is None):
        console.print(
            "[red]Attempted to build a library. Failed because exactly one of --git-url or --local-path is needed.[/red]"
        )
        raise typer.Exit(1)
    if branch is not None and git_url is None:
        console.print("[red]Attempted to use --branch. Failed because it only applies with --git-url.[/red]")
        raise typer.Exit(1)
    console.print(Panel("[bold cyan]Griptape Nodes library packages[/bold cyan]", expand=False))

    def build() -> None:
        options = LibraryBuildOptions(
            store=admin_path(local_packages_path),
            tools=RezTools.locate(rez_tools),
            torch_backends=torch_backend or None,
            rebuild=rebuild,
            interactive=can_prompt(yes=yes),
        )
        if git_url is not None:
            build_library_from_git(git_url, branch, options)
            return
        if local_path is not None:
            build_library_from_dir(admin_path(local_path), options)

    _run(build)


@app.command("write-launch")
def write_launch_command(
    local_packages_path: StoreOption,
    tools: Annotated[
        list[str],
        typer.Option(
            "--tools",
            help="Absolute folder of rez's tools on a platform, as PLATFORM=PATH (repeatable; linux, osx, windows).",
        ),
    ],
    library: Annotated[
        list[str] | None,
        typer.Option("--library", help="Library to include, as FAMILY or FAMILY-VERSION (repeatable)."),
    ] = None,
    testing_store: Annotated[
        list[str] | None,
        typer.Option("--testing-store", help="Testing only: a store workers search first, as PLATFORM=PATH."),
    ] = None,
    config_file: Annotated[
        list[str] | None,
        typer.Option("--config-file", help="A rezconfig layered after the studio's, as PLATFORM=PATH."),
    ] = None,
    set_value: Annotated[
        list[str] | None,
        typer.Option("--set", help="Extra environment variable for every platform, as NAME=VALUE (repeatable)."),
    ] = None,
    engine_version: Annotated[
        str | None, typer.Option("--engine-version", help="Engine version. Defaults to the newest found.")
    ] = None,
    version: Annotated[
        str | None, typer.Option("--version", help="Launch package version. Defaults to one above the newest.")
    ] = None,
    allow_sandbox: Annotated[
        bool,
        typer.Option(
            "--allow-sandbox",
            help="Let the engine load the sandbox library too; its imports must come from this environment.",
        ),
    ] = False,
    print_only: Annotated[bool, typer.Option("--print", help="Show the package instead of writing it.")] = False,
    rebuild: Annotated[bool, typer.Option("--rebuild", help="Replace a launch package of the same version.")] = False,
    yes: YesOption = False,  # noqa: ARG001 - accepted for scripted use; this command never prompts
    rez_tools: RezToolsOption = None,
) -> None:
    """Write the griptape_launch package: engine, chosen libraries, and the engine's environment settings."""

    def write() -> None:
        located: RezTools | None = None
        if not print_only:
            located = RezTools.locate(rez_tools)
        write_launch(
            LaunchOptions(
                tools=tools,
                libraries=library or [],
                store=admin_path(local_packages_path),
                testing_stores=testing_store or [],
                config_files=config_file or [],
                set_values=set_value or [],
                engine_version=engine_version,
                version=version,
                print_only=print_only,
                rebuild=rebuild,
                rez_tools=located,
                allow_sandbox=allow_sandbox,
            )
        )

    _run(write)


@app.command("gpu-config")
def gpu_config_command(
    local_packages_path: Annotated[
        list[str] | None,
        typer.Option("--local-packages-path", help="Store to read torch builds from. Defaults to rez's search path."),
    ] = None,
    rez_tools: RezToolsOption = None,
) -> None:
    """Print the rezconfig line that picks this machine's torch build."""

    def show() -> None:
        stores = [admin_path(path) for path in local_packages_path or []]
        if not stores:
            stores = RezTools.locate(rez_tools).search_paths()
        typer.echo(gpu_config_snippet(stores), nl=False)

    _run(show)


def main() -> None:
    """Entry point of the griptape-nodes-rez command."""
    app()


def _run(action: Callable[[], None]) -> None:
    """Run a command body; a BuildError is printed for the administrator and exits with status 1."""
    try:
        action()
    except BuildError as exc:
        console.print(str(exc), markup=False, style="red")
        raise typer.Exit(1) from exc
