from tempfile import TemporaryDirectory

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp is not None:
        return

    temporary_root = config.rootpath / ".pytest_tmp"
    temporary_root.mkdir(exist_ok=True)
    temporary_directory = TemporaryDirectory(prefix="run-", dir=temporary_root)
    config.option.basetemp = temporary_directory.name
    config.add_cleanup(temporary_directory.cleanup)
