import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

from xplore_installer.stm32_bootloader import STM32Bootloader


@pytest.fixture
def stm32_bootloader():
    return STM32Bootloader("COM3")


@pytest.fixture
def serial_port(monkeypatch: pytest.MonkeyPatch):
    port = Mock(spec=["reset_output_buffer", "reset_input_buffer", "write", "flush", "read"])
    port.read.side_effect = [
        b"\x79",
        b"\x79",
        b"\x01",
        b"\x04\x67",
        b"\x79",
        b"\x79",
        b"\x31",
        b"\x00\x00",
        b"\x79",
    ]
    connection = MagicMock()
    connection.__enter__.return_value = port
    connection.__exit__.return_value = False
    factory = Mock(return_value=connection)
    sleep = Mock()
    module = sys.modules[STM32Bootloader.__module__]
    monkeypatch.setattr(
        module,
        "serial",
        SimpleNamespace(
            Serial=factory,
            SerialException=OSError,
            EIGHTBITS=8,
            PARITY_EVEN="E",
            STOPBITS_ONE=1,
        ),
    )
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=sleep))
    return SimpleNamespace(port=port, connection=connection, factory=factory, sleep=sleep)


@pytest.mark.parametrize("reply", [b"\x79", b"\x1f"], ids=["ack", "nack"])
def test_bootloader_probe_reads_chip_and_version(stm32_bootloader, serial_port, reply: bytes):
    serial_port.port.read.side_effect = [
        reply,
        b"\x79",
        b"\x01",
        b"\x04\x67",
        b"\x79",
        b"\x79",
        b"\x31",
        b"\x00\x00",
        b"\x79",
    ]

    result = stm32_bootloader.probe()

    assert result == ("3.1", 0x0467)
    serial_port.sleep.assert_called()


@pytest.mark.parametrize("reply", [b"", b"\x00"], ids=["timeout", "other-reply"])
def test_bootloader_probe_clears_frame_when_device_does_not_respond(
    stm32_bootloader, serial_port, reply: bytes
) -> None:
    serial_port.port.read.side_effect = [reply]

    assert stm32_bootloader.probe() is None

    serial_port.port.write.assert_any_call(b"\x7f")
    serial_port.port.assert_has_calls([call.write(b"\x00"), call.flush()])
    serial_port.sleep.assert_called()


def test_bootloader_probe_returns_none_when_port_cannot_open(stm32_bootloader, serial_port) -> None:
    serial_port.factory.side_effect = OSError("Port unavailable")

    assert stm32_bootloader.probe() is None

    serial_port.connection.__enter__.assert_not_called()


@pytest.mark.parametrize("operation", ["write", "read"])
def test_bootloader_probe_closes_port_after_serial_error(
    stm32_bootloader, serial_port, operation: str
) -> None:
    getattr(serial_port.port, operation).side_effect = OSError("Serial transfer failed")

    assert stm32_bootloader.probe() is None

    serial_port.connection.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "replies",
    [
        [b"\x1f"],
        [b"\x79", b""],
        [b"\x79", b"\x01", b"\x04", b"\x79"],
        [b"\x79", b"\x01", b"\x04\x67", b""],
        [b"\x79", b"\x01", b"\x04\x67", b"\x1f"],
    ],
    ids=["command-rejected", "missing-length", "short-id", "missing-ack", "nack"],
)
def test_bootloader_chip_id_rejects_incomplete_response(
    stm32_bootloader, serial_port, replies: list[bytes]
) -> None:
    serial_port.port.read.side_effect = replies

    assert stm32_bootloader._get_id(serial_port.port) is None


@pytest.mark.parametrize(
    "replies",
    [
        [b"\x1f"],
        [b"\x79", b"", b"\x00\x00", b"\x79"],
        [b"\x79", b"\x31", b"\x00", b"\x79"],
        [b"\x79", b"\x31", b"\x00\x00", b""],
        [b"\x79", b"\x31", b"\x00\x00", b"\x1f"],
    ],
    ids=["command-rejected", "missing-version", "short-options", "missing-ack", "nack"],
)
def test_bootloader_version_rejects_incomplete_response(
    stm32_bootloader, serial_port, replies: list[bytes]
) -> None:
    serial_port.port.read.side_effect = replies

    assert stm32_bootloader._get_version(serial_port.port) is None
