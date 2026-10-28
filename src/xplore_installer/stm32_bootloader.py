from __future__ import annotations

import logging
import time

import serial

logger = logging.getLogger(__name__)


class STM32BootloaderError(RuntimeError):
    pass


class STM32Bootloader:
    _INIT_BYTE = b"\x7f"
    _INIT_COMPLEMENT = b"\x80"
    _ACK = b"\x79"
    _NACK = b"\x1f"
    _GET_ID_COMMAND = b"\x02\xfd"
    _GET_VERSION_COMMAND = b"\x01\xfe"
    _FRAME_DELIMITER = b"\x00"
    _BAUDRATE = 115200
    _TIMEOUT_SECONDS = 0.5
    _SETTLE_SECONDS = 0.3
    _ERASE_TIMEOUT_SECONDS = 30
    _BOOT_SECONDS = 2
    _CONNECT_SECONDS = 40
    _CHUNK_SIZE = 256
    _READ_MEMORY_COMMAND = 0x11
    _GO_COMMAND = 0x21
    _WRITE_MEMORY_COMMAND = 0x31
    _ERASE_COMMAND = 0x44
    _UNPROTECT_COMMAND = 0x92

    def __init__(self, com_port: str) -> None:
        self._com_port = com_port
        self._deadline: float | None = None

    def _open_port(self) -> serial.Serial:
        return serial.Serial(
            port=self._com_port,
            baudrate=self._BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=self._TIMEOUT_SECONDS,
            write_timeout=self._TIMEOUT_SECONDS,
        )

    def probe(self) -> tuple[str | None, int | None] | None:
        """Probe before CoAP. CoAP commands can stop the bootloader response."""
        try:
            with self._open_port() as port:
                port.reset_output_buffer()
                time.sleep(self._SETTLE_SECONDS)
                port.reset_input_buffer()
                port.write(self._INIT_BYTE)
                port.flush()
                response = port.read(1)
                if response not in (self._ACK, self._NACK):
                    port.write(self._INIT_COMPLEMENT)
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

    def download(self, base_address: int, data: bytes, timeout_duration: float) -> None:
        """Limit serial operations to the step timeout. Wait for boot before CoAP."""
        if timeout_duration <= 0:
            raise ValueError("Download timeout must be positive.")
        if not data:
            raise ValueError("Firmware image must not be empty.")
        if len(data) % 4:
            data += b"\xff" * (4 - len(data) % 4)

        if base_address < 0 or base_address % 4 or base_address + len(data) > 0x100000000:
            raise ValueError("Firmware address or image size is not supported.")

        self._deadline = time.monotonic() + timeout_duration
        try:
            with self._open_port() as port:
                self._connect(port)
                logger.info("Removing readout protection on %s.", self._com_port)
                self._readout_unprotect(port)
                logger.info("Erasing flash.")
                self._mass_erase(port)
                logger.info("Writing %d bytes at 0x%08X.", len(data), base_address)
                self._write_flash(port, base_address, data)
                logger.info("Verifying firmware.")
                self._verify_flash(port, base_address, data)
                self._go(port, base_address)
        finally:
            self._deadline = None
        time.sleep(self._BOOT_SECONDS)
        logger.info("Firmware download completed.")

    def _read(self, port: serial.Serial, length: int, timeout: float | None = None) -> bytes:
        self._check_deadline()
        self._apply_read_timeout(port, self._TIMEOUT_SECONDS if timeout is None else timeout)
        return port.read(length)

    def _apply_read_timeout(self, port: serial.Serial, timeout: float) -> None:
        if port.timeout != timeout:
            port.timeout = timeout

    def _check_deadline(self) -> None:
        """Enforce the overall step timeout without touching the port timeout."""
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise TimeoutError("Firmware download timed out.")

    def _write(self, port: serial.Serial, data: bytes) -> None:
        self._check_deadline()
        port.write(data)
        port.flush()

    def _read_ack(self, port: serial.Serial, operation: str, timeout: float | None = None) -> None:
        reply = self._read(port, 1, timeout)
        if reply == self._ACK:
            return
        if not reply:
            raise TimeoutError(f"{operation}: no bootloader response.")
        raise STM32BootloaderError(f"{operation}: expected ACK, received {reply.hex()}.")

    def _command(
        self, port: serial.Serial, opcode: int, operation: str, timeout: float | None = None
    ) -> None:
        port.reset_input_buffer()
        self._write(port, bytes([opcode, opcode ^ 0xFF]))
        self._read_ack(port, operation, timeout)

    def _write_with_checksum(
        self, port: serial.Serial, payload: bytes, operation: str, timeout: float | None = None
    ) -> None:
        checksum = 0
        for byte in payload:
            checksum ^= byte
        self._write(port, payload + bytes([checksum]))
        self._read_ack(port, operation, timeout)

    def _connect(self, port: serial.Serial) -> None:
        """Retry initialization while the device enters the bootloader.

        The first reply after an application reset can take more than 30 seconds.
        A device that is already in bootloader mode can reply on the first attempt.
        """
        port.reset_output_buffer()
        time.sleep(self._SETTLE_SECONDS)
        deadline = time.monotonic() + self._CONNECT_SECONDS
        while True:
            port.reset_input_buffer()
            self._write(port, self._INIT_BYTE)
            if self._read(port, 1) in (self._ACK, self._NACK):
                return
            if time.monotonic() >= deadline:
                raise STM32BootloaderError("Bootloader did not respond to initialization.")
            time.sleep(self._SETTLE_SECONDS)

    def _readout_unprotect(self, port: serial.Serial) -> None:
        """The device can reset without a second ACK after it clears readout protection."""
        self._command(
            port, self._UNPROTECT_COMMAND, "readout unprotect", self._ERASE_TIMEOUT_SECONDS
        )
        try:
            self._read_ack(port, "readout unprotect completed", self._ERASE_TIMEOUT_SECONDS)
        except TimeoutError:
            pass
        time.sleep(self._SETTLE_SECONDS)
        self._connect(port)

    def _mass_erase(self, port: serial.Serial) -> None:
        self._command(port, self._ERASE_COMMAND, "erase", self._ERASE_TIMEOUT_SECONDS)
        self._write_with_checksum(port, b"\xff\xff", "mass erase", self._ERASE_TIMEOUT_SECONDS)

    def _write_memory(self, port: serial.Serial, address: int, data: bytes) -> None:
        if not data or len(data) > self._CHUNK_SIZE or len(data) % 4:
            raise STM32BootloaderError("Write length must be a multiple of 4, from 4 to 256 bytes.")
        self._command(port, self._WRITE_MEMORY_COMMAND, "write")
        self._write_with_checksum(port, address.to_bytes(4, "big"), "write address")
        self._write_with_checksum(port, bytes([len(data) - 1]) + data, "write data")

    def _read_memory(self, port: serial.Serial, address: int, length: int) -> bytes:
        if not 1 <= length <= self._CHUNK_SIZE:
            raise STM32BootloaderError("Read length must be from 1 to 256 bytes.")
        self._command(port, self._READ_MEMORY_COMMAND, "read")
        self._write_with_checksum(port, address.to_bytes(4, "big"), "read address")
        port.reset_input_buffer()
        self._write(port, bytes([length - 1, (length - 1) ^ 0xFF]))
        self._read_ack(port, "read count")
        data = self._read(port, length)
        if len(data) != length:
            raise STM32BootloaderError("Bootloader returned an incomplete memory read.")
        return data

    def _write_flash(self, port: serial.Serial, base_address: int, data: bytes) -> None:
        for offset in range(0, len(data), self._CHUNK_SIZE):
            self._write_memory(
                port, base_address + offset, data[offset : offset + self._CHUNK_SIZE]
            )

    def _verify_flash(self, port: serial.Serial, base_address: int, data: bytes) -> None:
        for offset in range(0, len(data), self._CHUNK_SIZE):
            chunk = data[offset : offset + self._CHUNK_SIZE]
            if self._read_memory(port, base_address + offset, len(chunk)) != chunk:
                raise STM32BootloaderError(
                    f"Firmware verification failed at 0x{base_address + offset:08X}."
                )

    def _go(self, port: serial.Serial, address: int) -> None:
        self._command(port, self._GO_COMMAND, "start application")
        self._write_with_checksum(port, address.to_bytes(4, "big"), "start address")
