/* score.c -- "Respect" scoring (round 28, SC). Display only.
 *
 * How the game scores (see docs/score.md). The judge object (scene machine at
 * 0xC8B30 -> scene -> +0xC) keeps, per song:
 *   judge+0x814  u32  sum of the judgment weights   MAX 100% = 100, 90% = 90 ... 10% = 10,
 *                                                   index 2 = 1, BREAK/MISS = 0
 *   judge+0x810  u16  number of weighted judgments (notes of the chart that count)
 *   judge+0x808  u32  the game's own score (+0x80C bonus), never touched here
 * The result screen's JUD. % is 10 * sum / count in tenths. Respect is the same
 * ratio scaled to a million: round(1000000 * sum / (100 * count)), exact in
 * 64-bit integers (100 % -> 1000000, 0 -> 0), never through the rounded percent.
 *
 * Nothing the game stores changes: the Respect value only replaces the number
 * the game hands to its number-drawing routines (--wrap, like the Fever mode).
 *   - result screen: the SCORE field, call 0x43C74 -> 0x42064 (a1 = score)
 *   - song select: the HIGH SCORE field, the value getScore returns to 0x319F0 (the
 *     draw call 0x31B30 -> 0x322E4 is in the same generated unit, --wrap cannot see it)
 * The game's record (score, combo, judgment, rank) stays in the save, by the
 * game's rules, in both modes. The Respect record of every cleared stage goes
 * to <data>/es_scores.json, also in both modes, at the moment the result
 * screen compares the stage with the stored record (0x43F70).
 *
 * There is no score on the in-song HUD in any mode (0x1A600, the HUD number
 * routine, is never called in 4T / 6.2T), so only these two places exist. */

#include <psprecomp/cpu.h>
#include "psprecomp/log.h"
#include <psprecomp/dispatch.h>
#include <psprecomp/hle.h>
#include <psprecomp/mem.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <io.h>
#endif

int djmax_score_respect = 1;      /* host.c: EMOTIONALSENSE_SCORE / --score */

/* ---- the numbers, captured while the song plays ----------------------------- */

static uint64_t g_sum;            /* judge+0x814 as last seen in the gameplay scene */
static uint32_t g_cnt;            /* judge+0x810 */

uint64_t djmax_respect_value(uint64_t sum, uint32_t count)
{
    if (!count) return 0;
    return (sum * 10000u + count / 2u) / count;       /* 1000000 * sum / (100 * count), rounded */
}

/* host.c, every vblank of the gameplay scene. The values only grow during a
 * song and the result screen comes seconds after the last judgment, so the
 * last sample is the final one. */
void djmax_score_sample(uint32_t sum, uint32_t count)
{
    /* The judge is torn down a moment before the scene changes, and its memory
     * reads as garbage then. A real sample is a new song (all zero) or grows by
     * a few notes at most between two vblanks and never exceeds 100 per note. */
    static uint32_t last_s = ~0u, last_c = ~0u;
    if ((sum != last_s || count != last_c) && getenv("EMOTIONALSENSE_TRACE_SCORE")) {
        psp_log("score: sample sum=%u count=%u (kept %llu/%u)\n", sum, count, (unsigned long long)g_sum, g_cnt);
        last_s = sum; last_c = count;
    }
    if (!sum && !count) { g_sum = 0; g_cnt = 0; return; }
    if (count < g_cnt || sum < g_sum || count - g_cnt > 24 || sum > 100u * count || sum - g_sum > 2400u)
        return;
    g_sum = sum;
    g_cnt = count;
}

/* ---- es_scores.json -------------------------------------------------------- */

#define MAXREC 4096
typedef struct { uint32_t style, song, diff, score; } rec_t;
static rec_t g_rec[MAXREC];
static int g_nrec;
static char g_path[1300];
static int g_loaded;

void djmax_score_set_dir(const char *dir)
{
    snprintf(g_path, sizeof g_path, "%s/es_scores.json", dir);
    g_loaded = 0;
}

static void load_records(void)
{
    if (g_loaded || !g_path[0]) return;
    g_loaded = 1;
    g_nrec = 0;
    FILE *f = fopen(g_path, "rb");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof line, f) && g_nrec < MAXREC) {
        unsigned st, so, di, sc;
        const char *q = strchr(line, '"');
        if (q && sscanf(q, "\"%u:%u:%u\": %u", &st, &so, &di, &sc) == 4)
            g_rec[g_nrec++] = (rec_t){st, so, di, sc};
    }
    fclose(f);
}

