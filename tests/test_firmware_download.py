import sys

import pytest

from tests.test_elf32_reader import make_elf
from tests.test_installation_contract import flasher as flasher
from tests.test_installation_contract import installation as installation
from tests.test_installation_contract import installation_steps as installation_steps
from xplore_installer.elf32_reader import ELF32FileReader


def test_download_receives_decoded_elf_data(
    installation, installation_steps, flasher, tmp_path, monkeypatch
) -> None:
    path = tmp_path / "firmware.elf"
    path.write_bytes(make_elf([(0x08004000, b"abcd"), (0x08004008, b"ef")]))
    step = installation_steps[0]
    step.software_package = str(path)
    monkeypatch.setattr(
        sys.modules[type(installation).__module__], "ELF32FileReader", ELF32FileReader
    )

    installation._run_install_step(step)

    flasher.download.assert_called_once_with(
        0x08004000, b"abcd\xff\xff\xff\xffef", step.timeout_duration
    )


@pytest.mark.parametrize("file_exists", [True, False], ids=["invalid-elf", "missing-file"])
def test_firmware_read_error_does_not_start_device_update(
    installation, installation_steps, flasher, tmp_path, monkeypatch, file_exists
) -> None:
    path = tmp_path / "firmware.elf"
    if file_exists:
        path.write_bytes(b"invalid firmware")
    installation_steps[0].software_package = str(path)
    monkeypatch.setattr(
        sys.modules[type(installation).__module__], "ELF32FileReader", ELF32FileReader
    )

    with pytest.raises(ValueError if file_exists else FileNotFoundError):
        installation._run_install_step(installation_steps[0])

    flasher.connector.setup.assert_not_called()
    flasher.connector.put.assert_not_called()
    flasher.factory.assert_not_called()
    assert installation._install_process.is_cancelable is True
