/* speed.c -- "Respect" lane speed (round 35, V): Auto Velocity in steps of 0.1.
 *
 * How the game does it (see docs/speed.md). The judge object (scene machine at 0xC8B30 -> scene -> +0xC)
 * owns the lane object (judge+0x55C):
 *   lane+0x1940  target speed (float)   lane+0x1944  current speed (float, eases 0.05 per vblank)
 *   lane+0x1930  ticks per beat (192)   lane+0x12B0  y of the judgment line (199.0), NOT a tempo
 * The chosen speed is an index byte at 0x130E62 (speed = 0.5 * index, 0.5 .. 7.0). 0x1B40C(lane, snap)
 * turns the index into the target (and into the current speed when snap = 1: song start, restart);
 * 0x17E90 / 0x17F04 are one step up / down (the R / L buttons, and the Fever's x4 / x5 steps).
 * The visible span of the lane is (ticks per measure + ticks per beat) / speed ticks, so the distance
 * between two beats is proportional to the speed and a note falls at BPM/60 * K * speed pixels a second:
 * the same speed number is faster on a faster song. The song's tempo is judge+0x20 (float, follows the
 * chart's tempo events); the chart's first tempo is chart+0xC, with chart = *(judge+0).
 *
 * Respect: the speed the player chooses is an exact number of tenths, 1.0 .. 7.0 (g_v, 10 .. 70), and
 * the lane target is  g_v / 10 * 100 / BPM  with BPM the chart's first tempo ("AV 2.0" is the apparent
 * scroll of a 200 BPM song at 1.0 on every song). The game's own tempo scaling stays, so a tempo change
 * inside a song changes the scroll in the same proportion as in the PSP game.
 * The byte at 0x130E62 always holds a valid index (floor of g_v to a multiple of 0.5, at least 1.0), so
 * the save and the Original mode never see anything odd. The label "AV 2.3" is drawn by the host over
 * the frame in the place and for the time of the game's own speed sprite, which has art for .0 and .5
 * only (0x1DF44, not called in Respect). Nothing is read from the game's art.
 *
 * MISSION always plays Original (djmax_mission_active): the wraps pass through, the byte is the game's. */

#include <psprecomp/cpu.h>
#include "psprecomp/log.h"
#include <psprecomp/dispatch.h>
#include <psprecomp/hle.h>
#include <psprecomp/mem.h>

#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "speed_glyphs.h"

#ifdef _WIN32
#include <io.h>
#endif

int djmax_speed_respect = 1;                 /* host.c: EMOTIONALSENSE_SPEED / --speed */
int djmax_mission_active(void);              /* guest_patches.c */
extern int djmax_fever_respect;              /* guest_patches.c */

#define APP_PTR      0x000C8B30u
#define SPEED_BYTE   0x00130E62u
#define MIN_T        10                      /* 1.0 */
#define MAX_T        70                      /* 7.0 */
#define IDX_MIN      2
#define IDX_MAX      14
#define LABEL_HOLD   45                      /* vblanks at full strength, then a fade */
#define LABEL_FADE   15

static int g_v = 25;                         /* the chosen speed, in tenths */
static int g_boost;                          /* tenths the Fever (Original) has added on top */
static int g_inited, g_in_mission;
static uint8_t g_last_byte;
static atomic_int g_label_t;                 /* tenths shown by the label (v + Fever step) */
static atomic_ullong g_label_vb;             /* vblank of the last change + 1 (0 = never) */
static char g_path[1300];
static int g_dirty;
static uint64_t g_dirty_vb;
static struct { uint32_t judge, lane; float bpm; } g_ctx;

static int ram_ptr(uint32_t a) { return a >= 0x08000000u && a < 0x0A000000u; }

static int active(void) { return djmax_speed_respect && !djmax_mission_active(); }

static int idx_of(int t)
{
    int i = t / 5;
    return i < IDX_MIN ? IDX_MIN : i > IDX_MAX ? IDX_MAX : i;
}

static void set_byte(int idx)
{
    psp_write8(SPEED_BYTE, (uint8_t)idx);
    g_last_byte = (uint8_t)idx;
}

/* ---- persistence: <data>/es_speed.json -------------------------------------- */

void djmax_speed_set_dir(const char *dir)
{
    snprintf(g_path, sizeof g_path, "%s/es_speed.json", dir);
}

static int file_load(void)
{
    if (!g_path[0]) return 0;
    FILE *f = fopen(g_path, "rb");
    if (!f) return 0;
    char line[256];
    int t = 0, ok = 0;
    while (fgets(line, sizeof line, f))
        if (sscanf(line, " \"tenths\": %d", &t) == 1) { ok = t >= MIN_T && t <= MAX_T; break; }
    fclose(f);
    if (ok) g_v = t;
    return ok;
}

