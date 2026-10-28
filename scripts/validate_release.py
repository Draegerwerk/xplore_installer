import re
import subprocess
import sys
import tomllib
from pathlib import Path


def validate_version_tag(tag: str, project_version: str) -> None:
    if re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag) is None:
        raise ValueError("Use the tag format vMAJOR.MINOR.PATCH, for example v0.1.0.")
    if tag != f"v{project_version}":
        raise ValueError("The tag must match the version in pyproject.toml.")


def validate_release(tag: str, root: Path) -> None:
    with (root / "pyproject.toml").open("rb") as project_file:
        project_version = tomllib.load(project_file)["project"]["version"]
    validate_version_tag(tag, project_version)
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", "refs/remotes/origin/main"],
        cwd=root,
        check=True,
    )


if __name__ == "__main__":
    validate_release(sys.argv[1], Path.cwd())
