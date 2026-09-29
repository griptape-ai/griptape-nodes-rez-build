"""``gpu-config``: the rezconfig line that picks this machine's torch build.

A torch library built with ``--torch-backend`` has one variant per torch build
(``.torch_backend-cu128``, ``.torch_backend-cpu``). Rez picks among them with the machine's
``implicit_packages``, the approach the rez documentation describes for GPU-specific packages
(*Ephemeral Packages*). This command reads the NVIDIA driver's CUDA version from ``nvidia-smi``
and the builds in the store, and prints the line for this machine's rezconfig. It never
changes any configuration.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from griptape_nodes_rez_build.errors import BuildError
from griptape_nodes_rez_build.rez_uv import (
    TORCH_BACKEND_EPHEMERAL,
    current_platform_key,
    read_package_file,
    torch_backend_request,
)

if TYPE_CHECKING:
    from pathlib import Path

_CUDA_BACKEND = re.compile(r"^cu(\d+)(\d)$")
_DRIVER_CUDA = re.compile(r"CUDA Version:\s*(\d+)\.(\d+)")
_NVIDIA_SMI_TIMEOUT_SECONDS = 30
CPU_BACKEND = "cpu"


@dataclass(frozen=True)
class CudaVersion:
    major: int
    minor: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    def supports(self, other: CudaVersion) -> bool:
        return (other.major, other.minor) <= (self.major, self.minor)


def gpu_config_snippet(stores: list[Path]) -> str:
    """The rezconfig snippet for this machine, as text to print."""
    platform_key = current_platform_key()
    host = socket.gethostname()
    if sys.platform == "darwin":
        return (
            f"# {host} ({platform_key}): macOS has a single torch build, so there is no torch build to choose.\n"
            "# No rezconfig change is needed.\n"
        )
    built = built_torch_backends(stores, platform_key)
    if not built:
        searched = ", ".join(str(store) for store in stores)
        return (
            f"# {host} ({platform_key}): no torch package in {searched} is built per torch build for this platform.\n"
            "# No rezconfig change is needed until a library is built with --torch-backend.\n"
        )
    driver = nvidia_driver_cuda_version()
    backend = choose_torch_backend(built, driver)
    if driver is None:
        reason = "no NVIDIA GPU found"
    else:
        reason = f"NVIDIA driver supports CUDA {driver}"
    return (
        f"# Torch build for {host} ({platform_key}): {reason}; builds in the store: {', '.join(built)}.\n"
        "# Add to this machine's rezconfig (never to one shared by machines with different GPUs):\n"
        f'implicit_packages = ModifyList(append=["{torch_backend_request(backend)}"])\n'
    )


def choose_torch_backend(built: list[str], driver: CudaVersion | None) -> str:
    """The newest CUDA build the driver supports, or the CPU build without an NVIDIA GPU."""
    built_text = ", ".join(built)
    if driver is None:
        if CPU_BACKEND in built:
            return CPU_BACKEND
        msg = (
            f"Attempted to choose a torch build for this machine. Failed because it has no NVIDIA GPU and the store "
            f"has no CPU build of torch (built: {built_text}). Build the library with --torch-backend cpu."
        )
        raise BuildError(msg)
    supported = [
        backend for backend in built if (cuda := cuda_version_of(backend)) is not None and driver.supports(cuda)
    ]
    if not supported:
        msg = (
            f"Attempted to choose a torch build for this machine. Failed because its NVIDIA driver supports CUDA "
            f"{driver}, older than every CUDA build in the store (built: {built_text}). Update the driver or build "
            "the library for an older --torch-backend."
        )
        raise BuildError(msg)
    return max(supported, key=_cuda_sort_key)


def built_torch_backends(stores: list[Path], platform_key: str) -> list[str]:
    """The torch builds installed for *platform_key* (``linux-x86_64``) in *stores*, oldest CUDA first."""
    rez_platform, _, rez_arch = platform_key.partition("-")
    prefix = f".{TORCH_BACKEND_EPHEMERAL}-"
    found: set[str] = set()
    for store in stores:
        family_dir = store / "torch"
        if not family_dir.is_dir():
            continue
        for package_file in family_dir.glob("*/package.py"):
            for variant in read_package_file(package_file).variants:
                if f"platform-{rez_platform}" not in variant or f"arch-{rez_arch}" not in variant:
                    continue
                found.update(entry.removeprefix(prefix) for entry in variant if entry.startswith(prefix))
    return sorted(found, key=_cuda_sort_key)


def cuda_version_of(backend: str) -> CudaVersion | None:
    """The CUDA version a torch build targets: ``cu128`` is 12.8, ``cu130`` is 13.0."""
    match = _CUDA_BACKEND.match(backend)
    if match is None:
        return None
    return CudaVersion(major=int(match.group(1)), minor=int(match.group(2)))


def nvidia_driver_cuda_version() -> CudaVersion | None:
    """The newest CUDA version this machine's NVIDIA driver supports, from ``nvidia-smi``, or None."""
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603
            [nvidia_smi], capture_output=True, text=True, check=False, timeout=_NVIDIA_SMI_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _DRIVER_CUDA.search(result.stdout)
    if result.returncode != 0 or match is None:
        return None
    return CudaVersion(major=int(match.group(1)), minor=int(match.group(2)))


def _cuda_sort_key(backend: str) -> tuple[int, int]:
    cuda = cuda_version_of(backend)
    if cuda is None:
        return (0, 0)
    return (cuda.major, cuda.minor)
