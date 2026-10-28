import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tests.install_interface_mocks import NotApplicable, TargetInformationData
from tests.test_install_lib_contract import installer_library as installer_library
from tests.test_install_lib_contract import target_connector as target_connector
from tests.test_stm32_bootloader import serial_port as serial_port
from xplore_installer.stm32_bootloader import STM32Bootloader


@pytest.fixture
def bootloader(installer_library, target_connector, monkeypatch: pytest.MonkeyPatch):
    probe = Mock(return_value=None)
    factory = Mock(return_value=Mock(probe=probe))
    monkeypatch.setattr(sys.modules[type(installer_library).__module__], "STM32Bootloader", factory)
    events = Mock()
    events.attach_mock(probe, "bootloader")
    events.attach_mock(target_connector.factory, "coap")
    return SimpleNamespace(probe=probe, events=events, factory=factory)


def test_get_target_information_probes_bootloader_before_coap(
    installer_library, target_connector, bootloader
) -> None:
    result = installer_library.get_target_information("COM3")

    calls = bootloader.events.mock_calls
    assert calls.index(call.bootloader()) < calls.index(call.coap({"comport": "COM3"}))
    bootloader.factory.assert_called_with("COM3")
    target_connector.factory.assert_called_with({"comport": "COM3"})
    assert isinstance(result, TargetInformationData)
    assert result.software_version == "1.2.3"
    assert result.serial_number == "ABCD-1234"
    target_connector.connection.teardown.assert_called()


def test_get_target_information_accepts_matching_bootloader_without_coap(
    installer_library, target_connector, bootloader
) -> None:
    bootloader.probe.return_value = ("3.1", 0x0467)

    result = installer_library.get_target_information("COM3")

    target_connector.factory.assert_not_called()
    bootloader.probe.assert_called()
    bootloader.factory.assert_called_with("COM3")
    assert isinstance(result, TargetInformationData)
    assert result.connection_information == "COM3"
    assert result.assigned_library_name == "Xplore8300InstallLib"
    assert result.software_version == "bootloader"
    assert result.device_type == "2776"
    assert result.serial_number == "---"
    assert result.equipment_number == "NO_EQUIPMENT_NUMBER"


@pytest.mark.parametrize(
    ("version", "product_id"),
    [("3.1", 0x0468), ("3.2", 0x0467), (None, 0x0467), ("3.1", None)],
    ids=["other-chip", "other-version", "missing-version", "missing-chip"],
)
def test_get_target_information_uses_coap_after_unmatched_bootloader(
    installer_library,
    target_connector,
    bootloader,
    version: str | None,
    product_id: int | None,
) -> None:
    bootloader.probe.return_value = (version, product_id)

    result = installer_library.get_target_information("COM3")

    calls = bootloader.events.mock_calls
    assert calls.index(call.bootloader()) < calls.index(call.coap({"comport": "COM3"}))
    bootloader.factory.assert_called_with("COM3")
    target_connector.factory.assert_called_with({"comport": "COM3"})
    assert result.software_version == "1.2.3"
    assert result.serial_number == "ABCD-1234"
    target_connector.connection.teardown.assert_called()


def test_get_target_information_rejects_device_when_neither_protocol_responds(
    installer_library, target_connector, bootloader
) -> None:
    target_connector.connection.get.side_effect = TimeoutError("No CoAP reply")

    with pytest.raises(NotApplicable):
        installer_library.get_target_information("COM3")

    bootloader.probe.assert_called()
    bootloader.factory.assert_called_with("COM3")
    target_connector.connection.teardown.assert_called()


@pytest.fixture
def real_bootloader(installer_library, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        sys.modules[type(installer_library).__module__], "STM32Bootloader", STM32Bootloader
    )


def test_get_target_information_closes_serial_probe_before_coap(
    installer_library, target_connector, serial_port, real_bootloader
) -> None:
    serial_port.port.read.side_effect = [b"", b""]

    def create_connector(connection_information):
        serial_port.connection.__exit__.assert_called()
        serial_port.port.assert_has_calls([call.write(b"\x00"), call.flush()])
        return target_connector.connection

    target_connector.factory.side_effect = create_connector

    result = installer_library.get_target_information("COM3")

    assert result.software_version == "1.2.3"
    target_connector.factory.assert_called_with({"comport": "COM3"})
    target_connector.connection.teardown.assert_called()
