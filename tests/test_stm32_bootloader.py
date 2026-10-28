import sys
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, Mock, call

import pytest

from xplore_installer.stm32_bootloader import STM32Bootloader, STM32BootloaderError


@pytest.fixture
def stm32_bootloader():
    return STM32Bootloader("COM3")


@pytest.fixture
def serial_port(monkeypatch: pytest.MonkeyPatch):
    port = Mock(
        spec=[
            "reset_output_buffer",
            "reset_input_buffer",
            "write",
            "flush",
            "read",
            "timeout",
            "write_timeout",
        ]
    )
    port.timeout = STM32Bootloader._TIMEOUT_SECONDS
    port.write_timeout = STM32Bootloader._TIMEOUT_SECONDS
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
    monotonic = Mock(return_value=0)
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=sleep, monotonic=monotonic))
    return SimpleNamespace(
        port=port, connection=connection, factory=factory, sleep=sleep, monotonic=monotonic
    )


@pytest.mark.parametrize(
    "initial_replies",
    [[b"\x79"], [b"\x1f"], [b"", b"\x79"]],
    ids=["ack", "nack", "initial-complement"],
)
def test_bootloader_probe_reads_chip_and_version(stm32_bootloader, serial_port, initial_replies):
    serial_port.port.read.side_effect = initial_replies + [
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
    if len(initial_replies) > 1:
        serial_port.port.write.assert_any_call(b"\x80")


@pytest.mark.parametrize("reply", [b"", b"\x00"], ids=["timeout", "other-reply"])
def test_bootloader_probe_clears_frame_when_device_does_not_respond(
    stm32_bootloader, serial_port, reply: bytes
) -> None:
    serial_port.port.read.side_effect = [reply, reply]

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

    serial_port.connection.__exit__.assert_called()


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


def download_replies(data: bytes) -> list[bytes]:
    size = STM32Bootloader._CHUNK_SIZE
    chunks = [data[offset : offset + size] for offset in range(0, len(data), size)]
    replies = [b"\x79"] * 6
    for _ in chunks:
        replies.extend([b"\x79"] * 3)
    for chunk in chunks:
        replies.extend([b"\x79"] * 3 + [chunk])
    return replies + [b"\x79"] * 2


def test_download_uses_rom_commands_and_waits(stm32_bootloader, serial_port) -> None:
    data = b"\x01\x02\x03\x04"
    serial_port.port.read.side_effect = download_replies(data)
    events = Mock()
    events.attach_mock(serial_port.connection.__exit__, "close")
    events.attach_mock(serial_port.sleep, "sleep")

    stm32_bootloader.download(0x08000000, b"\x01\x02\x03\x04", 100)

    assert [call.args[0] for call in serial_port.port.write.call_args_list] == [
        b"\x7f",
        b"\x92\x6d",
        b"\x7f",
        b"\x44\xbb",
        b"\xff\xff\x00",
        b"\x31\xce",
        b"\x08\x00\x00\x00\x08",
        b"\x03\x01\x02\x03\x04\x07",
        b"\x11\xee",
        b"\x08\x00\x00\x00\x08",
        b"\x03\xfc",
        b"\x21\xde",
        b"\x08\x00\x00\x00\x08",
    ]
    events.assert_has_calls([call.close(ANY, ANY, ANY), call.sleep(ANY)])
    assert stm32_bootloader._deadline is None


@pytest.mark.parametrize("data", [b"abc", bytes(range(256)) + b"abcd"])
def test_download_pads_and_verifies_each_chunk(stm32_bootloader, serial_port, data):
    padded = data + b"\xff" * (-len(data) % 4)
    serial_port.port.read.side_effect = download_replies(padded)

    stm32_bootloader.download(0x08000000, data, 100)

    frames = [call.args[0] for call in serial_port.port.write.call_args_list]
    written = [
        (frames[index + 1][:-1], frames[index + 2][1:-1])
        for index, frame in enumerate(frames)
        if frame == b"\x31\xce"
    ]
    assert b"".join(chunk for _, chunk in written) == padded
    address = 0x08000000
    for actual_address, chunk in written:
        assert actual_address == address.to_bytes(4, "big")
        assert 0 < len(chunk) <= 256
        address += len(chunk)
    assert frames.count(b"\x11\xee") == len(written)
    serial_port.connection.__exit__.assert_called()


def test_download_accepts_reset_without_second_unprotect_ack(stm32_bootloader, serial_port) -> None:
    replies = download_replies(b"\x01\x02\x03\x04")
    replies[2] = b""
    serial_port.port.read.side_effect = replies

    stm32_bootloader.download(0x08000000, b"\x01\x02\x03\x04", 100)

    serial_port.connection.__exit__.assert_called()
    serial_port.sleep.assert_called()


@pytest.mark.parametrize(
    ("reply_index", "last_frame"),
    [
        (1, b"\x92\x6d"),
        (2, b"\x92\x6d"),
        (4, b"\x44\xbb"),
        (6, b"\x31\xce"),
        (9, b"\x11\xee"),
        (13, b"\x21\xde"),
    ],
    ids=["unprotect", "unprotect-done", "erase", "write", "read", "go"],
)
def test_download_stops_after_nack(stm32_bootloader, serial_port, reply_index, last_frame):
    replies = download_replies(b"\x01\x02\x03\x04")
    replies[reply_index] = b"\x1f"
    serial_port.port.read.side_effect = replies

    with pytest.raises(STM32BootloaderError, match="expected ACK"):
        stm32_bootloader.download(0x08000000, b"\x01\x02\x03\x04", 100)

    serial_port.connection.__exit__.assert_called()
    assert stm32_bootloader._deadline is None
    assert serial_port.port.write.call_args.args == (last_frame,)


def test_download_does_not_start_application_after_verification_failure(
    stm32_bootloader, serial_port
) -> None:
    replies = download_replies(b"\x01\x02\x03\x04")
    replies[12] = b"\x01\x02\x03\x05"
    serial_port.port.read.side_effect = replies

    with pytest.raises(STM32BootloaderError, match="verification failed"):
        stm32_bootloader.download(0x08000000, b"\x01\x02\x03\x04", 100)

    assert b"\x21\xde" not in [call.args[0] for call in serial_port.port.write.call_args_list]
    serial_port.connection.__exit__.assert_called()


def test_download_closes_port_after_step_timeout(stm32_bootloader, serial_port):
    def settle(delay):
        serial_port.monotonic.return_value = 2

    serial_port.sleep.side_effect = settle

    with pytest.raises(TimeoutError, match="download timed out"):
        stm32_bootloader.download(0x08000000, b"\x01\x02\x03\x04", 1)

    serial_port.connection.__exit__.assert_called()
    assert stm32_bootloader._deadline is None


def test_download_preserves_serial_open_error(stm32_bootloader, serial_port):
    error = OSError("Port unavailable")
    serial_port.factory.side_effect = error

    with pytest.raises(OSError) as failure:
        stm32_bootloader.download(0x08000000, b"\x01\x02\x03\x04", 100)

    assert failure.value is error
    assert stm32_bootloader._deadline is None


@pytest.mark.parametrize("timeout_duration", [0, -1])
def test_download_rejects_invalid_timeout_before_opening_port(
    stm32_bootloader, serial_port, timeout_duration
) -> None:
    with pytest.raises(ValueError, match="positive"):
        stm32_bootloader.download(0x08000000, b"\x01\x02\x03\x04", timeout_duration)

    serial_port.factory.assert_not_called()


def test_download_rejects_empty_data_before_opening_port(stm32_bootloader, serial_port):
    with pytest.raises(ValueError, match="must not be empty"):
        stm32_bootloader.download(0x08000000, b"", 100)

    serial_port.factory.assert_not_called()


@pytest.mark.parametrize(
    ("base_address", "data"),
    [(-4, b"abcd"), (0x08000001, b"abcd"), (0x100000000, b"abcd"), (0xFFFFFFFC, b"abcde")],
    ids=["negative", "unaligned", "outside-address-range", "padded-image-overflow"],
)
def test_download_rejects_invalid_address_before_opening_port(
    stm32_bootloader, serial_port, base_address, data
):
    with pytest.raises(ValueError, match="address or image size"):
        stm32_bootloader.download(base_address, data, 100)

    serial_port.factory.assert_not_called()


def test_download_accepts_last_aligned_address(stm32_bootloader, serial_port):
    serial_port.port.read.side_effect = download_replies(b"abc\xff")

    stm32_bootloader.download(0xFFFFFFFC, b"abc", 100)

    frames = [call.args[0] for call in serial_port.port.write.call_args_list]
    assert frames[-1] == b"\xff\xff\xff\xfc\x03"
    serial_port.connection.__exit__.assert_called()


@pytest.mark.parametrize("reply", [b"\x79", b"\x1f"])
def test_connect_retries_lost_initial_byte(stm32_bootloader, serial_port, reply):
    serial_port.port.read.side_effect = [b"", reply]

    stm32_bootloader._connect(serial_port.port)

    assert serial_port.port.write.call_count >= 2
    serial_port.sleep.assert_called()


def test_connect_waits_for_slow_application_reset(stm32_bootloader, serial_port):
    def read(length):
        if serial_port.monotonic.return_value < 32:
            serial_port.monotonic.return_value += 8
            return b""
        return b"\x79"

    serial_port.port.read.side_effect = read

    stm32_bootloader._connect(serial_port.port)

    serial_port.port.write.assert_any_call(b"\x7f")
    serial_port.sleep.assert_called()


def test_connect_fails_when_device_does_not_respond(stm32_bootloader, serial_port):
    serial_port.port.read.side_effect = [b"", b""]
    serial_port.monotonic.side_effect = [0, 0, STM32Bootloader._CONNECT_SECONDS]

    with pytest.raises(STM32BootloaderError, match="initialization"):
        stm32_bootloader._connect(serial_port.port)


@pytest.mark.parametrize(
    ("reply", "error_type"),
    [(b"", TimeoutError), (b"\x1f", STM32BootloaderError), (b"\x00", STM32BootloaderError)],
)
def test_read_ack_reports_invalid_response(stm32_bootloader, serial_port, reply, error_type):
    serial_port.port.read.side_effect = [reply]

    with pytest.raises(error_type):
        stm32_bootloader._read_ack(serial_port.port, "read")


def test_read_preserves_timeout_after_serial_error(stm32_bootloader, serial_port):
    error = OSError("Read failed")
    requested_timeout = 30

    def read(length):
        assert serial_port.port.timeout == requested_timeout
        raise error

    serial_port.port.read.side_effect = read

    with pytest.raises(OSError) as failure:
        stm32_bootloader._read(serial_port.port, 1, requested_timeout)

    assert failure.value is error
    assert serial_port.port.timeout == requested_timeout


def test_read_keeps_timeout_until_next_operation(stm32_bootloader, serial_port):
    stm32_bootloader._deadline = 0.25
    requested_timeout = 30

    def read(length):
        assert serial_port.port.timeout == requested_timeout
        return b"\x79"

    serial_port.port.read.side_effect = read

    assert stm32_bootloader._read(serial_port.port, 1, requested_timeout) == b"\x79"
    assert serial_port.port.timeout == requested_timeout
    serial_port.port.read.side_effect = None
    serial_port.port.read.return_value = b"\x79"
    assert stm32_bootloader._read(serial_port.port, 1) == b"\x79"
    assert serial_port.port.timeout == stm32_bootloader._TIMEOUT_SECONDS


def test_erase_does_not_send_command_after_step_timeout(stm32_bootloader, serial_port):
    stm32_bootloader._deadline = 0

    with pytest.raises(TimeoutError, match="download timed out"):
        stm32_bootloader._mass_erase(serial_port.port)

    serial_port.port.write.assert_not_called()


def test_write_preserves_timeout_after_serial_error(stm32_bootloader, serial_port):
    stm32_bootloader._deadline = 0.25
    error = OSError("Write failed")
    original_timeout = serial_port.port.write_timeout
    serial_port.port.write.side_effect = error

    with pytest.raises(OSError) as failure:
        stm32_bootloader._write(serial_port.port, b"\x7f")

    assert failure.value is error
    assert serial_port.port.write_timeout == original_timeout


@pytest.mark.parametrize("data", [b"", b"abc", bytes(257)])
def test_write_memory_rejects_invalid_chunk_before_command(stm32_bootloader, serial_port, data):
    with pytest.raises(STM32BootloaderError, match="Write length"):
        stm32_bootloader._write_memory(serial_port.port, 0x08000000, data)

    serial_port.port.write.assert_not_called()


@pytest.mark.parametrize("length", [0, 257])
def test_read_memory_rejects_invalid_length_before_command(stm32_bootloader, serial_port, length):
    with pytest.raises(STM32BootloaderError, match="Read length"):
        stm32_bootloader._read_memory(serial_port.port, 0x08000000, length)

    serial_port.port.write.assert_not_called()


def test_read_memory_rejects_short_response(stm32_bootloader, serial_port):
    serial_port.port.read.side_effect = [b"\x79", b"\x79", b"\x79", b"ab"]

    with pytest.raises(STM32BootloaderError, match="incomplete"):
        stm32_bootloader._read_memory(serial_port.port, 0x08000000, 4)
