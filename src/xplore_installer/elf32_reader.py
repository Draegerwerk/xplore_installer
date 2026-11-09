import struct
from pathlib import Path


class ELF32FileReader:
    _HEADER_SIZE = 52
    _PROGRAM_HEADER_SIZE = 32
    _MAX_IMAGE_SIZE = 16 * 1024 * 1024

    def read(self, file_path: str) -> tuple[int, bytes]:
        return self.decode(Path(file_path).read_bytes())

    def decode(self, elf: bytes) -> tuple[int, bytes]:
        """Load ELF32 segments by physical address. Fill gaps with erased flash bytes."""
        if len(elf) < self._HEADER_SIZE or elf[:6] != b"\x7fELF\x01\x01":
            raise ValueError("Firmware must be an ELF32 file with little-endian byte order.")
        header_offset = struct.unpack_from("<I", elf, 0x1C)[0]
        header_size, header_count = struct.unpack_from("<HH", elf, 0x2A)
        if (
            header_size < self._PROGRAM_HEADER_SIZE
            or header_offset + header_size * header_count > len(elf)
        ):
            raise ValueError("Firmware has an invalid program header table.")

        segments = []
        for index in range(header_count):
            offset = header_offset + index * header_size
            segment_type, file_offset, _, address, file_size = struct.unpack_from(
                "<IIIII", elf, offset
            )
            if segment_type != 1 or file_size == 0:
                continue
            if file_offset + file_size > len(elf) or address + file_size > 0x100000000:
                raise ValueError("Firmware has an invalid load segment.")
            segments.append((address, elf[file_offset : file_offset + file_size]))
        if not segments:
            raise ValueError("Firmware has no load segments.")

        segments.sort()
        base_address = segments[0][0]
        end_address = max(address + len(data) for address, data in segments)
        if base_address % 4 or end_address - base_address > self._MAX_IMAGE_SIZE:
            raise ValueError("Firmware address or image size is not supported.")
        image = bytearray(b"\xff" * (end_address - base_address))
        previous_end = base_address
        for address, data in segments:
            if address < previous_end:
                raise ValueError("Firmware load segments overlap.")
            offset = address - base_address
            image[offset : offset + len(data)] = data
            previous_end = address + len(data)
        return base_address, bytes(image)
