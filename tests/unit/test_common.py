"""Steps shared by the build commands."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from griptape_nodes_rez_build import common
from griptape_nodes_rez_build.common import check_bindings, install_packages, package_requires
from griptape_nodes_rez_build.errors import BuildError, ResolveError
from griptape_nodes_rez_build.rez_uv import RezInstallError

if TYPE_CHECKING:
    from pathlib import Path

    from tests.unit.conftest import FakeRezTools


class TestPackageRequires:
    def test_ranges_sorted(self, tmp_path: Path) -> None:
        requires = package_requires("x", ["tabulate>=0.9", "humanize>=4.9,<5"], store=tmp_path, resolved_versions={})
        assert requires.common == ["humanize-4.9+<5", "tabulate-0.9+"]
        assert requires.platform is None

    def test_platform_markers_give_a_list_per_platform(self, tmp_path: Path) -> None:
        requires = package_requires(
            "x", ["numpy>=1.26", "bitsandbytes>=0.46; sys_platform == 'win32'"], store=tmp_path, resolved_versions={}
        )
        assert requires.platform is not None
        assert requires.platform["windows-AMD64"] == ["bitsandbytes-0.46+", "numpy-1.26+"]
        assert requires.platform["linux-x86_64"] == ["numpy-1.26+"]
        assert requires.platform["*"] == ["numpy-1.26+"]

    def test_python_version_markers_are_evaluated_here(self, tmp_path: Path) -> None:
        requires = package_requires("x", ['tomli>=2; python_version < "3.11"'], store=tmp_path, resolved_versions={})
        assert requires.common == []

    def test_unconvertible_specifier_fails(self, tmp_path: Path) -> None:
        with pytest.raises(BuildError, match="Fix the specifier"):
            package_requires("the library", ["torch<=>2"], store=tmp_path, resolved_versions={})

    def test_invalid_requirement_fails(self, tmp_path: Path) -> None:
        with pytest.raises(BuildError, match="not a valid requirement"):
            package_requires("the library", ["!!!"], store=tmp_path, resolved_versions={})


class TestInstallPackages:
    def test_uv_resolve_failure(self, tmp_path: Path) -> None:
        error = subprocess.CalledProcessError(1, ["uv"], stderr="No solution found when resolving dependencies:\n  x")
        with (
            patch.object(common.rez_uv, "install", side_effect=error),
            pytest.raises(ResolveError, match="No solution"),
        ):
            install_packages("deps", ["x"], store=tmp_path, skip_installed=True)

    def test_package_failures_are_listed(self, tmp_path: Path) -> None:
        error = RezInstallError(["idna==3.7 (download failed)"])
        with patch.object(common.rez_uv, "install", side_effect=error), pytest.raises(BuildError, match=r"idna==3\.7"):
            install_packages("deps", ["x"], store=tmp_path, skip_installed=True)


class TestBindings:
    def test_all_present(self, fake_tools: FakeRezTools, tmp_path: Path) -> None:
        check_bindings(fake_tools, [tmp_path])

    def test_missing_bindings_name_the_fix(self, fake_tools: FakeRezTools, tmp_path: Path) -> None:
        fake_tools.missing = ["os", f"python-{common.python_version()}"]
        with pytest.raises(BuildError) as raised:
            check_bindings(fake_tools, [tmp_path])
        assert "rez-bind os" in str(raised.value)
        assert "rez-bind python" in str(raised.value)
        assert "rez-bind platform" not in str(raised.value)
