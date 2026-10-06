"""Git operations library builds use."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from griptape_nodes_rez_build import git
from griptape_nodes_rez_build.errors import BuildError

if TYPE_CHECKING:
    from pathlib import Path


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    """A repository with one commit, tagged v1.0.0."""
    repo = tmp_path / "source"
    _git("init", "-q", str(repo))
    (repo / "file.txt").write_text("hello", encoding="utf-8")
    _git("-C", str(repo), "add", "file.txt")
    _git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "first")
    _git("-C", str(repo), "tag", "v1.0.0")
    return repo


class TestClone:
    def test_clones_and_checks_out_a_ref(self, tmp_path: Path, source_repo: Path) -> None:
        destination = tmp_path / "clone"
        git.clone(str(source_repo), destination, "v1.0.0")
        assert (destination / "file.txt").read_text(encoding="utf-8") == "hello"

    def test_a_url_starting_with_a_dash_is_not_an_option(self, tmp_path: Path) -> None:
        marker = tmp_path / "ran"
        with pytest.raises(BuildError, match="Attempted to clone"):
            git.clone(f"--upload-pack=touch {marker}", tmp_path / "clone", None)
        assert not marker.exists()

    def test_a_ref_starting_with_a_dash_is_not_an_option(self, tmp_path: Path, source_repo: Path) -> None:
        with pytest.raises(BuildError, match="Attempted to check out --orphan=evil"):
            git.clone(str(source_repo), tmp_path / "clone", "--orphan=evil")