static void file_save(void)
{
    if (!g_path[0]) return;
    char tmp[1320];
    snprintf(tmp, sizeof tmp, "%s.tmp", g_path);
    FILE *f = fopen(tmp, "wb");
    if (!f) { psp_log("speed: cannot write %s\n", tmp); return; }
    fprintf(f, "{\n \"version\": 1,\n \"note\": \"Respect speed in tenths (23 = 2.3)\",\n \"tenths\": %d\n}\n", g_v);
    int bad = fflush(f) != 0;
    bad |= fclose(f) != 0;
    if (bad) { remove(tmp); return; }
#ifdef _WIN32
    remove(g_path);          /* rename() does not replace on Windows */
#endif
    if (rename(tmp, g_path) != 0) { psp_log("speed: cannot replace %s\n", g_path); remove(tmp); }
}

static void changed(uint64_t vb)
{
    g_dirty = 1;
    g_dirty_vb = vb;
}

/* ---- the objects ----------------------------------------------------------- */

/* The judge that owns `lane`, found through the scene machine; 0 unless judge+0x55C == lane. */
static uint32_t judge_of(uint32_t lane)
{
    uint32_t app = psp_read32(APP_PTR);
    if (!ram_ptr(app)) return 0;
    uint32_t scene = psp_read32(app + 0);
    if (!ram_ptr(scene)) return 0;
    uint32_t judge = psp_read32(scene + 0xC);
    if (!ram_ptr(judge) || !psp_mem_ptr(judge + 0x560, 4)) return 0;
    return psp_read32(judge + 0x55C) == lane ? judge : 0;
}

/* The chart's first tempo: *(judge+0) is the chart header, +0xC the float the sound player starts at. */
static float chart_bpm(uint32_t judge)
{
    uint32_t chart = psp_read32(judge + 0);
    if (!ram_ptr(chart) || !psp_mem_ptr(chart + 0x14, 4)) return 0.f;
    uint32_t bits = psp_read32(chart + 0xC);
    float f;
    memcpy(&f, &bits, 4);
    return f >= 30.f && f <= 1000.f ? f : 0.f;
}

static float load_f(uint32_t a)
{
    uint32_t b = psp_read32(a);
    float f;
    memcpy(&f, &b, 4);
    return f;
}

static void store_f(uint32_t a, float f)
{
    uint32_t b;
    memcpy(&b, &f, 4);
    psp_write32(a, b);
}

/* Context of the song being played: judge, lane and the chart's base tempo. `judge_hint` is what the
 * caller knows (the song-start call sites hold the judge in $s0); the scene machine is the fallback. */
static int context_for(uint32_t lane, uint32_t judge_hint)
{
    if (g_ctx.lane == lane && g_ctx.judge && g_ctx.bpm > 0.f && psp_read32(g_ctx.judge + 0x55C) == lane)
        return 1;
    uint32_t judge = 0;
    if (ram_ptr(judge_hint) && psp_mem_ptr(judge_hint + 0x560, 4) && psp_read32(judge_hint + 0x55C) == lane)
        judge = judge_hint;
    if (!judge) judge = judge_of(lane);
    if (!judge) return 0;
    float bpm = chart_bpm(judge);
    if (bpm <= 0.f) return 0;
    g_ctx.judge = judge; g_ctx.lane = lane; g_ctx.bpm = bpm;
    return 1;
}

/* Lane target for the speed g_v (+ the Fever's steps): (v / 10) * 100 / BPM. */
static float target_for(int tenths)
{
    float t = (float)tenths / 10.f * 100.f / g_ctx.bpm;
    return t < 0.05f ? 0.05f : t > 60.f ? 60.f : t;
}

/* R / L / Fever: a new target, the current speed snaps to the old target first and eases from there (what
 * 0x1B40C(lane, 0) does with an index). */
static void retarget(uint32_t lane)
{
    store_f(lane + 0x1944, load_f(lane + 0x1940));
    store_f(lane + 0x1940, target_for(g_v + g_boost));
}

static void show_label(void)
{
    atomic_store(&g_label_t, g_v + g_boost);
    atomic_store(&g_label_vb, psp_display_vblanks() + 1);
}

