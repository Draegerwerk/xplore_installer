from importlib.metadata import distribution

import xplore_installer


def test_package_identity() -> None:
    package = distribution("xplore-installer")
    assert xplore_installer.library_name == "xplore_installer"
    assert xplore_installer.__version__ == package.version
    assert package.metadata["Requires-Python"] == ">=3.11"
    assert package.metadata["License-Expression"] == "Apache-2.0"


def test_distribution_contains_package_and_license() -> None:
    files = distribution("xplore-installer").files
    assert files is not None
    paths = [str(path).replace("\\", "/") for path in files]
    assert "xplore_installer/__init__.py" in paths
    assert any(path.endswith("/licenses/LICENSE") for path in paths)
