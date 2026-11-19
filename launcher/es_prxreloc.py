"""Apply a PSP PRX's relocations statically, for a load address of 0.

A PRX is linked at 0 and relocated by the loader. psprecomp loads modules
exactly at their link address, so for a single-segment module the relocations
are a no-op and can be ignored -- which is what upstream does. But PSP
relocations are *segment-relative*: each entry names the segment its offset is
in (ofs_base) and the segment whose load address it adds (addr_base). DJMAX
Portable 3 has two PT_LOADs (.text/.rodata at 0, .data/.bss at 0xCB540), so a
reference into .data is stored as an offset from 0 and only becomes correct
once 0xCB540 is added. Unrelocated, `lui $s0, 0; addiu $s0, $s0, 0` that
should point at a function table in .data points at code instead, and the
game calls an instruction word (0x8FBF0000) as a function.

The recompiler bakes immediates into C, so this must happen *before*
allegrexrecomp sees the module: we rewrite the ELF with every relocation
applied (segment n placed at its own p_vaddr) and keep the relocation tables,
which allegrexrecomp still reads for function-pointer seeds.

Handles the classic SHT_PRXRELOC / PT_PRXRELOC (0x700000A0) format. The
compressed 0x700000A1 format is rejected loudly rather than mis-applied.

    python3 es_prxreloc.py in.elf out.elf
"""

from __future__ import annotations

import struct
import sys

PT_LOAD = 1
PRXRELOC = 0x700000A0
PRXRELOC2 = 0x700000A1

R_MIPS_NONE, R_MIPS_16, R_MIPS_32, R_MIPS_REL32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16 = range(7)


class RelocError(RuntimeError):
    pass


def relocate(data: bytes) -> tuple[bytes, dict]:
    b = bytearray(data)
    if b[:4] != b"\x7fELF":
        raise RelocError("not an ELF")
    phoff = struct.unpack_from("<I", b, 28)[0]
    phentsize, phnum = struct.unpack_from("<HH", b, 42)
    shoff = struct.unpack_from("<I", b, 32)[0]
    shentsize, shnum = struct.unpack_from("<HH", b, 46)

    segs = []        # (offset, vaddr, filesz, memsz) for every PT_LOAD, in order
    rel_blocks = []  # (offset, size)
    for i in range(phnum):
        p_type, p_off, p_vaddr, _pa, p_filesz, p_memsz = struct.unpack_from("<6I", b, phoff + i * phentsize)
        if p_type == PT_LOAD:
            segs.append((p_off, p_vaddr, p_filesz, p_memsz))
        elif p_type == PRXRELOC:
            rel_blocks.append((p_off, p_filesz))
        elif p_type == PRXRELOC2:
            raise RelocError("compressed PRX relocations (0x700000A1) are not supported yet")
    if not rel_blocks and shoff:
        for i in range(shnum):
            sh = shoff + i * shentsize
            if sh + 40 > len(b):
                break
            s_type = struct.unpack_from("<I", b, sh + 4)[0]
            s_off, s_size = struct.unpack_from("<II", b, sh + 16)
            if s_type == PRXRELOC:
                rel_blocks.append((s_off, s_size))
            elif s_type == PRXRELOC2:
                raise RelocError("compressed PRX relocations (0x700000A1) are not supported yet")
    if not segs:
        raise RelocError("no PT_LOAD segments")

    def file_off(vaddr: int) -> int:
        for off, va, filesz, _ in segs:
            if va <= vaddr < va + filesz:
                return off + (vaddr - va)
        raise RelocError(f"relocation target 0x{vaddr:08X} is outside the file-backed image")

    def rd(fo):
        return struct.unpack_from("<I", b, fo)[0]

    def wr(fo, v):
        struct.pack_into("<I", b, fo, v & 0xFFFFFFFF)

    rels = []
    for off, size in rel_blocks:
        for r in range(0, size - size % 8, 8):
            rels.append(struct.unpack_from("<II", b, off + r))

    stats = {"total": len(rels), "applied": 0, "noop": 0, "by_type": {}}
    n = len(rels)
    i = 0
    while i < n:
        r_offset, r_info = rels[i]
        rtype = r_info & 0xFF
        ofs_base = (r_info >> 8) & 0xFF
        addr_base = (r_info >> 16) & 0xFF
        if ofs_base >= len(segs) or addr_base >= len(segs):
            raise RelocError(f"relocation {i} names segment {max(ofs_base, addr_base)}, "
                             f"module has {len(segs)}")
        to = segs[addr_base][1]                  # where that segment is "loaded"
        fo = file_off(segs[ofs_base][1] + r_offset)
        stats["by_type"][rtype] = stats["by_type"].get(rtype, 0) + 1
        if to == 0:
            stats["noop"] += 1
            i += 1
            continue
        op = rd(fo)
        if rtype == R_MIPS_NONE:
            pass
        elif rtype == R_MIPS_32:
            wr(fo, op + to)
        elif rtype == R_MIPS_26:
            tgt = ((op & 0x03FFFFFF) << 2) + to
            wr(fo, (op & 0xFC000000) | ((tgt >> 2) & 0x03FFFFFF))
        elif rtype == R_MIPS_HI16:
            # The full address is hi<<16 plus the *signed* low half of the
            # LO16 that follows; carry from the low half moves the high half.
            lo = None
            for j in range(i + 1, n):
                jo, ji = rels[j]
                if ji & 0xFF == R_MIPS_LO16:
                    lo = rd(file_off(segs[(ji >> 8) & 0xFF][1] + jo))
                    break
            lo16 = 0 if lo is None else (lo & 0xFFFF)
            lo16 = lo16 - 0x10000 if lo16 & 0x8000 else lo16
            full = ((op & 0xFFFF) << 16) + lo16 + to
            hi = ((full >> 16) + (1 if full & 0x8000 else 0)) & 0xFFFF
            wr(fo, (op & 0xFFFF0000) | hi)
        elif rtype == R_MIPS_LO16:
            wr(fo, (op & 0xFFFF0000) | ((op + to) & 0xFFFF))
        elif rtype == R_MIPS_16:
            wr(fo, (op & 0xFFFF0000) | ((op + to) & 0xFFFF))
        else:
            raise RelocError(f"unsupported relocation type {rtype} at 0x{r_offset:08X}")
        stats["applied"] += 1
        i += 1
    return bytes(b), stats


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    with open(argv[1], "rb") as f:
        data = f.read()
    out, st = relocate(data)
    with open(argv[2], "wb") as f:
        f.write(out)
    print(f"{st['total']} relocations, {st['applied']} applied, {st['noop']} no-op (segment at 0); "
          f"by type {st['by_type']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
