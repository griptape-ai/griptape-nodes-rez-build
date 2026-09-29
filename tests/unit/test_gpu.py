"""gpu-config: choosing this machine's torch build."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from griptape_nodes_rez_build import gpu
from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.gpu import (
    CudaVersion,
    built_torch_backends,
    choose_torch_backend,
    cuda_version_of,
    gpu_config_snippet,
    nvidia_driver_cuda_version,
)

if TYPE_CHECKING:
    from pathlib import Path


def _torch_store(tmp_path: Path) -> Path:
    package_file = tmp_path / "torch" / "2.7.0" / "package.py"
    package_file.parent.mkdir(parents=True)
    variants = [
        ["platform-linux", "arch-x86_64", "python-3.12", ".torch_backend-cu128"],
        ["platform-linux", "arch-x86_64", "python-3.12", ".torch_backend-cu118"],
        ["platform-linux", "arch-x86_64", "python-3.12", ".torch_backend-cpu"],
        ["platform-windows", "arch-AMD64", "python-3.12", ".torch_backend-cu130"],
        ["platform-osx", "arch-arm64", "python-3.12"],
    ]
    package_file.write_text(f"name = 'torch'\nvariants = {variants!r}\n", encoding="utf-8")
    return tmp_path


class TestChoose:
    def test_newest_cuda_build_the_driver_supports(self) -> None:
        assert choose_torch_backend(["cpu", "cu118", "cu128", "cu130"], CudaVersion(12, 9)) == "cu128"

    def test_cpu_without_nvidia(self) -> None:
        assert choose_torch_backend(["cpu", "cu128"], None) == "cpu"

    def test_no_cpu_build_without_nvidia(self) -> None:
        with pytest.raises(BuildError, match="--torch-backend cpu"):
            choose_torch_backend(["cu128"], None)

    def test_driver_older_than_every_build(self) -> None:
        with pytest.raises(BuildError, match=r"CUDA 11\.4"):
            choose_torch_backend(["cu118", "cu128"], CudaVersion(11, 4))

    @pytest.mark.parametrize(
        ("backend", "expected"), [("cu118", CudaVersion(11, 8)), ("cu130", CudaVersion(13, 0)), ("cpu", None)]
    )
    def test_cuda_version_of(self, backend: str, expected: CudaVersion | None) -> None:
        assert cuda_version_of(backend) == expected


class TestBuiltBackends:
    def test_only_this_platforms_builds(self, tmp_path: Path) -> None:
        assert built_torch_backends([_torch_store(tmp_path)], "linux-x86_64") == ["cpu", "cu118", "cu128"]
        assert built_torch_backends([tmp_path], "windows-AMD64") == ["cu130"]
        assert built_torch_backends([tmp_path], "osx-arm64") == []


class TestSnippet:
    def test_linux_with_a_driver(self, tmp_path: Path) -> None:
        with (
            patch.object(gpu.sys, "platform", "linux"),
            patch.object(gpu, "current_platform_key", return_value="linux-x86_64"),
            patch.object(gpu, "nvidia_driver_cuda_version", return_value=CudaVersion(12, 8)),
        ):
            snippet = gpu_config_snippet([_torch_store(tmp_path)])
        assert 'implicit_packages = ModifyList(append=[".torch_backend-cu128"])' in snippet
        assert "CUDA 12.8" in snippet

    def test_macos_needs_nothing(self, tmp_path: Path) -> None:
        with patch.object(gpu.sys, "platform", "darwin"):
            snippet = gpu_config_snippet([tmp_path])
        assert "implicit_packages" not in snippet
        assert "single torch build" in snippet

    def test_store_without_torch_builds(self, tmp_path: Path) -> None:
        with (
            patch.object(gpu.sys, "platform", "linux"),
            patch.object(gpu, "current_platform_key", return_value="linux-x86_64"),
        ):
            snippet = gpu_config_snippet([tmp_path])
        assert "implicit_packages" not in snippet
        assert "--torch-backend" in snippet


class TestNvidiaSmi:
    def test_reads_the_driver_cuda_version(self) -> None:
        output = "| NVIDIA-SMI 570.86.10   Driver Version: 570.86.10   CUDA Version: 12.8     |\n"
        with (
            patch.object(gpu.shutil, "which", return_value="/usr/bin/nvidia-smi"),
            patch.object(gpu.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output)),
        ):
            assert nvidia_driver_cuda_version() == CudaVersion(12, 8)

    def test_no_nvidia_smi(self) -> None:
        with patch.object(gpu.shutil, "which", return_value=None):
            assert nvidia_driver_cuda_version() is None
