import subprocess
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
        "pkg_resources": SimpleNamespace(resource_filename=Mock(return_value="flasher.exe")),
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


@pytest.mark.xfail(strict=True, raises=AttributeError, reason="Firmware state is not initialized.")
def test_setup_initializes_firmware_state(installation) -> None:
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


def test_install_steps_cancellation(
    installation, installation_steps, progress_updater, monkeypatch: pytest.MonkeyPatch
) -> None:
    from install_interface.data.installation_process_data import ProcessState

    def cancel_after_step(step) -> None:
        installation._install_process.cancel = True

    run_step = Mock(side_effect=cancel_after_step)
    monkeypatch.setattr(installation, "_run_install_step", run_step)

    installation.run()

    run_step.assert_called_once_with(installation_steps[0])
    assert installation._install_process.state == ProcessState.canceled
    assert installation._install_process.progress == 100
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
    process = Mock(returncode=0)
    process.communicate.return_value = (b"Flash completed", b"")
    startup_info = SimpleNamespace(dwFlags=0, wShowWindow=0)
    start_process = Mock(return_value=process)
    process_api = SimpleNamespace(
        STARTUPINFO=Mock(return_value=startup_info),
        STARTF_USESHOWWINDOW=1,
        SW_HIDE=0,
        PIPE=subprocess.PIPE,
        Popen=start_process,
        SubprocessError=subprocess.SubprocessError,
        CalledProcessError=subprocess.CalledProcessError,
    )
    connector = Mock()
    monkeypatch.setattr(module, "subprocess", process_api)
    monkeypatch.setattr(module, "AlcotestOneConnectorSerial", Mock(return_value=connector))
    installation._firmware_started = False
    return SimpleNamespace(
        process=process,
        start_process=start_process,
        startup_info=startup_info,
        connector=connector,
    )


def test_run_install_step_success(installation, installation_steps, flasher, caplog) -> None:
    step = installation_steps[0]

    def communicate(*, timeout):
        assert timeout > 0
        assert installation._install_process.is_cancelable is False
        return b"Flash completed", b""

    flasher.process.communicate.side_effect = communicate
    with caplog.at_level("DEBUG"):
        result = installation._run_install_step(step)

    assert result is None
    flasher.start_process.assert_called_once_with(
        f'{installation.get_flasher_exe_path()} -c port="COM3" -rdu -e all '
        f'-d "{step.software_package}" -v -s',
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        startupinfo=flasher.startup_info,
    )
    assert flasher.startup_info.dwFlags & 1
    assert flasher.startup_info.wShowWindow == 0
    flasher.connector.assert_has_calls(
        [call.setup(), call.put("/service/startUpdate"), call.teardown()]
    )
    flasher.process.kill.assert_not_called()
    assert installation._install_process.is_cancelable is True
    assert "Flash completed" in caplog.text


def test_run_install_step_failure(installation, installation_steps, flasher, caplog) -> None:
    step = installation_steps[0]
    flasher.process.returncode = 2
    flasher.process.communicate.return_value = (b"Flash output", b"Flash failed")

    with pytest.raises(subprocess.CalledProcessError) as failure:
        installation._run_install_step(step)

    assert failure.value.returncode == 2
    assert failure.value.cmd == flasher.start_process.call_args.args[0]
    flasher.process.assert_has_calls(
        [call.communicate(timeout=ANY), call.kill(), call.communicate()]
    )
    assert installation._install_process.is_cancelable is True
    assert "Flash failed" in caplog.text


def test_run_install_step_timeout(installation, installation_steps, flasher, caplog) -> None:
    step = installation_steps[0]
    timeout = subprocess.TimeoutExpired("flasher.exe", step.timeout_duration)
    flasher.process.communicate.side_effect = [timeout, (b"Flash output", b"Flash timed out")]

    with pytest.raises(subprocess.TimeoutExpired) as failure:
        installation._run_install_step(step)

    assert failure.value is timeout
    flasher.process.assert_has_calls(
        [call.communicate(timeout=ANY), call.kill(), call.communicate()]
    )
    assert installation._install_process.is_cancelable is True
    assert "Flash timed out" in caplog.text


@pytest.mark.parametrize(
    ("component_type", "expected_ids"), [("Xplore8300", ["2776"]), ("other", [])]
)
def test_find_device_ids(installation, component_type, expected_ids) -> None:
    assert installation._find_device_ids_by_component_type(component_type) == expected_ids


@pytest.mark.parametrize("error_type", [OSError, subprocess.SubprocessError])
def test_process_start_error_restores_cancellation(
    installation, installation_steps, flasher, error_type
) -> None:
    error = error_type("Cannot start the flasher")
    flasher.start_process.side_effect = error

    with pytest.raises(error_type) as failure:
        installation._run_install_step(installation_steps[0])

    assert failure.value is error
    flasher.process.communicate.assert_not_called()
    flasher.process.kill.assert_not_called()
    assert installation._install_process.is_cancelable is True


@pytest.mark.parametrize("failed_stream", ["stdout", "stderr"])
def test_logging_error_does_not_stop_output_logging(installation, caplog, failed_stream) -> None:
    def log_output(message):
        if message == ("Flash output" if failed_stream == "stdout" else "Flash error"):
            raise RuntimeError("Logger unavailable")

    logmethod = Mock(side_effect=log_output)

    installation._log_installation_output(b"Flash output", b"Flash error", logmethod)

    logmethod.assert_has_calls([call("Flash output"), call("Flash error")], any_order=True)
    assert failed_stream in caplog.text
    assert "Logger unavailable" in caplog.text


def test_output_logging_replaces_invalid_utf8(installation) -> None:
    logmethod = Mock()

    installation._log_installation_output(b"\xff", b"", logmethod)

    logmethod.assert_has_calls([call("\ufffd"), call("")], any_order=True)


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
    flasher.process.communicate.assert_called()


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="Firmware state is not updated.")
def test_bootloader_download_restarts_application_for_next_step(
    installation, installation_steps, flasher, progress_updater
) -> None:
    installation._target_information.software_version = "bootloader"

    installation.run()

    assert installation._install_process.state == ProcessState.success
    assert flasher.start_process.call_count == len(installation_steps)
    flasher.connector.put.assert_any_call("/service/startUpdate")


@pytest.mark.skip(reason="Hardware version detection not implemented")
def test_find_hardware_version_does_not_raise(installation) -> None:
    installation._find_hardware_version()
