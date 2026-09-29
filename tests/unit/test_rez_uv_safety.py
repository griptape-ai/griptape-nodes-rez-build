"""Review fixes in rez_uv: escaped package.py values, payload before package.py, and the torch build guard.

Each test here guards against a failure an earlier version of this code had: it escaped only
single quotes, wrote package.py before copying the payload, and let rez pick a torch build
silently.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from griptape_nodes_rez_build import rez_uv
from griptape_nodes_rez_build.rez_uv import (
    TORCH_BACKEND_GUARD,
    ResolvedPackage,
    WheelInfo,
    _install_one,
    _write_package_py,
    read_package_file,
)
from griptape_nodes_rez_build.store import WrittenPackage

if TYPE_CHECKING:
    from collections.abc import Iterator

LINUX = ("linux", "x86_64")
AWKWARD = 'It\'s "quoted" and ends with a backslash \\'


@pytest.fixture(autouse=True)
def linux_platform() -> Iterator[None]:
    """Pin rez platform detection to linux/x86_64 so variant paths are deterministic."""
    with patch.object(rez_uv, "_current_rez_platform", return_value=LINUX):
        yield


def _info(**overrides: Any) -> WheelInfo:
    fields: dict[str, Any] = {
        "pip_name": "Demo-Pkg",
        "version": "1.0.0",
        "description": "Demo package",
        "authors": ["Test Author"],
        "requires_python": None,
        "console_scripts": [],
        "is_pure_python": True,
        "platform_tag": "any",
        "requires_dist": [],
    }
    fields.update(overrides)
    return WheelInfo(**fields)


def _assignments(content: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for node in ast.parse(content).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return values


def _fake_wheel(target: Path, name: str, version: str, *, tag: str = "py3-none-any", summary: str = "x") -> None:
    dist_info = target / f"{name}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nSummary: {summary}\n", encoding="utf-8"
    )
    (dist_info / "WHEEL").write_text(f"Wheel-Version: 1.0\nTag: {tag}\n", encoding="utf-8")
    (target / name).mkdir()
    (target / name / "__init__.py").write_text("", encoding="utf-8")


def _uv_installing(name: str, *, tag: str = "py3-none-any", summary: str = "x") -> Any:
    def run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        target = Path(cmd[cmd.index("--target") + 1])
        version = cmd[cmd.index("--target") + 2].split("==")[1].split("+")[0]
        _fake_wheel(target, name, version, tag=tag, summary=summary)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return run


class TestValuesAreEscaped:
    def test_quotes_and_trailing_backslash_round_trip(self, tmp_path: Path) -> None:
        info = _info(description=AWKWARD, authors=[AWKWARD, "O'Brien \\"], pip_name="Demo\\Pkg'")

        _write_package_py(tmp_path, "demo_pkg", info, [], variant=["python-3.12"])

        values = _assignments((tmp_path / "package.py").read_text(encoding="utf-8"))
        assert values["description"] == AWKWARD
        assert values["authors"] == [AWKWARD, "O'Brien \\"]
        assert values["pip_name"] == "Demo\\Pkg' (1.0.0)"

    def test_installed_wheel_summary_with_backslash_writes_valid_python(self, tmp_path: Path) -> None:
        with patch.object(rez_uv.subprocess, "run", side_effect=_uv_installing("demo", summary=AWKWARD)):
            failure = _install_one(
                ResolvedPackage(pip_name="demo", version="1.0"),
                uv="uv",
                packages_dir=tmp_path,
                extra_index_url=None,
                extra_flags=None,
                python_version="3.12",
            )

        assert failure is None
        values = _assignments((tmp_path / "demo" / "1.0" / "package.py").read_text(encoding="utf-8"))
        assert values["description"] == AWKWARD


class TestPayloadBeforePackageFile:
    def test_variant_files_exist_before_package_py_names_them(self, tmp_path: Path) -> None:
        seen: list[bool] = []
        real_write = rez_uv._write_package_py

        def checking_write(version_dir: Path, *args: Any, **kwargs: Any) -> None:
            seen.append((version_dir / "python-3.12" / "python" / "demo" / "__init__.py").is_file())
            real_write(version_dir, *args, **kwargs)

        with (
            patch.object(rez_uv.subprocess, "run", side_effect=_uv_installing("demo")),
            patch.object(rez_uv, "_write_package_py", side_effect=checking_write),
        ):
            _install_one(
                ResolvedPackage(pip_name="demo", version="1.0"),
                uv="uv",
                packages_dir=tmp_path,
                extra_index_url=None,
                extra_flags=None,
                python_version="3.12",
            )

        assert seen == [True]

    def test_failed_copy_leaves_no_package_and_no_staging(self, tmp_path: Path) -> None:
        with (
            patch.object(rez_uv.subprocess, "run", side_effect=_uv_installing("demo")),
            patch.object(rez_uv, "_copy_payload", side_effect=OSError(28, "No space left on device")),
        ):
            failure = _install_one(
                ResolvedPackage(pip_name="demo", version="1.0"),
                uv="uv",
                packages_dir=tmp_path,
                extra_index_url=None,
                extra_flags=None,
                python_version="3.12",
            )

        assert failure is not None
        assert "No space left on device" in failure
        assert not (tmp_path / "demo" / "1.0" / "package.py").exists()
        assert [path.name for path in (tmp_path / "demo").iterdir()] == []

    def test_rebuilding_a_variant_keeps_the_other_platforms_variant(self, tmp_path: Path) -> None:
        version_dir = tmp_path / "demo" / "1.0"
        osx = ["platform-osx", "arch-arm64", "python-3.12"]
        (version_dir / Path(*osx) / "python").mkdir(parents=True)
        (version_dir / "package.py").write_text(f"name = 'demo'\nvariants = [{osx!r}]\n", encoding="utf-8")

        with patch.object(rez_uv.subprocess, "run", side_effect=_uv_installing("demo", tag="cp312-cp312-linux")):
            _install_one(
                ResolvedPackage(pip_name="demo", version="1.0"),
                uv="uv",
                packages_dir=tmp_path,
                extra_index_url=None,
                extra_flags=None,
                python_version="3.12",
            )

        linux = ["platform-linux", "arch-x86_64", "python-3.12"]
        assert read_package_file(version_dir / "package.py").variants == [osx, linux]
        assert (version_dir / Path(*osx) / "python").is_dir()
        assert (version_dir / Path(*linux) / "python" / "demo" / "__init__.py").is_file()


class TestUnconvertibleRequirement:
    def test_package_with_an_unconvertible_requirement_is_a_failure(self, tmp_path: Path) -> None:
        def run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            target = Path(cmd[cmd.index("--target") + 1])
            _fake_wheel(target, "demo", "1.0")
            metadata = next(target.glob("*.dist-info")) / "METADATA"
            metadata.write_text(metadata.read_text(encoding="utf-8") + "Requires-Dist: other===1.0\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(rez_uv.subprocess, "run", side_effect=run):
            failure = _install_one(
                ResolvedPackage(pip_name="demo", version="1.0"),
                uv="uv",
                packages_dir=tmp_path,
                extra_index_url=None,
                extra_flags=None,
                python_version="3.12",
            )

        assert failure is not None
        assert "===" in failure
        assert not (tmp_path / "demo" / "1.0" / "package.py").exists()


class TestTorchBuildGuard:
    def test_package_with_torch_build_variants_stops_without_a_chosen_build(self, tmp_path: Path) -> None:
        variant = ["platform-linux", "arch-x86_64", "python-3.12", ".torch_backend-cu128"]
        _write_package_py(tmp_path, "torch", _info(pip_name="torch", is_pure_python=False), [], variant=variant)

        content = (tmp_path / "package.py").read_text(encoding="utf-8")
        assert TORCH_BACKEND_GUARD in content
        compile(content, "package.py", "exec")

    def test_package_without_torch_builds_has_no_guard(self, tmp_path: Path) -> None:
        _write_package_py(tmp_path, "six", _info(pip_name="six"), [], variant=["python-3.12"])
        assert "torch_backend" not in (tmp_path / "package.py").read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("ephemerals", "implicits", "requested", "stops"),
        [
            ({"torch_backend": ".torch_backend-cu128"}, {}, {}, True),
            ({"torch_backend": ".torch_backend-cu128"}, {".torch_backend": ".torch_backend-cu128"}, {}, False),
            ({"torch_backend": ".torch_backend-cu128"}, {}, {".torch_backend": ".torch_backend-cu128"}, False),
            ({}, {}, {}, False),
        ],
    )
    def test_guard_logic(
        self, ephemerals: dict[str, str], implicits: dict[str, str], requested: dict[str, str], *, stops: bool
    ) -> None:
        stopped: list[str] = []
        this = type("This", (), {"name": "torch", "version": "2.7.0"})()
        namespace = {
            "ephemerals": ephemerals,
            "implicits": implicits,
            "request": requested,
            "this": this,
            "stop": stopped.append,
        }
        exec(compile(f"def check():\n{TORCH_BACKEND_GUARD}\n", "guard", "exec"), namespace)  # noqa: S102
        namespace["check"]()  # type: ignore[operator]

        assert bool(stopped) is stops
        if stops:
            assert "gpu-config" in stopped[0]
            assert ".torch_backend-cu128" in stopped[0]


class TestWrittenForRollBack:
    def test_new_versions_and_added_variants_are_recorded_not_existing_ones(self, tmp_path: Path) -> None:
        compile_output = "demo==1.0\nsix==1.16.0\nshared==2.0\n"
        # shared-2.0 exists for another platform (compiled): this build adds a variant to it.
        other = ["platform-osx", "arch-arm64", "python-3.12"]
        shared_dir = tmp_path / "shared" / "2.0"
        (shared_dir / Path(*other)).mkdir(parents=True)
        (shared_dir / "package.py").write_text(f"name = 'shared'\nvariants = [{other!r}]\n", encoding="utf-8")
        # six-1.16.0 is already installed for this machine: not touched, not recorded.
        six_dir = tmp_path / "six" / "1.16.0"
        (six_dir / "python-3.12").mkdir(parents=True)
        (six_dir / "package.py").write_text("name = 'six'\nvariants = [['python-3.12']]\n", encoding="utf-8")

        def run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            if "compile" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=compile_output, stderr="")
            target = Path(cmd[cmd.index("--target") + 1])
            name, version = cmd[cmd.index("--target") + 2].split("==")
            _fake_wheel(target, name, version, tag="cp312-cp312-linux" if name == "shared" else "py3-none-any")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(rez_uv.subprocess, "run", side_effect=run):
            report = rez_uv.install(["demo"], packages_dir=tmp_path, python_version="3.12", uv_cmd="uv")

        assert WrittenPackage(version_dir=tmp_path / "demo" / "1.0", variant=None) in report.written
        linux = ["platform-linux", "arch-x86_64", "python-3.12"]
        assert WrittenPackage(version_dir=shared_dir, variant=linux) in report.written
        assert all(item.version_dir != six_dir for item in report.written)
