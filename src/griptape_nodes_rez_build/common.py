"""Steps every build command shares: checks before building, installing pip packages, requires."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from griptape_nodes_rez_build import rez_uv
from griptape_nodes_rez_build.console import console
from griptape_nodes_rez_build.errors import BuildError, ResolveError
from griptape_nodes_rez_build.package_py import FALLBACK_PLATFORM_KEY
from griptape_nodes_rez_build.versions import SpecifierConversionError

if TYPE_CHECKING:
    from griptape_nodes_rez_build.rez_tools import RezTools


@dataclass(frozen=True)
class PackageRequires:
    """A package's requires: one list for every platform, or a list per ``<platform>-<arch>``."""

    common: list[str]
    platform: dict[str, list[str]] | None


def python_version() -> str:
    """The Python version builds resolve for: the interpreter running the build tool."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def can_prompt(*, yes: bool) -> bool:
    """Prompts appear only in an interactive terminal, and never with --yes."""
    if yes:
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def admin_path(value: str) -> Path:
    """A path the administrator typed, made absolute, in the form they will see echoed back."""
    return Path(value).expanduser().absolute()


def check_bindings(tools: RezTools, paths: list[Path]) -> None:
    """Stop unless rez can find the platform, arch, os, and python packages builds are checked with.

    ``rez bind`` creates them. Without them no package resolves, so the build could not be
    checked and would count as done without proof; ``--yes`` does not change that.
    """
    version = python_version()
    hints = {
        "platform": "rez-bind platform",
        "arch": "rez-bind arch",
        "os": "rez-bind os",
        f"python-{version}": f"rez-bind python (with Python {version} first on PATH)",
    }
    missing = tools.missing_families(list(hints), paths=paths)
    if not missing:
        return
    searched = ", ".join(str(path) for path in paths)
    steps = "\n".join(f"  {hints[family]}" for family in missing)
    msg = (
        f"Attempted to check that rez can resolve packages on this machine. Failed because rez cannot find "
        f"{', '.join(missing)} (searched: {searched}). Create them with:\n{steps}"
    )
    raise BuildError(msg)


def install_packages(  # noqa: PLR0913
    label: str,
    requests: list[str],
    *,
    store: Path,
    skip_installed: bool,
    extra_flags: list[str] | None = None,
    provided: frozenset[str] = frozenset(),
) -> rez_uv.InstallReport:
    """Install pip *requests* and everything they need as rez packages, or stop with the reason."""
    console.print(f"[bold]Installing {label} as rez packages...[/bold]")
    try:
        report = rez_uv.install(
            requests,
            packages_dir=store,
            extra_flags=extra_flags,
            python_version=python_version(),
            skip_installed=skip_installed,
            provided=provided,
        )
    except subprocess.CalledProcessError as exc:
        msg = f"Attempted to resolve {label} with uv. Failed because uv reported:\n{_uv_error_tail(exc)}"
        raise ResolveError(msg) from exc
    except rez_uv.RezInstallError as exc:
        failures = "\n".join(f"  - {failure}" for failure in exc.failures)
        msg = (
            f"Attempted to install {label} as rez packages. Failed because {len(exc.failures)} package(s) could not "
            f"be installed:\n{failures}\nNothing that requires them was written."
        )
        raise BuildError(msg) from exc
    except OSError as exc:
        msg = f"Attempted to install {label} as rez packages into {store}. Failed because: {exc}"
        raise BuildError(msg) from exc
    print_install_report(report)
    return report


def print_install_report(report: rez_uv.InstallReport) -> None:
    """Say what an install added to the store, and which other platforms it already served."""
    this_machine = rez_uv.current_platform_key().replace("-", "/", 1)
    if report.other_platforms:
        console.print(f"  This store already has packages built for {', '.join(sorted(report.other_platforms))}.")
    parts = [f"{len(report.new)} new"]
    if report.added_variant:
        parts.append(f"{len(report.added_variant)} gained a {this_machine} variant")
    if report.rebuilt:
        parts.append(f"{len(report.rebuilt)} rebuilt")
    parts.append(f"{len(report.already_installed)} already installed for {this_machine}")
    console.print(f"  Packages: {'; '.join(parts)}.")


def package_requires(
    label: str, specs: list[str], *, store: Path, resolved_versions: dict[str, str]
) -> PackageRequires:
    """Rez requires for pip *specs*, as version ranges, for a package with no variants.

    Rez has no extras, so what a requested extra adds (``pydantic-ai-slim[mcp]`` adds
    ``fastmcp-slim``) is required directly, read from the installed metadata in *store*.
    Dependencies marked for some platforms only are listed per platform.
    """
    parsed: list[rez_uv.ParsedRequirement] = []
    for spec in specs:
        requirement = rez_uv.parse_requirement(spec)
        if requirement is None:
            msg = (
                f"Attempted to read the requirement '{spec}' of {label}. Failed because it is not a valid requirement."
            )
            raise BuildError(msg)
        parsed.append(requirement)
    expanded = rez_uv.expand_extras(parsed, rez_uv.store_requires_dist_reader(store, resolved_versions))
    try:
        return _split_by_platform(expanded)
    except SpecifierConversionError as exc:
        msg = (
            f"Attempted to write the requirements of {label} as rez version ranges. Failed because {exc}. "
            "Fix the specifier; a guessed range could resolve to versions it rules out."
        )
        raise BuildError(msg) from exc


def _split_by_platform(requirements: list[rez_uv.ParsedRequirement]) -> PackageRequires:
    common: list[str] = []
    platform_specific: list[rez_uv.ParsedRequirement] = []
    for requirement in requirements:
        if requirement.depends_on_platform:
            platform_specific.append(requirement)
        elif requirement.applies():
            common.append(rez_uv.rez_range(requirement))
    if not platform_specific:
        return PackageRequires(common=sorted(common), platform=None)

    per_platform: dict[str, list[str]] = {FALLBACK_PLATFORM_KEY: sorted(common)}
    for key, environment in rez_uv.platform_environments().items():
        extra = [rez_uv.rez_range(r) for r in platform_specific if r.applies(environment)]
        per_platform[key] = sorted(common + extra)
    return PackageRequires(common=sorted(common), platform=per_platform)


def _uv_error_tail(exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").strip()
    if not stderr:
        return f"uv exited with status {exc.returncode}"
    return "\n".join(stderr.splitlines()[-10:])
