# from __future__ import annotations

import logging
import time

import serial

logger = logging.getLogger(__name__)


class STM32Bootloader:
    _INIT_BYTE = b"\x7f"
    _ACK = b"\x79"
    _NACK = b"\x1f"
    _GET_ID_COMMAND = b"\x02\xfd"
    _GET_VERSION_COMMAND = b"\x01\xfe"
    _FRAME_DELIMITER = b"\x00"
    _BAUDRATE = 115200
    _TIMEOUT_SECONDS = 0.5
    _SETTLE_SECONDS = 0.3

    def __init__(self, com_port: str) -> None:
        self._com_port = com_port

    def probe(self) -> tuple[str | None, int | None] | None:
        """Probe before CoAP. CoAP commands can stop the bootloader response."""
        try:
            with serial.Serial(
                port=self._com_port,
                baudrate=self._BAUDRATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._TIMEOUT_SECONDS,
                write_timeout=self._TIMEOUT_SECONDS,
            ) as port:
                port.reset_output_buffer()
                time.sleep(self._SETTLE_SECONDS)
                port.reset_input_buffer()
                port.write(self._INIT_BYTE)
                port.flush()
                response = port.read(1)
                if response not in (self._ACK, self._NACK):
                    port.write(self._FRAME_DELIMITER)
                    port.flush()
                    return None
                product_id = self._get_id(port)
                bootloader_version = self._get_version(port)
        except (serial.SerialException, OSError) as error:
            logger.debug("Bootloader probe failed on %s: %s", self._com_port, error)
            return None
        return bootloader_version, product_id

    def _send_command(self, port: serial.Serial, command: bytes) -> bool:
        port.reset_input_buffer()
        port.write(command)
        port.flush()
        return port.read(1) == self._ACK

    def _get_id(self, port: serial.Serial) -> int | None:
        if not self._send_command(port, self._GET_ID_COMMAND):
            return None
        length = port.read(1)
        if not length:
            return None
        payload = port.read(length[0] + 1)
        acknowledgement = port.read(1)
        if len(payload) != length[0] + 1 or acknowledgement != self._ACK:
            return None
        return int.from_bytes(payload, "big")

    def _get_version(self, port: serial.Serial) -> str | None:
        if not self._send_command(port, self._GET_VERSION_COMMAND):
            return None
        version = port.read(1)
        options = port.read(2)
        acknowledgement = port.read(1)
        if not version or len(options) != 2 or acknowledgement != self._ACK:
            return None
        return f"{version[0] >> 4}.{version[0] & 0x0F}"
