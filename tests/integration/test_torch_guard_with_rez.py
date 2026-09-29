"""The torch build guard and per-machine implicit packages, resolved by real rez.

Needs rez: set GRIPTAPE_NODES_REZ_TOOLS to the folder holding rez-env (or put rez-env on PATH).
Everything happens in a temporary store with a temporary rezconfig; no other configuration is
read or changed. The torch "builds" are empty packages written by the same code that writes
real ones, with this machine's platform so rez can resolve them here.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.rez_tools import ENV_REZ_TOOLS, RezTools
from griptape_nodes_rez_build.rez_uv import WheelInfo, _write_package_py, build_variant

PYTHON_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"
CHECK = "import os; print(os.environ['REZ_USED_RESOLVE'])"


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
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    for binding in ("platform", "arch", "os", "python"):
        result = tools.run(["rez-bind", binding], timeout=120)
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
        variant = build_variant(is_pure=False, python_version=PYTHON_VERSION, backend=backend, extras=[])
        (store / "torch" / "2.7.0" / Path(*variant) / "python").mkdir(parents=True)
        _write_package_py(store / "torch" / "2.7.0", "torch", info, [], variant=variant)

    library_dir = store / "torch_nodes" / "1.0.0"
    library_dir.mkdir(parents=True)
    (library_dir / "package.py").write_text(
        f"name = 'torch_nodes'\nversion = '1.0.0'\nrequires = ['torch-2.7+', 'python-{PYTHON_VERSION}']\n",
        encoding="utf-8",
    )
    return store


def _with_implicit(store: Path, backend: str) -> None:
    config = Path(os.environ["REZ_CONFIG_FILE"])
    config.write_text(
        f'packages_path = [{str(store)!r}]\nimplicit_packages = ModifyList(append=[".torch_backend-{backend}"])\n',
        encoding="utf-8",
    )


class TestTorchGuard:
    @pytest.mark.usefixtures("rez_store")
    def test_without_implicit_the_environment_stops_with_the_fix(self) -> None:
        result = _tools().env(["torch_nodes"], ["python", "-c", CHECK])

        assert not result.succeeded
        output = result.stderr + result.stdout
        assert "has not chosen one" in output
        assert "gpu-config" in output

    @pytest.mark.parametrize("backend", ["cpu", "cu128"])
    def test_implicit_selects_its_build(self, rez_store: Path, backend: str) -> None:
        _with_implicit(rez_store, backend)

        result = _tools().env(["torch_nodes"], ["python", "-c", CHECK])

        assert result.succeeded, result.failure_summary()
        assert "torch-2.7.0" in result.stdout
        root = _tools().env(["torch_nodes"], ["python", "-c", "import os; print(os.environ['REZ_TORCH_ROOT'])"])
        assert root.stdout.strip().endswith(f".torch_backend-{backend}")

    @pytest.mark.usefixtures("rez_store")
    def test_explicit_request_also_satisfies_the_guard(self) -> None:
        result = _tools().env(["torch_nodes", ".torch_backend-cu128"], ["python", "-c", CHECK])
        assert result.succeeded, result.failure_summary()

    def test_implicit_for_a_build_that_is_not_in_the_store_fails_to_resolve(self, rez_store: Path) -> None:
        _with_implicit(rez_store, "cu118")
        result = _tools().env(["torch_nodes"], ["python", "-c", CHECK])
        assert not result.succeeded

    @pytest.mark.usefixtures("rez_store")
    def test_packages_without_torch_builds_are_unaffected(self) -> None:
        result = _tools().env([f"python-{PYTHON_VERSION}"], ["python", "-c", "print('ok')"])
        assert result.succeeded, result.failure_summary()


def test_rez_tools_folder_has_rez() -> None:
    """The tools folder the other tests use really holds rez-env."""
    tools = _tools()
    assert shutil.which("rez-env", path=str(tools.folder)) is not None
