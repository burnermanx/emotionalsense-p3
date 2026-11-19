/* The 1.02 update is a DATA.TPA with 6 entries that replace the disc's entries
 * of the same name hash (see docs/update-1.02.md). The top-level container is
 * plain: a 12-byte header (magic 0x0004C0DA, entry count in the high 16 bits of
 * the second word, total size minus 12), then 16-byte entries (hash, size,
 * offset, 0), then the data back to back. The overlay is that same layout with
 * the replaced entries' size and offset changed, written once to
 * <data>/update/DATA.combined.TPA; the entries' contents are not interpreted. */
#include "update_overlay.h"
#include "psprecomp/log.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

typedef struct { uint32_t hash, size, off, zero; } ent;
typedef struct { FILE *f; uint32_t count; uint64_t size; ent *e; } tpa;

static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static void wr32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

static int tpa_open(tpa *t, const char *path) {
    memset(t, 0, sizeof *t);
    t->f = fopen(path, "rb");
    if (!t->f) return -1;
    fseek(t->f, 0, SEEK_END);
    t->size = (uint64_t)ftell(t->f);
    fseek(t->f, 0, SEEK_SET);
    uint8_t h[12];
    if (fread(h, 1, 12, t->f) != 12) return -1;
    if (rd32(h) != 0x0004C0DAu) return -1;
    t->count = rd32(h + 4) >> 16;
    if (!t->count || t->count > 4096 || rd32(h + 8) + 12ull != t->size) return -1;
    t->e = (ent *)calloc(t->count, sizeof(ent));
    uint8_t *tb = (uint8_t *)malloc((size_t)t->count * 16);
    if (!t->e || !tb || fread(tb, 16, t->count, t->f) != t->count) { free(tb); return -1; }
    for (uint32_t i = 0; i < t->count; i++) {
        t->e[i].hash = rd32(tb + i * 16); t->e[i].size = rd32(tb + i * 16 + 4);
        t->e[i].off = rd32(tb + i * 16 + 8); t->e[i].zero = rd32(tb + i * 16 + 12);
        if ((uint64_t)t->e[i].off + t->e[i].size > t->size) { free(tb); return -1; }
    }
    free(tb);
    return 0;
}

static void tpa_close(tpa *t) { if (t->f) fclose(t->f); free(t->e); memset(t, 0, sizeof *t); }

static int copy_range(FILE *src, uint64_t off, uint32_t n, FILE *dst) {
    static uint8_t buf[1 << 16];
    fseek(src, (long)off, SEEK_SET);
    while (n) {
        size_t k = n < sizeof buf ? n : sizeof buf;
        if (fread(buf, 1, k, src) != k || fwrite(buf, 1, k, dst) != k) return -1;
        n -= (uint32_t)k;
    }
    return 0;
}

int es_update_prepare(const char *data, char *combined, size_t cap) {
    char disc_p[1100], upd_p[1100], tmp_p[1100];
    snprintf(disc_p, sizeof disc_p, "%s/disc/PSP_GAME/INSDIR/DATA.TPA", data);
    snprintf(upd_p, sizeof upd_p, "%s/update/DATA.TPA", data);
    snprintf(combined, cap, "%s/update/DATA.combined.TPA", data);
    snprintf(tmp_p, sizeof tmp_p, "%s.tmp", combined);
    struct stat su, sd, sc;
    if (stat(upd_p, &su) != 0) return 0;
    if (stat(disc_p, &sd) != 0) return 0;

    tpa d, u;
    int rc = -1;
    memset(&d, 0, sizeof d); memset(&u, 0, sizeof u);
    if (tpa_open(&d, disc_p) != 0) { psp_log("update: the disc's DATA.TPA is not what was expected\n"); goto out; }
    if (tpa_open(&u, upd_p) != 0) { psp_log("update: %s is not a DATA.TPA (bad header or sizes); ignored\n", upd_p); goto out; }

    /* Which disc entries the update replaces. */
    int *rep = (int *)malloc(sizeof(int) * d.count);
    if (!rep) goto out;
    uint32_t nrep = 0;
    uint64_t total = 12ull + 16ull * d.count;
    /* EMOTIONALSENSE_UPDATE_MASK=<hex bits by update entry>: a debugging aid that
     * applies only some of the update's entries (the combined file is then always rebuilt). */
    const char *mk = getenv("EMOTIONALSENSE_UPDATE_MASK");
    unsigned long mask = mk && *mk ? strtoul(mk, NULL, 16) : ~0ul;
    for (uint32_t i = 0; i < d.count; i++) {
        rep[i] = -1;
        for (uint32_t j = 0; j < u.count; j++)
            if (u.e[j].hash == d.e[i].hash && (mask >> j & 1)) { rep[i] = (int)j; nrep++; break; }
        total += rep[i] >= 0 ? u.e[rep[i]].size : d.e[i].size;
    }
    if (nrep != u.count && !mk) psp_log("update: %u of %u update entries have no match on the disc and are ignored\n",
                                 u.count - nrep, u.count);
    if (!nrep) { psp_log("update: no entry of %s matches the disc; ignored\n", upd_p); free(rep); goto out; }

    /* Reuse a combined file that is the right size and newer than both inputs. */
    if (!mk && stat(combined, &sc) == 0 && (uint64_t)sc.st_size == total &&
        sc.st_mtime >= su.st_mtime && sc.st_mtime >= sd.st_mtime) { free(rep); rc = 1; goto out; }

    FILE *o = fopen(tmp_p, "wb");
    if (!o) { psp_log("update: cannot write %s\n", tmp_p); free(rep); goto out; }
    uint8_t hdr[12];
    wr32(hdr, 0x0004C0DAu); wr32(hdr + 4, d.count << 16); wr32(hdr + 8, (uint32_t)(total - 12));
    /* keep the low bits of the disc's second word */
    { uint8_t dh[12]; fseek(d.f, 0, SEEK_SET); if (fread(dh, 1, 12, d.f) == 12) wr32(hdr + 4, rd32(dh + 4)); }
    int bad = fwrite(hdr, 1, 12, o) != 12;
    uint64_t off = 12ull + 16ull * d.count;
    for (uint32_t i = 0; i < d.count && !bad; i++) {
        uint8_t e[16];
        uint32_t sz = rep[i] >= 0 ? u.e[rep[i]].size : d.e[i].size;
        wr32(e, d.e[i].hash); wr32(e + 4, sz); wr32(e + 8, (uint32_t)off); wr32(e + 12, d.e[i].zero);
        bad |= fwrite(e, 1, 16, o) != 16;
        off += sz;
    }
    for (uint32_t i = 0; i < d.count && !bad; i++)
        bad |= rep[i] >= 0 ? copy_range(u.f, u.e[rep[i]].off, u.e[rep[i]].size, o)
                           : copy_range(d.f, d.e[i].off, d.e[i].size, o);
    bad |= fclose(o) != 0;
    free(rep);
    if (bad) { remove(tmp_p); psp_log("update: could not build %s\n", combined); goto out; }
    remove(combined);
    if (rename(tmp_p, combined) != 0) { psp_log("update: could not rename %s\n", tmp_p); goto out; }
    psp_out("update: built %s (%u entries replaced)\n", combined, nrep);
    rc = 1;
out:
    tpa_close(&d); tpa_close(&u);
    return rc;
}