static void lazy_init(void)
{
    if (g_inited) return;
    g_inited = 1;
    uint8_t b = psp_read8(SPEED_BYTE);
    int from_file = file_load();
    if (!from_file) {
        g_v = (int)b * 5;
        if (g_v < MIN_T) g_v = MIN_T;
        if (g_v > MAX_T) g_v = MAX_T;
    }
    set_byte(idx_of(g_v));
    psp_log("speed: respect, start %d.%d (%s, game byte %u)\n", g_v / 10, g_v % 10,
            from_file ? "from es_speed.json" : "from the game's index", b);
}

/* ---- every vblank, from host.c ----------------------------------------------- */

void djmax_speed_tick(void)
{
    if (!djmax_speed_respect) return;
    uint64_t vb = psp_display_vblanks();
    int scene = (int)psp_scene_now();
    if (djmax_mission_active()) {            /* Original rules: the game's byte, untouched */
        g_in_mission = 1;
        return;
    }
    if (g_in_mission) {                      /* back from a mission: put the AV representation back */
        g_in_mission = 0;
        if (g_inited) { set_byte(idx_of(g_v)); psp_log("speed: left the mission, back to %d.%d\n", g_v / 10, g_v % 10); }
    }
    if (scene == 3) {
        if (g_dirty && vb - g_dirty_vb >= 30) { file_save(); g_dirty = 0; }
        return;
    }
    if (scene < 2) return;                   /* boot and title: the save is not loaded yet */
    if (!g_inited) { lazy_init(); return; }
    uint8_t b = psp_read8(SPEED_BYTE);
    if (b != g_last_byte) {                  /* R / L in the song list, or a style change reloading its saved speed */
        int nv = (int)b * 5;
        nv = nv < MIN_T ? MIN_T : nv > MAX_T ? MAX_T : nv;
        psp_log("speed: the list set the game's index %u, speed %d.%d (was %d.%d)\n", b, nv / 10, nv % 10,
                g_v / 10, g_v % 10);
        g_v = nv;
        set_byte(idx_of(g_v));
        changed(vb);
    }
    if (g_dirty) { file_save(); g_dirty = 0; }
}

/* Called at the end of a run (host.c): write what has not been written yet. */
void djmax_speed_flush(void)
{
    if (g_dirty) { file_save(); g_dirty = 0; }
}

/* ---- wraps ------------------------------------------------------------------ */

#define RA_SONG_START_A 0x00020580u          /* 0x20578: judge initialisation */
#define RA_SONG_START_B 0x000210D4u          /* 0x210CC: judge restart (retry) */
#define RA_FEVER_UP_A   0x000255C0u          /* 0x255B8 / 0x255E8: Fever x4 / x5 speed the lane up */
#define RA_FEVER_UP_B   0x000255F0u

static int is_fever_down(uint32_t ra)        /* 0x24564 0x24578 0x2517C 0x2519C 0x25314 0x25334 + 8 */
{
    return ra == 0x0002456Cu || ra == 0x00024580u || ra == 0x00025184u || ra == 0x000251A4u ||
           ra == 0x0002531Cu || ra == 0x0002533Cu;
}

/* 0x1B40C(lane, snap): index -> target speed. At the song-start call sites (the judge's initialisation
 * 0x20578 and restart 0x210CC) the chart's tempo is not loaded yet (chart+0xC still holds a placeholder), so
 * Respect only forgets the last song's Fever steps here; the Auto Velocity value is set when the sound
 * player starts (djmax_speed_song_start, from the wrap of 0x77124), a few vblanks later and before any
 * note moves. */
void __real_psp_func_0001B40C(void);

void __wrap_psp_func_0001B40C(void)
{
    uint32_t ra = psp_cpu.r[PSP_REG_RA];
    uint32_t snap = psp_cpu.r[PSP_REG_A1] & 0xFFu;
    __real_psp_func_0001B40C();
    if (snap && (ra == RA_SONG_START_A || ra == RA_SONG_START_B) && djmax_speed_respect) {
        g_boost = 0;
        g_ctx.lane = 0;                      /* a new song: read the tempo again */
    }
}

/* guest_patches.c, 0x77124 (the song's sound player starts): the chart is loaded, so its first tempo is
 * known. Sets the lane's target and current speed to the Auto Velocity value of the chosen speed. */
