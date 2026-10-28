import sys
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call

import pytest

from tests.install_interface_mocks import (
    BaseInstallation,
    InstallationProcessData,
    InstallStep,
    ProcessState,
)


@pytest.fixture
def installation(monkeypatch: pytest.MonkeyPatch):
    dependencies = {
        "install_interface.data.installation_process_data": Mock(
            InstallationProcessData=InstallationProcessData, ProcessState=ProcessState
        ),
        "install_interface.interface.base_installation": Mock(
            BaseInstallation=BaseInstallation, InstallStep=InstallStep
        ),
        "install_interface.tools.progress_updater": Mock(ProgressStepUpdater=Mock()),
        "aoneconnector": Mock(),
        "aoneconnector.aone_connector": Mock(),
    }
    for name, dependency in dependencies.items():
        monkeypatch.setitem(sys.modules, name, dependency)

    from xplore_installer.xplore8300_installation import Xplore8300Installation

    return Xplore8300Installation(
        InstallationProcessData(),
        Mock(),
        Mock(connection_information="COM3", software_version="1.2.3"),
    )


def test_setup_does_not_raise(installation) -> None:
    installation._setup()


def test_setup_initializes_firmware_state(installation) -> None:
    installation._firmware_started = True
    installation._setup()

    assert installation._firmware_started is False


def test_teardown_does_not_raise(installation) -> None:
    installation._teardown()


@pytest.fixture
def installation_steps(installation, monkeypatch: pytest.MonkeyPatch):
    from install_interface.interface.base_installation import InstallStep

    steps = [
        InstallStep("Firmware", "2776", "firmware.bin", "", 1, 1),
        InstallStep("Files", "2776", "files.bin", "", 1, 1),
    ]
    monkeypatch.setattr(installation, "_determine_installation_steps", Mock(return_value=steps))
    return steps


@pytest.fixture
def progress_updater(monkeypatch: pytest.MonkeyPatch, installation):
    updater = Mock()
    monkeypatch.setattr(sys.modules[type(installation).__module__], "ProgressStepUpdater", updater)
    return updater


def test_install_steps_success(
    installation, installation_steps, progress_updater, monkeypatch: pytest.MonkeyPatch
) -> None:
    from install_interface.data.installation_process_data import ProcessState

    run_step = Mock()
    monkeypatch.setattr(installation, "_run_install_step", run_step)

    installation.run()

    assert run_step.call_args_list == [call(step) for step in installation_steps]
    assert installation._install_process.state == ProcessState.success
    assert installation._install_process.progress == 100
    progress_updater.return_value.start.assert_called()
    progress_updater.return_value.finish.assert_called()


@pytest.mark.parametrize("cancel_before", [True, False], ids=["before-download", "after-download"])
def test_install_steps_cancellation(
    installation,
    installation_steps,
    progress_updater,
    monkeypatch: pytest.MonkeyPatch,
    cancel_before: bool,
) -> None:
    from install_interface.data.installation_process_data import ProcessState

    installation._install_process.cancel = cancel_before

    def cancel_after_step(step):
        installation._install_process.cancel = True

    run_step = Mock(side_effect=cancel_after_step)
    monkeypatch.setattr(installation, "_run_install_step", run_step)

    installation.run()

    assert installation._install_process.state == ProcessState.canceled
    assert installation._install_process.progress == 100
    if cancel_before:
        run_step.assert_not_called()
        progress_updater.assert_not_called()
    else:
        run_step.assert_called_once_with(installation_steps[0])
        progress_updater.return_value.start.assert_called()
        progress_updater.return_value.finish.assert_called()


def test_install_steps_failure(
    installation, installation_steps, progress_updater, monkeypatch: pytest.MonkeyPatch
) -> None:
    from install_interface.data.installation_process_data import ProcessState

    run_step = Mock(side_effect=RuntimeError("Flash failed"))
    monkeypatch.setattr(installation, "_run_install_step", run_step)

    installation.run()

    run_step.assert_called_once_with(installation_steps[0])
    assert installation._install_process.state == ProcessState.error
    assert "Flash failed" in installation._install_process.info_message
    assert installation._install_process.progress == 100
    progress_updater.return_value.start.assert_called()
    progress_updater.return_value.finish.assert_called()


