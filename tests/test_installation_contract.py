import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

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

    return Xplore8300Installation(InstallationProcessData(), Mock(), Mock())


def test_setup_does_not_raise(installation) -> None:
    installation._setup()


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
    assert progress_updater.return_value.start.call_count == 2
    assert progress_updater.return_value.finish.call_count == 2


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
    assert installation._install_process.info_message == "Installation canceled."
    assert installation._install_process.progress == 100
    progress_updater.assert_called_once()
    progress_updater.return_value.start.assert_called_once_with()
    progress_updater.return_value.finish.assert_called_once_with()


def test_install_steps_failure(
    installation, installation_steps, progress_updater, monkeypatch: pytest.MonkeyPatch
) -> None:
    from install_interface.data.installation_process_data import ProcessState

    run_step = Mock(side_effect=RuntimeError("Flash failed"))
    monkeypatch.setattr(installation, "_run_install_step", run_step)

    installation.run()

    run_step.assert_called_once_with(installation_steps[0])
    assert installation._install_process.state == ProcessState.error
    assert installation._install_process.info_message == "Update error: Flash failed"
    assert installation._install_process.progress == 100
    progress_updater.assert_called_once()
    progress_updater.return_value.start.assert_called_once_with()
    progress_updater.return_value.finish.assert_called_once_with()


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
    installation._target_information.connection_information = "COM3"
    return SimpleNamespace(
        process=process,
        start_process=start_process,
        startup_info=startup_info,
        connector=connector,
    )


def test_run_install_step_success(installation, installation_steps, flasher, caplog) -> None:
    step = installation_steps[0]

    def communicate(*, timeout):
        assert timeout == step.timeout_duration
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
    assert flasher.startup_info.dwFlags == 1
    assert flasher.startup_info.wShowWindow == 0
    assert flasher.connector.method_calls == [
        call.setup(),
        call.put("service/startUpdate"),
        call.teardown(),
    ]
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
    assert flasher.process.communicate.call_args_list == [
        call(timeout=step.timeout_duration),
        call(),
    ]
    flasher.process.kill.assert_called_once_with()
    assert installation._install_process.is_cancelable is True
    assert "Flash failed" in caplog.text


def test_run_install_step_timeout(installation, installation_steps, flasher, caplog) -> None:
    step = installation_steps[0]
    timeout = subprocess.TimeoutExpired("flasher.exe", step.timeout_duration)
    flasher.process.communicate.side_effect = [timeout, (b"Flash output", b"Flash timed out")]

    with pytest.raises(subprocess.TimeoutExpired) as failure:
        installation._run_install_step(step)

    assert failure.value is timeout
    assert flasher.process.method_calls == [
        call.communicate(timeout=step.timeout_duration),
        call.kill(),
        call.communicate(),
    ]
    assert installation._install_process.is_cancelable is True
    assert "Flash timed out" in caplog.text


def test_find_device_ids_does_not_raise(installation) -> None:
    installation._find_device_ids_by_component_type("Xplore8300")


@pytest.mark.skip(reason="Hardware version detection not implemented")
def test_find_hardware_version_does_not_raise(installation) -> None:
    installation._find_hardware_version()
