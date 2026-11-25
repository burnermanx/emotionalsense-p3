/* Apply a PSP PRX's relocations statically, for a load address of 0.
 *
 * Standalone C11 port of launcher/es_prxreloc.py (no external dependencies).
 *
 *   es_prxreloc <in.elf> <out.elf>
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PT_LOAD   1u
#define PRXRELOC  0x700000A0u
#define PRXRELOC2 0x700000A1u

enum {
    R_MIPS_NONE  = 0,
    R_MIPS_16    = 1,
    R_MIPS_32    = 2,
    R_MIPS_REL32 = 3,
    R_MIPS_26    = 4,
    R_MIPS_HI16  = 5,
    R_MIPS_LO16  = 6,
};

typedef struct {
    uint32_t offset;
    uint32_t vaddr;
    uint32_t filesz;
    uint32_t memsz;
} seg_t;

typedef struct {
    uint32_t offset;
    uint32_t size;
} block_t;

typedef struct {
    uint32_t r_offset;
    uint32_t r_info;
} rel_t;

static uint16_t rd16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t rd32(const uint8_t *p) {
    return (uint32_t)p[0]
         | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

static void wr32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

static int file_off(const seg_t *segs, size_t nsegs, uint32_t vaddr, size_t len, size_t *out_fo) {
    for (size_t i = 0; i < nsegs; i++) {
        uint64_t va = segs[i].vaddr;
        uint64_t end = va + segs[i].filesz;
        if ((uint64_t)vaddr >= va && (uint64_t)vaddr < end) {
            uint64_t fo = (uint64_t)segs[i].offset + ((uint64_t)vaddr - va);
            if (fo + 4 > len) {
                fprintf(stderr, "es_prxreloc: target 0x%08X (offset %llu) out of file bounds\n",
                        vaddr, (unsigned long long)fo);
                return -1;
            }
            *out_fo = (size_t)fo;
            return 0;
        }
    }
    fprintf(stderr, "es_prxreloc: relocation target 0x%08X is outside the file-backed image\n", vaddr);
    return -1;
}

static int read_file(const char *path, uint8_t **out_buf, size_t *out_len) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        perror(path);
        return -1;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        perror(path);
        fclose(f);
        return -1;
    }
    long sz = ftell(f);
    if (sz < 0) {
        perror(path);
        fclose(f);
        return -1;
    }
    if (fseek(f, 0, SEEK_SET) != 0) {
        perror(path);
        fclose(f);
        return -1;
    }
    size_t len = (size_t)sz;
    uint8_t *buf = (uint8_t *)malloc(len ? len : 1);
    if (!buf) {
        fprintf(stderr, "es_prxreloc: out of memory\n");
        fclose(f);
        return -1;
    }
    if (len && fread(buf, 1, len, f) != len) {
        fprintf(stderr, "es_prxreloc: failed to read %s\n", path);
        free(buf);
        fclose(f);
        return -1;
    }
    fclose(f);
    *out_buf = buf;
    *out_len = len;
    return 0;
}

static int write_file(const char *path, const uint8_t *buf, size_t len) {
    FILE *f = fopen(path, "wb");
    if (!f) {
        perror(path);
        return -1;
    }
    if (len && fwrite(buf, 1, len, f) != len) {
        fprintf(stderr, "es_prxreloc: failed to write %s\n", path);
        fclose(f);
        return -1;
    }
    if (fclose(f) != 0) {
        perror(path);
        return -1;
    }
    return 0;
}

static int relocate_buf(uint8_t *b, size_t len) {
    if (len < 52 || memcmp(b, "\x7f" "ELF", 4) != 0) {
        fprintf(stderr, "es_prxreloc: not an ELF\n");
        return -1;
    }

    uint32_t phoff = rd32(b + 28);
    uint16_t phentsize = rd16(b + 42);
    uint16_t phnum = rd16(b + 44);
    uint32_t shoff = rd32(b + 32);
    uint16_t shentsize = rd16(b + 46);
    uint16_t shnum = rd16(b + 48);

    seg_t *segs = NULL;
    size_t nsegs = 0, seg_cap = 0;
    block_t *blocks = NULL;
    size_t nblocks = 0, block_cap = 0;
    rel_t *rels = NULL;
    size_t nrels = 0, rel_cap = 0;
    int rc = -1;

    for (uint16_t i = 0; i < phnum; i++) {
        uint64_t ph = (uint64_t)phoff + (uint64_t)i * phentsize;
        if (phentsize < 24 || ph + 24 > len) {
            fprintf(stderr, "es_prxreloc: truncated program header\n");
            goto out;
        }
        uint32_t p_type   = rd32(b + ph + 0);
        uint32_t p_off    = rd32(b + ph + 4);
        uint32_t p_vaddr  = rd32(b + ph + 8);
        uint32_t p_filesz = rd32(b + ph + 16);
        uint32_t p_memsz  = rd32(b + ph + 20);
        if (p_type == PT_LOAD) {
            if (nsegs == seg_cap) {
                size_t nc = seg_cap ? seg_cap * 2 : 4;
                seg_t *ns = (seg_t *)realloc(segs, nc * sizeof *ns);
                if (!ns) { fprintf(stderr, "es_prxreloc: out of memory\n"); goto out; }
                segs = ns; seg_cap = nc;
            }
            segs[nsegs++] = (seg_t){ p_off, p_vaddr, p_filesz, p_memsz };
        } else if (p_type == PRXRELOC) {
            if (nblocks == block_cap) {
                size_t nc = block_cap ? block_cap * 2 : 4;
                block_t *nb = (block_t *)realloc(blocks, nc * sizeof *nb);
                if (!nb) { fprintf(stderr, "es_prxreloc: out of memory\n"); goto out; }
                blocks = nb; block_cap = nc;
            }
            blocks[nblocks++] = (block_t){ p_off, p_filesz };
        } else if (p_type == PRXRELOC2) {
            fprintf(stderr, "es_prxreloc: compressed PRX relocations (0x700000A1) are not supported yet\n");
            goto out;
        }
    }

    if (nblocks == 0 && shoff != 0) {
        for (uint16_t i = 0; i < shnum; i++) {
            uint64_t sh = (uint64_t)shoff + (uint64_t)i * shentsize;
            if (shentsize < 40 || sh + 40 > len) break;
            uint32_t s_type = rd32(b + sh + 4);
            uint32_t s_off  = rd32(b + sh + 16);
            uint32_t s_size = rd32(b + sh + 20);
            if (s_type == PRXRELOC) {
                if (nblocks == block_cap) {
                    size_t nc = block_cap ? block_cap * 2 : 4;
                    block_t *nb = (block_t *)realloc(blocks, nc * sizeof *nb);
                    if (!nb) { fprintf(stderr, "es_prxreloc: out of memory\n"); goto out; }
                    blocks = nb; block_cap = nc;
                }
                blocks[nblocks++] = (block_t){ s_off, s_size };
            } else if (s_type == PRXRELOC2) {
                fprintf(stderr, "es_prxreloc: compressed PRX relocations (0x700000A1) are not supported yet\n");
                goto out;
            }
        }
    }

    if (nsegs == 0) {
        fprintf(stderr, "es_prxreloc: no PT_LOAD segments\n");
        goto out;
    }

    for (size_t k = 0; k < nblocks; k++) {
        uint64_t off = blocks[k].offset;
        uint32_t sz = blocks[k].size;
        uint32_t usable = sz - (sz % 8u);
        if (off + usable > len) {
            fprintf(stderr, "es_prxreloc: relocation block out of file bounds\n");
            goto out;
        }
        for (uint32_t r = 0; r < usable; r += 8u) {
            if (nrels == rel_cap) {
                size_t nc = rel_cap ? rel_cap * 2 : 64;
                rel_t *nr = (rel_t *)realloc(rels, nc * sizeof *nr);
                if (!nr) { fprintf(stderr, "es_prxreloc: out of memory\n"); goto out; }
                rels = nr; rel_cap = nc;
            }
            rels[nrels++] = (rel_t){
                rd32(b + off + r),
                rd32(b + off + r + 4)
            };
        }
    }

    for (size_t i = 0; i < nrels; i++) {
        uint32_t r_offset = rels[i].r_offset;
        uint32_t r_info = rels[i].r_info;
        uint32_t rtype = r_info & 0xFFu;
        uint32_t ofs_base = (r_info >> 8) & 0xFFu;
        uint32_t addr_base = (r_info >> 16) & 0xFFu;
        if (ofs_base >= nsegs || addr_base >= nsegs) {
            uint32_t bad = ofs_base > addr_base ? ofs_base : addr_base;
            fprintf(stderr, "es_prxreloc: relocation %zu names segment %u, module has %zu\n",
                    i, bad, nsegs);
            goto out;
        }
        uint32_t to = segs[addr_base].vaddr;
        size_t fo = 0;
        if (file_off(segs, nsegs, segs[ofs_base].vaddr + r_offset, len, &fo) != 0) goto out;
        if (to == 0) continue;

        uint32_t op = rd32(b + fo);
        switch (rtype) {
        case R_MIPS_NONE:
            break;
        case R_MIPS_32:
            wr32(b + fo, op + to);
            break;
        case R_MIPS_26: {
            uint32_t tgt = ((op & 0x03FFFFFFu) << 2) + to;
            wr32(b + fo, (op & 0xFC000000u) | ((tgt >> 2) & 0x03FFFFFFu));
            break;
        }
        case R_MIPS_HI16: {
            int32_t lo16 = 0;
            for (size_t j = i + 1; j < nrels; j++) {
                uint32_t ji = rels[j].r_info;
                if ((ji & 0xFFu) == R_MIPS_LO16) {
                    uint32_t j_ofs_base = (ji >> 8) & 0xFFu;
                    if (j_ofs_base >= nsegs) {
                        fprintf(stderr, "es_prxreloc: LO16 relocation %zu names segment %u, module has %zu\n",
                                j, j_ofs_base, nsegs);
                        goto out;
                    }
                    size_t lo_fo = 0;
                    if (file_off(segs, nsegs, segs[j_ofs_base].vaddr + rels[j].r_offset, len, &lo_fo) != 0)
                        goto out;
                    uint32_t lo = rd32(b + lo_fo);
                    lo16 = (int32_t)(int16_t)(lo & 0xFFFFu);
                    break;
                }
            }
            uint32_t full = ((op & 0xFFFFu) << 16) + (uint32_t)lo16 + to;
            uint32_t hi = ((full >> 16) + ((full & 0x8000u) ? 1u : 0u)) & 0xFFFFu;
            wr32(b + fo, (op & 0xFFFF0000u) | hi);
            break;
        }
        case R_MIPS_LO16:
        case R_MIPS_16:
            wr32(b + fo, (op & 0xFFFF0000u) | ((op + to) & 0xFFFFu));
            break;
        default:
            fprintf(stderr, "es_prxreloc: unsupported relocation type %u at 0x%08X\n",
                    rtype, r_offset);
            goto out;
        }
    }

    rc = 0;
out:
    free(rels);
    free(blocks);
    free(segs);
    return rc;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <in.elf> <out.elf>\n", argc > 0 ? argv[0] : "es_prxreloc");
        return 2;
    }
    uint8_t *buf = NULL;
    size_t len = 0;
    if (read_file(argv[1], &buf, &len) != 0) return 1;
    int rc = relocate_buf(buf, len);
    if (rc == 0) rc = write_file(argv[2], buf, len);
    free(buf);
    return rc == 0 ? 0 : 1;
}
