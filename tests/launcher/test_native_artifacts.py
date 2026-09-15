from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
ROOT = Path(__file__).parents[2]
BUILD = ROOT / "build" / "launcher"


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _pe_metadata(path: Path) -> tuple[int, int, set[str]]:
    data = path.read_bytes()
    pe = _u32(data, 0x3C)
    assert data[pe : pe + 4] == b"PE\0\0"
    machine = _u16(data, pe + 4)
    section_count = _u16(data, pe + 6)
    optional_size = _u16(data, pe + 20)
    optional = pe + 24
    assert _u16(data, optional) == 0x20B, "expected a PE32+ executable"
    subsystem = _u16(data, optional + 68)
    import_rva = _u32(data, optional + 112 + 8)
    sections_offset = optional + optional_size
    sections: list[tuple[int, int, int]] = []
    for index in range(section_count):
        section = sections_offset + index * 40
        virtual_size = _u32(data, section + 8)
        virtual_address = _u32(data, section + 12)
        raw_size = _u32(data, section + 16)
        raw_offset = _u32(data, section + 20)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset))

    def file_offset(rva: int) -> int:
        for virtual_address, size, raw_offset in sections:
            if virtual_address <= rva < virtual_address + size:
                return raw_offset + rva - virtual_address
        raise AssertionError(f"RVA 0x{rva:x} is outside all PE sections")

    imports: set[str] = set()
    descriptor = file_offset(import_rva)
    while any(data[descriptor : descriptor + 20]):
        name_offset = file_offset(_u32(data, descriptor + 12))
        name_end = data.index(b"\0", name_offset)
        imports.add(data[name_offset:name_end].decode("ascii").lower())
        descriptor += 20
    return machine, subsystem, imports


@pytest.mark.parametrize("name", ["MovieMakerLauncher.exe", "MovieMakerSetup.exe"])
def test_native_gui_has_no_external_language_runtime_dependency(name: str) -> None:
    path = BUILD / name
    if not path.is_file():
        pytest.skip("Build launcher artifacts first")

    machine, subsystem, imports = _pe_metadata(path)

    assert machine == 0x8664
    assert subsystem == 2, "the artifact must be a Windows GUI executable"
    forbidden = {
        "coreclr.dll",
        "hostfxr.dll",
        "mscoree.dll",
        "msvcp140.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "libgcc_s_seh-1.dll",
        "libstdc++-6.dll",
        "libwinpthread-1.dll",
    }
    assert imports.isdisjoint(forbidden), f"unexpected external runtime imports: {imports & forbidden}"
    assert imports
    assert all(value.endswith(".dll") for value in imports)
