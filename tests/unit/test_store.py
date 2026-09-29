"""Rolling back what one build wrote into a store."""

from __future__ import annotations

from pathlib import Path

from griptape_nodes_rez_build.rez_uv import read_package_file
from griptape_nodes_rez_build.store import WrittenPackage, roll_back

OSX = ["platform-osx", "arch-arm64", "python-3.12"]
WINDOWS = ["platform-windows", "arch-AMD64", "python-3.12"]


def _package(store: Path, variants: list[list[str]]) -> Path:
    version_dir = store / "torch" / "2.7.0"
    for variant in variants:
        (version_dir / Path(*variant) / "python").mkdir(parents=True)
    (version_dir / "package.py").write_text(
        f"name = 'torch'\n\nversion = '2.7.0'\n\nvariants = {variants!r}\n\nformat_version = 2\n", encoding="utf-8"
    )
    return version_dir


class TestRollBack:
    def test_a_version_this_build_created_is_removed(self, tmp_path: Path) -> None:
        version_dir = _package(tmp_path, [OSX])
        removed = roll_back([WrittenPackage(version_dir=version_dir, variant=None)])
        assert removed == ["torch-2.7.0"]
        assert not version_dir.exists()
        # An empty family folder goes too; one with other versions stays.
        assert not version_dir.parent.exists()

    def test_a_variant_added_to_another_builds_version_keeps_that_build(self, tmp_path: Path) -> None:
        version_dir = _package(tmp_path, [WINDOWS, OSX])

        removed = roll_back([WrittenPackage(version_dir=version_dir, variant=OSX)])

        assert removed == [f"torch-2.7.0 variant {OSX}"]
        assert read_package_file(version_dir / "package.py").variants == [WINDOWS]
        assert (version_dir / Path(*WINDOWS) / "python").is_dir()
        assert not (version_dir / Path(*OSX)).exists()
        assert "format_version = 2" in (version_dir / "package.py").read_text(encoding="utf-8")

    def test_the_last_variant_takes_the_version_with_it(self, tmp_path: Path) -> None:
        version_dir = _package(tmp_path, [OSX])
        roll_back([WrittenPackage(version_dir=version_dir, variant=OSX)])
        assert not version_dir.exists()

    def test_already_gone_is_skipped(self, tmp_path: Path) -> None:
        assert roll_back([WrittenPackage(version_dir=tmp_path / "nope" / "1.0", variant=None)]) == []