@pytest.fixture
def flasher(installation, monkeypatch: pytest.MonkeyPatch):
    module = sys.modules[type(installation).__module__]
    download = Mock()
    factory = Mock(return_value=Mock(download=download))
    connector = Mock()
    read_firmware = Mock(return_value=(0x08000000, b"abcd"))
    monkeypatch.setattr(module, "ELF32FileReader", Mock(return_value=Mock(read=read_firmware)))
    monkeypatch.setattr(module, "STM32Bootloader", factory)
    monkeypatch.setattr(module, "AlcotestOneConnectorSerial", Mock(return_value=connector))
    return SimpleNamespace(
        download=download, factory=factory, connector=connector, read_firmware=read_firmware
    )


def test_run_install_step_success(installation, installation_steps, flasher) -> None:
    step = installation_steps[0]

    def download(address, data, timeout):
        assert timeout > 0
        assert installation._install_process.is_cancelable is False

    flasher.download.side_effect = download

    result = installation._run_install_step(step)

    assert result is None
    flasher.read_firmware.assert_called_with(step.software_package)
    flasher.factory.assert_called_with("COM3")
    flasher.download.assert_called_once_with(0x08000000, b"abcd", step.timeout_duration)
    flasher.connector.assert_has_calls(
        [call.setup(), call.put("/service/startUpdate"), call.teardown()]
    )
    assert installation._install_process.is_cancelable is True
    assert installation._firmware_started is True


def test_run_install_step_failure(installation, installation_steps, flasher) -> None:
    error = RuntimeError("Firmware download failed")
    flasher.download.side_effect = error

    with pytest.raises(RuntimeError) as failure:
        installation._run_install_step(installation_steps[0])

    assert failure.value is error
    assert installation._install_process.is_cancelable is True
    assert installation._firmware_started is False


def test_run_install_step_timeout(installation, installation_steps, flasher) -> None:
    error = TimeoutError("Firmware download timed out")
    flasher.download.side_effect = error

    with pytest.raises(TimeoutError) as failure:
        installation._run_install_step(installation_steps[0])

    assert failure.value is error
    assert installation._install_process.is_cancelable is True
    assert installation._firmware_started is False


@pytest.mark.parametrize(
    ("component_type", "expected_ids"), [("Xplore8300", ["2776"]), ("other", [])]
)
def test_find_device_ids(installation, component_type, expected_ids) -> None:
    assert installation._find_device_ids_by_component_type(component_type) == expected_ids


def test_connection_error_restores_cancellation(installation, installation_steps, flasher) -> None:
    error = OSError("Cannot open the port")
    flasher.factory.side_effect = error

    with pytest.raises(OSError) as failure:
        installation._run_install_step(installation_steps[0])

    assert failure.value is error
    flasher.download.assert_not_called()
    assert installation._install_process.is_cancelable is True
    assert installation._firmware_started is False


@pytest.mark.parametrize(
    ("version", "firmware_started", "start_update"),
    [
        ("bootloader", False, False),
        ("bootloader 3.1", False, False),
        ("2.0-28", False, True),
        ("bootloader", True, True),
    ],
)
def test_run_install_step_selects_start_mode(
    installation, installation_steps, flasher, version, firmware_started, start_update
) -> None:
    installation._firmware_started = firmware_started
    installation._target_information.software_version = Mock(
        spec=["__str__"], __str__=Mock(return_value=version)
    )

    installation._run_install_step(installation_steps[0])

    if start_update:
        flasher.connector.put.assert_any_call("/service/startUpdate")
    else:
        flasher.connector.setup.assert_not_called()
        flasher.connector.put.assert_not_called()
        flasher.connector.teardown.assert_not_called()
    flasher.download.assert_called()


def test_bootloader_download_restarts_application_for_next_step(
    installation, installation_steps, flasher, progress_updater
) -> None:
    installation._target_information.software_version = "bootloader"
    events = Mock()
    events.attach_mock(flasher.download, "download")
    events.attach_mock(flasher.connector.put, "put")

    installation.run()

    assert installation._install_process.state == ProcessState.success
    events.assert_has_calls(
        [
            call.download(ANY, ANY, ANY),
            call.put("/service/startUpdate"),
            call.download(ANY, ANY, ANY),
        ]
    )


@pytest.mark.skip(reason="Hardware version detection not implemented")
def test_find_hardware_version_does_not_raise(installation) -> None:
    installation._find_hardware_version()