void djmax_speed_song_start(uint32_t judge)
{
    if (!active() || !ram_ptr(judge) || !psp_mem_ptr(judge + 0x560, 4)) return;
    uint32_t lane = psp_read32(judge + 0x55C);
    if (!ram_ptr(lane) || !psp_mem_ptr(lane + 0x1948, 4)) return;
    lazy_init();
    g_boost = 0;
    g_ctx.lane = 0;
    if (!context_for(lane, judge)) {
        psp_log("speed: song start without a readable chart tempo, the game's index stays\n");
        return;
    }
    float t = target_for(g_v);
    store_f(lane + 0x1940, t);
    store_f(lane + 0x1944, t);
    psp_log("speed: song start, %d.%d -> lane speed %.4f (chart tempo %.3f, judge tempo %.3f)\n", g_v / 10, g_v % 10,
            (double)t, (double)g_ctx.bpm, (double)load_f(judge + 0x20));
}

/* 0x17E90(lane): one step up. R in play; the Fever's two calls (x4, x5). */
void __real_psp_func_00017E90(void);

void __wrap_psp_func_00017E90(void)
{
    uint32_t lane = psp_cpu.r[PSP_REG_A0], ra = psp_cpu.r[PSP_REG_RA];
    int fever_up = ra == RA_FEVER_UP_A || ra == RA_FEVER_UP_B;
    int mission = djmax_mission_active();
    if (fever_up && djmax_fever_respect && !mission) {
        psp_cpu.r[PSP_REG_V0] = 0;           /* "no step taken": the Fever does not remember one either */
        return;
    }
    if (!active()) { __real_psp_func_00017E90(); return; }
    lazy_init();
    if (!context_for(lane, 0)) { __real_psp_func_00017E90(); return; }
    if (fever_up) {                          /* Fever (Original) adds 0.5 on top of the chosen speed */
        if (g_v + g_boost + 5 > MAX_T) { psp_cpu.r[PSP_REG_V0] = 0; return; }
        g_boost += 5;
    } else {
        if (g_v >= MAX_T) { psp_cpu.r[PSP_REG_V0] = 0; return; }
        g_v++;
        set_byte(idx_of(g_v));
        changed(psp_display_vblanks());
    }
    retarget(lane);
    show_label();
    psp_log("speed: up -> %d.%d (+%d.%d Fever) lane target %.4f\n", g_v / 10, g_v % 10, g_boost / 10, g_boost % 10,
            (double)load_f(lane + 0x1940));
    psp_cpu.r[PSP_REG_V0] = 1;
}

/* 0x17F04(lane): one step down. L in play; the end of the Fever scrolls back. */
void __real_psp_func_00017F04(void);

void __wrap_psp_func_00017F04(void)
{
    uint32_t lane = psp_cpu.r[PSP_REG_A0], ra = psp_cpu.r[PSP_REG_RA];
    if (!active()) { __real_psp_func_00017F04(); return; }
    lazy_init();
    if (!context_for(lane, 0)) { __real_psp_func_00017F04(); return; }
    if (is_fever_down(ra)) {
        if (g_boost < 5) { psp_cpu.r[PSP_REG_V0] = 0; return; }
        g_boost -= 5;
    } else {
        if (g_v <= MIN_T) { psp_cpu.r[PSP_REG_V0] = 0; return; }
        g_v--;
        set_byte(idx_of(g_v));
        changed(psp_display_vblanks());
    }
    retarget(lane);
    show_label();
    psp_log("speed: down -> %d.%d (+%d.%d Fever) lane target %.4f\n", g_v / 10, g_v % 10, g_boost / 10, g_boost % 10,
            (double)load_f(lane + 0x1940));
    psp_cpu.r[PSP_REG_V0] = 1;
}

/* ---- the label ---------------------------------------------------------------- */

/* The window's overlay: "AV 2.3" over the frame, where and for as long as the game's own speed sprite
 * (top left, about a second, fading). Drawn from rasterised glyphs, an outline in black. */
static int glyph_index(char c)
{
    for (int i = 0; i < ES_SPEED_NGLYPHS; i++)
        if (es_speed_glyphs[i].ch == c) return i;
    return -1;
}

/* coverage of glyph g at (x, y) of a pixel grid that maps `scale` source pixels to one destination pixel */
static float glyph_cov(const es_glyph *g, float sx, float sy, float scale)
{
    int x0 = (int)(sx), y0 = (int)(sy);
    int x1 = (int)(sx + scale), y1 = (int)(sy + scale);
    if (x1 == x0) x1++;
    if (y1 == y0) y1++;
    float sum = 0.f;
    int n = 0;
    for (int y = y0; y < y1; y++)
        for (int x = x0; x < x1; x++) {
            n++;
            if (x >= 0 && y >= 0 && x < g->w && y < g->h) sum += g->a[y * g->w + x];
        }
    return n ? sum / (255.f * (float)n) : 0.f;
}

