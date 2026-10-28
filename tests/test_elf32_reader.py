import struct

import pytest

from xplore_installer.elf32_reader import ELF32FileReader


def make_elf(segments: list[tuple[int, bytes]]) -> bytes:
    image = bytearray(52 + 32 * len(segments))
    image[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HHI", image, 16, 2, 40, 1)
    struct.pack_into("<I", image, 0x1C, 52)
    struct.pack_into("<HHH", image, 0x28, 52, 32, len(segments))
    for index, (address, data) in enumerate(segments):
        struct.pack_into(
            "<IIIIIIII",
            image,
            52 + 32 * index,
            1,
            len(image),
            address,
            address,
            len(data),
            len(data),
            5,
            4,
        )
        image.extend(data)
    return bytes(image)


def test_read_uses_physical_addresses_and_fills_gaps(tmp_path) -> None:
    path = tmp_path / "firmware.elf"
    elf = bytearray(make_elf([(0x08000008, b"\x05\x06"), (0x08000000, b"\x01\x02\x03\x04")]))
    struct.pack_into("<I", elf, 52 + 8, 0x20000000)
    path.write_bytes(elf)

    assert ELF32FileReader().read(str(path)) == (
        0x08000000,
        b"\x01\x02\x03\x04\xff\xff\xff\xff\x05\x06",
    )


@pytest.mark.parametrize(
    "elf",
    [b"", b"not ELF" + bytes(52), b"\x7fELF\x02\x01" + bytes(52), b"\x7fELF\x01\x02" + bytes(52)],
)
def test_decode_rejects_unsupported_header(elf: bytes) -> None:
    with pytest.raises(ValueError, match="ELF32"):
        ELF32FileReader().decode(elf)


@pytest.mark.parametrize(
    ("offset", "format_string", "value"), [(0x2A, "<H", 16), (0x1C, "<I", 1000), (0x2C, "<H", 100)]
)
def test_decode_rejects_invalid_program_header_table(offset, format_string, value) -> None:
    elf = bytearray(make_elf([(0x08000000, b"abcd")]))
    struct.pack_into(format_string, elf, offset, value)

    with pytest.raises(ValueError, match="program header"):
        ELF32FileReader().decode(elf)


@pytest.mark.parametrize(("offset", "value"), [(52 + 4, 1000), (52 + 12, 0xFFFFFFFF)])
def test_decode_rejects_invalid_load_segment(offset: int, value: int) -> None:
    elf = bytearray(make_elf([(0x08000000, b"abcd")]))
    struct.pack_into("<I", elf, offset, value)

    with pytest.raises(ValueError, match="invalid load segment"):
        ELF32FileReader().decode(elf)


def test_decode_skips_segments_without_load_data() -> None:
    elf = bytearray(make_elf([(0x08000000, b"ignored"), (0x08000010, b""), (0x08000020, b"abcd")]))
    struct.pack_into("<I", elf, 52, 2)

    assert ELF32FileReader().decode(elf) == (0x08000020, b"abcd")


@pytest.mark.parametrize("segments", [[], [(0x08000000, b"")]])
def test_decode_rejects_empty_firmware(segments) -> None:
    with pytest.raises(ValueError, match="no load segments"):
        ELF32FileReader().decode(make_elf(segments))


@pytest.mark.parametrize(
    "segments", [[(0x08000001, b"abcd")], [(0x08000000, b"abcd"), (0x09000000, b"abcd")]]
)
def test_decode_rejects_unsupported_address_or_size(segments) -> None:
    with pytest.raises(ValueError, match="address or image size"):
        ELF32FileReader().decode(make_elf(segments))


def test_decode_rejects_overlapping_segments() -> None:
    with pytest.raises(ValueError, match="overlap"):
        ELF32FileReader().decode(make_elf([(0x08000000, b"abcd"), (0x08000002, b"efgh")]))


def test_read_preserves_file_error(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        ELF32FileReader().read(str(tmp_path / "missing.elf"))
