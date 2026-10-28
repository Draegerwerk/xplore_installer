import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_release import validate_release, validate_version_tag


def run_git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    run_git(tmp_path, "init", "--initial-branch=main")
    run_git(tmp_path, "config", "user.name", "Test")
    run_git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
    run_git(tmp_path, "add", "pyproject.toml")
    run_git(tmp_path, "commit", "-m", "Initial package")
    run_git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def test_accepts_main_commit(repository: Path) -> None:
    validate_release("v0.1.0", repository)


def test_accepts_earlier_commit_on_main(repository: Path) -> None:
    release_commit = run_git(repository, "rev-parse", "HEAD")
    run_git(repository, "commit", "--allow-empty", "-m", "Later change")
    run_git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    run_git(repository, "checkout", "--detach", release_commit)
    validate_release("v0.1.0", repository)


@pytest.mark.parametrize("tag", ["0.1.0", "v01.1.0", "v0.1", "v0.1.0rc1", "v0.1.0-extra"])
def test_rejects_invalid_tag(tag: str) -> None:
    with pytest.raises(ValueError, match="tag format"):
        validate_version_tag(tag, "0.1.0")


def test_rejects_version_mismatch() -> None:
    with pytest.raises(ValueError, match="must match"):
        validate_version_tag("v0.2.0", "0.1.0")


def test_rejects_release_version_mismatch(repository: Path) -> None:
    with pytest.raises(ValueError, match="must match"):
        validate_release("v0.2.0", repository)


def test_rejects_commit_outside_main(repository: Path) -> None:
    run_git(repository, "checkout", "-b", "feature")
    run_git(repository, "commit", "--allow-empty", "-m", "Unmerged change")
    with pytest.raises(subprocess.CalledProcessError):
        validate_release("v0.1.0", repository)


def test_rejects_missing_main_reference(repository: Path) -> None:
    run_git(repository, "update-ref", "-d", "refs/remotes/origin/main")
    with pytest.raises(subprocess.CalledProcessError):
        validate_release("v0.1.0", repository)


def test_command_line(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "validate_release.py"
    monkeypatch.chdir(repository)
    monkeypatch.setattr(sys, "argv", [str(script), "v0.1.0"])
    runpy.run_path(str(script), run_name="__main__")