static void save_records(void)
{
    char tmp[1320];
    snprintf(tmp, sizeof tmp, "%s.tmp", g_path);
    FILE *f = fopen(tmp, "wb");
    if (!f) { psp_log("score: cannot write %s\n", tmp); return; }
    fprintf(f, "{\n \"version\": 1,\n \"note\": \"Respect scores by style:song:difficulty\",\n \"scores\": {\n");
    for (int i = 0; i < g_nrec; i++)
        fprintf(f, "  \"%u:%u:%u\": %u%s\n", g_rec[i].style, g_rec[i].song, g_rec[i].diff,
                g_rec[i].score, i + 1 < g_nrec ? "," : "");
    fprintf(f, " }\n}\n");
    int bad = fflush(f) != 0;
    bad |= fclose(f) != 0;
    if (bad) { remove(tmp); return; }
#ifdef _WIN32
    remove(g_path);          /* rename() does not replace on Windows */
#endif
    if (rename(tmp, g_path) != 0) { psp_log("score: cannot replace %s\n", g_path); remove(tmp); }
}

static rec_t *find_record(uint32_t st, uint32_t so, uint32_t di)
{
    for (int i = 0; i < g_nrec; i++)
        if (g_rec[i].style == st && g_rec[i].song == so && g_rec[i].diff == di) return &g_rec[i];
    return NULL;
}

/* A cleared stage: keep the best Respect score for the key. */
static void record_update(uint32_t st, uint32_t so, uint32_t di, uint32_t score)
{
    if (!g_path[0]) return;
    load_records();
    rec_t *r = find_record(st, so, di);
    if (r) {
        if (score <= r->score) return;
        r->score = score;
    } else {
        if (g_nrec >= MAXREC) return;
        g_rec[g_nrec++] = (rec_t){st, so, di, score};
    }
    save_records();
}

static uint32_t record_get(uint32_t st, uint32_t so, uint32_t di)
{
    load_records();
    rec_t *r = find_record(st, so, di);
    return r ? r->score : 0;
}

/* ---- hooks ----------------------------------------------------------------- */

static int trace_on(void)
{
    static int tr = -1;
    if (tr < 0) tr = getenv("EMOTIONALSENSE_TRACE_NUM") != NULL;
    return tr;
}

static uint32_t g_sel_style, g_sel_song, g_sel_diff;   /* the chart the select screen is showing */
static int g_sel_valid;

/* 0x552C4 getScore(db, style, song, difficulty): the game's stored record. */
void __real_psp_func_000552C4(void);

void __wrap_psp_func_000552C4(void)
{
    uint32_t ra = psp_cpu.r[PSP_REG_RA];
    uint32_t st = psp_cpu.r[PSP_REG_A1] & 0xFF, so = psp_cpu.r[PSP_REG_A2], di = psp_cpu.r[PSP_REG_A3];
    if (trace_on())
        psp_log("score: getScore vb=%llu ra=%08X style=%u song=%u diff=%u\n",
                (unsigned long long)psp_display_vblanks(), ra, st, so, di);
    if (ra == 0x000319F8u) {                 /* song select panel */
        g_sel_style = st; g_sel_song = so; g_sel_diff = di; g_sel_valid = 1;
    } else if (ra == 0x00043F78u && g_cnt) { /* result screen, stage cleared */
        uint32_t v = (uint32_t)djmax_respect_value(g_sum, g_cnt);
        psp_log("score: result style=%u song=%u diff=%u respect=%u (weights %llu / %u notes, game JUD. %llu.%llu%%)\n",
                st, so, di, v, (unsigned long long)g_sum, g_cnt,
                (unsigned long long)(g_sum * 10 / g_cnt / 10), (unsigned long long)(g_sum * 10 / g_cnt % 10));
        record_update(st, so, di, v);
    }
    __real_psp_func_000552C4();
    /* The song panel only draws what getScore returns (HIGH SCORE), so the
     * Respect record replaces the value here; the game's record is untouched. */
    if (djmax_score_respect && ra == 0x000319F8u)
        psp_cpu.r[PSP_REG_V0] = record_get(st, so, di);
}

/* 0x42064 setNumber(obj, value, digits, slot, ...): the result screen's numbers. */
void __real_psp_func_00042064(void);

void __wrap_psp_func_00042064(void)
{
    uint32_t ra = psp_cpu.r[PSP_REG_RA];
    if (trace_on())
        psp_log("score: num42064 vb=%llu ra=%08X a1=%u a2=%u a3=%u\n",
                (unsigned long long)psp_display_vblanks(), ra,
                psp_cpu.r[PSP_REG_A1], psp_cpu.r[PSP_REG_A2], psp_cpu.r[PSP_REG_A3]);
    if (djmax_score_respect && ra == 0x00043C7Cu && g_cnt) {
        uint32_t v = (uint32_t)djmax_respect_value(g_sum, g_cnt);
        psp_log("score: result shows Respect %u (the game's own: %u)\n", v, psp_cpu.r[PSP_REG_A1]);
        psp_cpu.r[PSP_REG_A1] = v;
    }
    __real_psp_func_00042064();
}
