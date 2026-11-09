import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

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
        assert flasher.download.call_count >= len(installation_steps)
        return host_time

    clock = Mock(now=Mock(side_effect=read_host_time))
    monkeypatch.setattr(sys.modules[type(installation).__module__], "datetime", clock)

    installation.run()

    flasher.connector.put.assert_any_call("/service/time", utc=expected_time)
    assert installation._install_process.state == ProcessState.success


@pytest.fixture
def rtc_connector(installation, monkeypatch: pytest.MonkeyPatch):
    connector = Mock(spec=["setup", "put", "teardown"])
    factory = Mock(return_value=connector)
    monkeypatch.setattr(
        sys.modules[type(installation).__module__], "AlcotestOneConnectorSerial", factory
    )
    monkeypatch.setattr(installation, "_run_install_step", Mock())
    return SimpleNamespace(connection=connector, factory=factory)


@pytest.mark.parametrize("operation", ["setup", "put", "teardown"])
def test_install_steps_reports_rtc_error_without_failing_download(
    installation, installation_steps, progress_updater, rtc_connector, caplog, operation: str
) -> None:
    getattr(rtc_connector.connection, operation).side_effect = OSError("RTC unavailable")

    installation.run()

    assert installation._install_process.state == ProcessState.success
    assert "RTC unavailable" in caplog.text
    rtc_connector.factory.assert_called_with({"comport": "COM3"})
    if operation == "setup":
        rtc_connector.connection.put.assert_not_called()
    else:
        rtc_connector.connection.put.assert_called()
        rtc_connector.connection.teardown.assert_called()


@pytest.mark.parametrize(
    ("cancel", "fail", "expected_state"),
    [(True, False, ProcessState.canceled), (False, True, ProcessState.error)],
    ids=["canceled-download", "failed-download"],
)
def test_install_steps_does_not_set_rtc_after_interrupted_download(
    installation,
    installation_steps,
    progress_updater,
    rtc_connector,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
    fail: bool,
    expected_state: ProcessState,
) -> None:
    def run_step(step):
        installation._install_process.cancel = cancel
        if fail:
            raise RuntimeError("Flash failed")

    monkeypatch.setattr(installation, "_run_install_step", Mock(side_effect=run_step))

    installation.run()

    assert installation._install_process.state == expected_state
    rtc_connector.factory.assert_not_called()
