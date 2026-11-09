import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tests.install_interface_mocks import ProcessState
from tests.test_installation_contract import flasher as flasher
from tests.test_installation_contract import installation as installation
from tests.test_installation_contract import installation_steps as installation_steps
from tests.test_installation_contract import progress_updater as progress_updater


def test_install_steps_sets_rtc_after_downloads(
    installation,
    installation_steps,
    progress_updater,
    flasher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_time = datetime(2026, 10, 28, 12, 34, 56, tzinfo=UTC)
    expected_time = "2026-10-28T12:34:56.00Z"

    def read_host_time(timezone):
        assert timezone == UTC
        assert flasher.process.communicate.call_count == len(installation_steps)
        return host_time

    clock = Mock(now=Mock(side_effect=read_host_time))
    monkeypatch.setattr(sys.modules[type(installation).__module__], "datetime", clock)
    events = Mock()
    events.attach_mock(flasher.process.communicate, "download")
    events.attach_mock(flasher.connector.setup, "connect")
    events.attach_mock(flasher.connector.put, "put")
    events.attach_mock(flasher.connector.teardown, "disconnect")

    installation.run()

    flasher.connector.put.assert_any_call("/service/time", utc=expected_time)
    assert events.mock_calls[-4][0] == "download"
    assert events.mock_calls[-3:] == [
        call.connect(),
        call.put("/service/time", utc=expected_time),
        call.disconnect(),
    ]
    clock.now.assert_called_once_with(UTC)
    assert installation._install_process.state == ProcessState.success


@pytest.fixture
def rtc_connector(installation, installation_steps, flasher, monkeypatch: pytest.MonkeyPatch):
    connector = Mock(spec=["setup", "put", "teardown"])
    factory = Mock(side_effect=[flasher.connector] * len(installation_steps) + [connector])
    monkeypatch.setattr(
        sys.modules[type(installation).__module__], "AlcotestOneConnectorSerial", factory
    )
    return SimpleNamespace(connection=connector, factory=factory)


@pytest.mark.parametrize("operation", ["setup", "put", "teardown"])
def test_install_steps_reports_rtc_error_without_failing_download(
    installation, installation_steps, progress_updater, rtc_connector, caplog, operation: str
) -> None:
    getattr(rtc_connector.connection, operation).side_effect = OSError("RTC unavailable")

    installation.run()

    assert installation._install_process.state == ProcessState.success
    assert "Could not set device RTC: RTC unavailable" in caplog.text
    assert rtc_connector.factory.call_args_list == [call({"comport": "COM3"})] * (
        len(installation_steps) + 1
    )
    if operation == "setup":
        rtc_connector.connection.put.assert_not_called()
        rtc_connector.connection.teardown.assert_not_called()
    else:
        rtc_connector.connection.put.assert_called_once()
        rtc_connector.connection.teardown.assert_called_once_with()


@pytest.mark.parametrize(
    ("cancel", "return_code", "expected_state"),
    [(True, 0, ProcessState.canceled), (False, 2, ProcessState.error)],
    ids=["canceled-download", "failed-download"],
)
def test_install_steps_does_not_set_rtc_after_interrupted_download(
    installation,
    installation_steps,
    progress_updater,
    flasher,
    rtc_connector,
    cancel: bool,
    return_code: int,
    expected_state: ProcessState,
) -> None:
    flasher.process.returncode = return_code

    def communicate(*, timeout=None):
        installation._install_process.cancel = cancel
        return b"Flash output", b""

    flasher.process.communicate.side_effect = communicate

    installation.run()

    assert installation._install_process.state == expected_state
    rtc_connector.connection.setup.assert_not_called()
    rtc_connector.connection.put.assert_not_called()
    rtc_connector.connection.teardown.assert_not_called()