static void blend(uint32_t *d, unsigned r, unsigned g, unsigned b, float a)
{
    if (a <= 0.f) return;
    if (a > 1.f) a = 1.f;
    uint32_t o = *d;
    unsigned or_ = o & 255, og = (o >> 8) & 255, ob = (o >> 16) & 255;
    unsigned nr = (unsigned)((float)r * a + (float)or_ * (1.f - a) + 0.5f);
    unsigned ng = (unsigned)((float)g * a + (float)og * (1.f - a) + 0.5f);
    unsigned nb = (unsigned)((float)b * a + (float)ob * (1.f - a) + 0.5f);
    *d = 0xFF000000u | (nb << 16) | (ng << 8) | nr;
}

int es_speed_overlay(uint32_t *px, int w, int h)
{
    unsigned long long l = atomic_load(&g_label_vb);
    if (!l || !djmax_speed_respect) return 0;
    long long t = (long long)psp_display_vblanks() - (long long)(l - 1);
    if (t < 0 || t >= LABEL_HOLD + LABEL_FADE) return 0;
    float alpha = t < LABEL_HOLD ? 1.f : (float)(LABEL_HOLD + LABEL_FADE - t) / (float)LABEL_FADE;
    int s = w / 480;
    if (s < 1) s = 1;
    int tenths = atomic_load(&g_label_t);
    char txt[16];
    snprintf(txt, sizeof txt, "AV %d.%d", tenths / 10, tenths % 10);

    /* layout: digits 13 units high, top-left at (7, 8), like the sprite it stands in for */
    float k = (13.f * (float)s) / (float)es_speed_digit_h;         /* destination pixels per em-pixel */
    int ox = 7 * s, oy = 8 * s;
    int bw = 92 * s, bh = 24 * s;                                  /* the sprite's rectangle */
    int r = s > 1 ? s : 1;                                         /* outline radius */

    static float *cov;                                             /* the text's coverage, bw x bh */
    static int cov_n;
    if (cov_n < bw * bh) {
        free(cov);
        cov = (float *)calloc((size_t)bw * (size_t)bh, sizeof(float));
        cov_n = cov ? bw * bh : 0;
        if (!cov) return 0;
    }
    memset(cov, 0, sizeof(float) * (size_t)bw * (size_t)bh);
    float pen = (float)ox;
    for (const char *c = txt; *c; c++) {
        if (*c == ' ') { pen += 24.f * k; continue; }
        int gi = glyph_index(*c);
        if (gi < 0) continue;
        const es_glyph *g = &es_speed_glyphs[gi];
        /* position of the mask's top-left in destination pixels; the digit tops sit on y = oy */
        float gx = pen + (float)g->xoff * k;
        float gy = (float)oy + (float)(g->yoff - 27) * k;           /* '0' has yoff 27: its top is the reference */
        int dx0 = (int)gx - 1, dy0 = (int)gy - 1;
        int dx1 = (int)(gx + (float)g->w * k) + 2, dy1 = (int)(gy + (float)g->h * k) + 2;
        for (int y = dy0; y < dy1; y++)
            for (int x = dx0; x < dx1; x++) {
                if (x < 0 || y < 0 || x >= bw || y >= bh) continue;
                float c0 = glyph_cov(g, ((float)x - gx) / k, ((float)y - gy) / k, 1.f / k);
                if (c0 > cov[y * bw + x]) cov[y * bw + x] = c0;
            }
        pen += (float)g->adv * k;
    }
    /* underline bar like the sprite's (white with a dark edge), at the same place */
    int uy = 21 * s, uh = s > 1 ? s : 1;
    for (int y = uy; y < uy + uh && y < bh; y++)
        for (int x = 0; x < 92 * s && x < bw; x++) cov[y * bw + x] = 1.f;

    for (int y = 0; y < bh && y < h; y++)
        for (int x = 0; x < bw && x < w; x++) {
            float c0 = cov[y * bw + x];
            /* outline: the largest coverage within r pixels */
            float o = c0;
            if (o < 1.f) {
                for (int dy = -r; dy <= r && o < 1.f; dy++)
                    for (int dx = -r; dx <= r; dx++) {
                        int xx = x + dx, yy = y + dy;
                        if (xx < 0 || yy < 0 || xx >= bw || yy >= bh) continue;
                        if (dx * dx + dy * dy > r * r) continue;
                        float v = cov[yy * bw + xx];
                        if (v > o) o = v;
                    }
            }
            uint32_t *d = &px[y * w + x];
            blend(d, 0, 0, 0, o * alpha * 0.9f);
            blend(d, 255, 255, 255, c0 * alpha);
        }
    return 1;
}
