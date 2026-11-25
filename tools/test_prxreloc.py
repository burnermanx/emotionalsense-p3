#!/usr/bin/env python3
"""Synthetic equivalence test: tools/es_prxreloc.c vs launcher/es_prxreloc.py."""

import importlib.util
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("es_prxreloc", ROOT / "launcher" / "es_prxreloc.py")
py_reloc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(py_reloc)

PT_LOAD = 1
PRXRELOC = 0x700000A0
PRXRELOC2 = 0x700000A1

R_MIPS_NONE, R_MIPS_16, R_MIPS_32, _R_MIPS_REL32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16 = range(7)


def make_synthetic_prx(use_section_header: bool = False, reloc_type_override: int | None = None) -> bytes:
    """Build a minimal synthetic 2-segment ELF32 PRX with every relocation type."""
    ehdr_size = 52
    ph_size = 32
    sh_size = 40
    phnum = 2 if use_section_header else 3
    shnum = 2 if use_section_header else 0

    seg0_off = 0x100
    seg0_va = 0x00000000
    seg0_words = [
        0x00000000,  # +0x00: R_MIPS_NONE
        0x24080010,  # +0x04: addiu $t0, $zero, 0x0010 (R_MIPS_16)
        0x00001000,  # +0x08: R_MIPS_32 -> 0x00001000 + seg1_va
        0x0C000100,  # +0x0C: jal 0x00000400 (R_MIPS_26)
        0x3C100001,  # +0x10: lui $s0, 0x0001 (R_MIPS_HI16, paired with negative lo16 below)
        0x2610FFF0,  # +0x14: addiu $s0, $s0, -16 (R_MIPS_LO16 -> 0xFFFFFEF0 + seg1_va)
        0x3C110002,  # +0x18: lui $s1, 0x0002 (R_MIPS_HI16, paired with positive lo16)
        0x26310020,  # +0x1C: addiu $s1, $s1, 0x0020 (R_MIPS_LO16)
        0x00000040,  # +0x20: R_MIPS_32 noop (addr_base = 0 -> seg0_va = 0)
    ]
    seg0_bytes = struct.pack(f"<{len(seg0_words)}I", *seg0_words)

    seg1_off = 0x200
    seg1_va = 0x000CB540
    seg1_words = [
        0x00000004,  # +0x00: R_MIPS_32 in seg1 pointing to seg1
        0x24098000,  # +0x04: R_MIPS_LO16 in seg1
    ]
    seg1_bytes = struct.pack(f"<{len(seg1_words)}I", *seg1_words)

    def r_info(rtype: int, ofs_base: int, addr_base: int) -> int:
        return (rtype & 0xFF) | ((ofs_base & 0xFF) << 8) | ((addr_base & 0xFF) << 16)

    rels = [
        (0x00, r_info(R_MIPS_NONE, 0, 1)),
        (0x04, r_info(R_MIPS_16, 0, 1)),
        (0x08, r_info(R_MIPS_32, 0, 1)),
        (0x0C, r_info(R_MIPS_26, 0, 1)),
        (0x10, r_info(R_MIPS_HI16, 0, 1)),
        (0x14, r_info(R_MIPS_LO16, 0, 1)),
        (0x18, r_info(R_MIPS_HI16, 0, 1)),
        (0x1C, r_info(R_MIPS_LO16, 0, 1)),
        (0x20, r_info(R_MIPS_32, 0, 0)),  # noop
        (0x00, r_info(R_MIPS_32, 1, 1)),
        (0x04, r_info(R_MIPS_LO16, 1, 1)),
    ]
    rel_off = 0x300
    rel_bytes = b"".join(struct.pack("<II", ro, ri) for ro, ri in rels)

    sh_off = 0x400 if use_section_header else 0
    total_size = (sh_off + shnum * sh_size) if use_section_header else (rel_off + len(rel_bytes))
    buf = bytearray(total_size)

    phoff = ehdr_size
    # ELF32 header (little-endian MIPS)
    e_ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
    struct.pack_into(
        "<16sHHIIIIIHHHHHH",
        buf,
        0,
        e_ident,
        0xFFA0,     # ET_PSPEXEC
        8,          # EM_MIPS
        1,          # EV_CURRENT
        0,          # e_entry
        phoff,
        sh_off,
        0,          # e_flags
        ehdr_size,
        ph_size,
        phnum,
        sh_size,
        shnum,
        0,
    )

    # PT_LOAD 0
    struct.pack_into("<8I", buf, phoff + 0 * ph_size,
                     PT_LOAD, seg0_off, seg0_va, seg0_va, len(seg0_bytes), len(seg0_bytes), 5, 0x10)
    # PT_LOAD 1
    struct.pack_into("<8I", buf, phoff + 1 * ph_size,
                     PT_LOAD, seg1_off, seg1_va, seg1_va, len(seg1_bytes), len(seg1_bytes), 6, 0x10)

    rel_hdr_type = reloc_type_override if reloc_type_override is not None else PRXRELOC
    if not use_section_header:
        struct.pack_into("<8I", buf, phoff + 2 * ph_size,
                         rel_hdr_type, rel_off, 0, 0, len(rel_bytes), 0, 0, 4)
    else:
        # SHT_NULL + SHT_PRXRELOC
        struct.pack_into("<10I", buf, sh_off + 1 * sh_size,
                         0, rel_hdr_type, 0, 0, rel_off, len(rel_bytes), 0, 0, 4, 8)

    buf[seg0_off:seg0_off + len(seg0_bytes)] = seg0_bytes
    buf[seg1_off:seg1_off + len(seg1_bytes)] = seg1_bytes
    buf[rel_off:rel_off + len(rel_bytes)] = rel_bytes
    return bytes(buf)


