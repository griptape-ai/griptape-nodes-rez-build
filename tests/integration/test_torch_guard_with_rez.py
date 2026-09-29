"""The torch build guard and per-machine implicit packages, resolved by real rez.

Needs rez: set GRIPTAPE_NODES_REZ_TOOLS to the folder holding rez-env (or put rez-env on PATH).
Everything happens in a temporary store with a temporary rezconfig; no other configuration is
read or changed. The torch "builds" are empty packages written by the same code that writes
real ones, with this machine's platform so rez can resolve them here.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.rez_tools import ENV_REZ_TOOLS, RezTools
from griptape_nodes_rez_build.rez_uv import WheelInfo, _write_package_py, build_variant

CHECK = "import os; print(os.environ['REZ_USED_RESOLVE'])"


@functools.cache
def _rez_python() -> Path:
    """The interpreter rez's own tools run on, from the rez-env script's first line.

    rez-bind python links the interpreter it is given with --exe but copies the standard library
    of the interpreter rez runs on (rez 3.4.0), so binding any other Python mixes two standard
    libraries ("SRE module mismatch"). The tests bind this one and build for its version.
    """
    first_line = (_tools().folder / "rez-env").read_text(encoding="utf-8").splitlines()[0]
    if not first_line.startswith("#!"):
        pytest.skip("cannot tell which Python rez runs on: rez-env has no #! line")
    return Path(first_line[2:].strip())


@functools.cache
def _python_version() -> str:
    """Major and minor version of the Python rez runs on, which the bound python package has."""
    result = subprocess.run(  # noqa: S603
        [str(_rez_python()), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _tools() -> RezTools:
    try:
        return RezTools.locate(None)
    except BuildError:
        pytest.skip(f"rez is not available (set {ENV_REZ_TOOLS})")


@pytest.fixture
def rez_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A store with platform/arch/os/python bound, torch built for cpu and cu128, and a library needing torch."""
    if sys.platform == "win32":
        pytest.skip("POSIX only: rez-bind python symlinks the interpreter")
    tools = _tools()
    store = tmp_path / "store"
    store.mkdir()
    config = tmp_path / "rezconfig.py"
    config.write_text(f"packages_path = [{str(store)!r}]\nlocal_packages_path = {str(store)!r}\n", encoding="utf-8")
    for name in list(os.environ):
        if name.startswith("REZ_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("REZ_CONFIG_FILE", str(config))
    # Without --exe, rez-bind python binds the first `python` on PATH, which on a CI runner can be
    # the system Python. The one rez runs on is the only one it binds consistently (_rez_python).
    bindings = [["platform"], ["arch"], ["os"], ["python", "--exe", str(_rez_python())]]
    for binding in bindings:
        result = tools.run(["rez-bind", *binding], timeout=120)
        assert result.succeeded, result.failure_summary()

    info = WheelInfo(
        pip_name="torch",
        version="2.7.0",
        description="Tensors",
        authors=[],
        requires_python=None,
        console_scripts=[],
        is_pure_python=False,
        platform_tag="test",
        requires_dist=[],
    )
    for backend in ("cpu", "cu128"):
        variant = build_variant(is_pure=False, python_version=_python_version(), backend=backend, extras=[])
        (store / "torch" / "2.7.0" / Path(*variant) / "python").mkdir(parents=True)
        _write_package_py(store / "torch" / "2.7.0", "torch", info, [], variant=variant)

    # A pure package whose requires differ by platform, written the way real ones are: its
    # late-bound requires() is what made rez 3.4.0 hand commands() an empty `ephemerals`.
    pure = WheelInfo(
        pip_name="click",
        version="8.1.0",
        description="",
        authors=[],
        requires_python=None,
        console_scripts=[],
        is_pure_python=True,
        platform_tag="any",
        requires_dist=[],
    )
    (store / "click" / "8.1.0" / f"python-{_python_version()}" / "python").mkdir(parents=True)
    _write_package_py(
        store / "click" / "8.1.0",
        "click",
        pure,
        [],
        variant=[f"python-{_python_version()}"],
        platform_requires={"*": [], "windows-AMD64": ["colorama"]},
    )

    library_dir = store / "torch_nodes" / "1.0.0"
    library_dir.mkdir(parents=True)
    (library_dir / "package.py").write_text(
        f"name = 'torch_nodes'\nversion = '1.0.0'\nrequires = ['torch-2.7+', 'python-{_python_version()}']\n",
        encoding="utf-8",
    )
    return store


def _with_implicit(store: Path, backend: str) -> None:
    config = Path(os.environ["REZ_CONFIG_FILE"])
    config.write_text(
        f'packages_path = [{str(store)!r}]\nimplicit_packages = ModifyList(append=[".torch_backend-{backend}"])\n',
        encoding="utf-8",
    )


LATE = "click"  # the package with a late-bound requires()


class TestTorchGuard:
    @pytest.mark.usefixtures("rez_store")
    @pytest.mark.parametrize("request_", [["torch_nodes"], ["torch_nodes", LATE]])
    def test_without_implicit_the_environment_stops_with_the_fix(self, request_: list[str]) -> None:
        result = _tools().run_python(request_, CHECK)

        assert not result.succeeded
        output = result.stderr + result.stdout
        assert "has not chosen one" in output
        assert "gpu-config" in output

    @pytest.mark.parametrize("backend", ["cpu", "cu128"])
    def test_implicit_selects_its_build(self, rez_store: Path, backend: str) -> None:
        _with_implicit(rez_store, backend)

        result = _tools().run_python(["torch_nodes", LATE], CHECK)

        assert result.succeeded, result.failure_summary()
        assert "torch-2.7.0" in result.stdout
        assert "click-8.1.0" in result.stdout
        root = _tools().run_python(["torch_nodes", LATE], "import os; print(os.environ['REZ_TORCH_ROOT'])")
        assert root.stdout.strip().endswith(f".torch_backend-{backend}")

    @pytest.mark.usefixtures("rez_store")
    @pytest.mark.parametrize("backend", ["cpu", "cu128"])
    def test_explicit_request_also_satisfies_the_guard_on_its_variant(self, backend: str) -> None:
        request_ = ["torch_nodes", LATE, f".torch_backend-{backend}"]
        result = _tools().run_python(request_, CHECK)
        assert result.succeeded, result.failure_summary()
        root = _tools().run_python(request_, "import os; print(os.environ['REZ_TORCH_ROOT'])")
        assert root.stdout.strip().endswith(f".torch_backend-{backend}")

    def test_implicit_for_a_build_that_is_not_in_the_store_fails_to_resolve(self, rez_store: Path) -> None:
        _with_implicit(rez_store, "cu118")
        result = _tools().run_python(["torch_nodes"], CHECK)
        assert not result.succeeded

    def test_rez_hands_commands_empty_ephemerals_next_to_a_late_requires(self, rez_store: Path) -> None:
        """Why the guard reads this.variant_requires: a guard on `ephemerals` never fires here."""
        package_dir = rez_store / "ephemeral_guard" / "1.0"
        for backend in ("cu118", "cu128"):
            (package_dir / f".torch_backend-{backend}").mkdir(parents=True)
        (package_dir / "package.py").write_text(
            "name = 'ephemeral_guard'\nversion = '1.0'\n"
            "variants = [['.torch_backend-cu118'], ['.torch_backend-cu128']]\n"
            "def commands():\n"
            "    if 'torch_backend' in ephemerals and '.torch_backend' not in implicits:\n"
            "        stop('guard fired')\n",
            encoding="utf-8",
        )
        alone = _tools().run_python(["ephemeral_guard", f"python-{_python_version()}"], "print('ran')")
        with_late = _tools().run_python(["ephemeral_guard", LATE], "print('ran')")

        assert not alone.succeeded
        assert with_late.succeeded  # rez 3.4.0: the ephemerals-based guard is silently skipped

    @pytest.mark.usefixtures("rez_store")
    def test_packages_without_torch_builds_are_unaffected(self) -> None:
        result = _tools().run_python([f"python-{_python_version()}"], "print('ok')")
        assert result.succeeded, result.failure_summary()


def test_rez_tools_folder_has_rez() -> None:
    """The tools folder the other tests use really holds rez-env."""
    tools = _tools()
    assert shutil.which("rez-env", path=str(tools.folder)) is not None