def run_case(c_bin: str, data: bytes, label: str) -> None:
    expected, _ = py_reloc.relocate(data)
    with tempfile.TemporaryDirectory() as td:
        in_path = Path(td) / "in.elf"
        out_path = Path(td) / "out.elf"
        in_path.write_bytes(data)
        res = subprocess.run([c_bin, str(in_path), str(out_path)], capture_output=True, text=True)
        if res.returncode != 0:
            raise AssertionError(f"{label}: es_prxreloc failed (rc={res.returncode}): {res.stderr}")
        actual = out_path.read_bytes()
        if actual != expected:
            for idx, (a, b) in enumerate(zip(actual, expected)):
                if a != b:
                    raise AssertionError(f"{label}: byte mismatch at 0x{idx:X}: got 0x{a:02X}, expected 0x{b:02X}")
            raise AssertionError(f"{label}: length mismatch {len(actual)} != {len(expected)}")
        if actual == data:
            raise AssertionError(f"{label}: output is unexpectedly identical to input (no relocations applied)")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: test_prxreloc.py <path-to-es_prxreloc>", file=sys.stderr)
        return 2
    c_bin = sys.argv[1]

    run_case(c_bin, make_synthetic_prx(use_section_header=False), "PT_PRXRELOC")
    run_case(c_bin, make_synthetic_prx(use_section_header=True), "SHT_PRXRELOC")

    # Verify PRXRELOC2 is rejected cleanly
    bad = make_synthetic_prx(use_section_header=False, reloc_type_override=PRXRELOC2)
    with tempfile.TemporaryDirectory() as td:
        in_path = Path(td) / "bad.elf"
        out_path = Path(td) / "bad_out.elf"
        in_path.write_bytes(bad)
        res = subprocess.run([c_bin, str(in_path), str(out_path)], capture_output=True, text=True)
        if res.returncode == 0:
            raise AssertionError("PRXRELOC2 should be rejected with non-zero exit status")

    print("es_prxreloc synthetic equivalence tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
