/* guest_patches.c -- replacements for recompiled DJMAX functions.
 *
 * Linked in with -Wl,--wrap=<symbol> (see CMakeLists.txt), so the generated C
 * stays untouched and can be regenerated freely. */

#include <psprecomp/cpu.h>
#include "psprecomp/log.h"
#include <psprecomp/dispatch.h>
#include <psprecomp/hle.h>
#include <psprecomp/mem.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* 0x7F438 -- PackManager remove(type): erase every entry of the pack stack
 * (a std::deque<Pack*> at *(mgr+72), 32 pointers per node) whose type id,
 * *(pack+4), equals `type`, then call 0x6E1F4(mgr, type).
 *
 * The original loop copies the deque's end iterator once, before it starts.
 * deque::erase (0xB11FC) may release a node and move the real end, so the
 * stale copy is never reached again. With the deque holding a single entry
 * in the last slot of a node (start.cur at the end of one node, finish.cur at
 * the start of the next) the loop re-reads the erased slot, erases it twice
 * more and leaves start after finish. The next remove walks off the node map
 * to address 0 and reads instruction words as pointers.
 *
 * This version keeps the same visible behaviour (erase every match, then call
 * 0x6E1F4) but re-reads the deque on every step.
 *
 *   dq+0..12   start  {cur, first, last, node}
 *   dq+16..28  finish {cur, first, last, node} */

#define DQ_SLOTS 32   /* 128-byte nodes */

static int32_t dq_size(uint32_t dq)
{
    uint32_t s_cur = psp_read32(dq + 0),  s_last = psp_read32(dq + 8),  s_node = psp_read32(dq + 12);
    uint32_t f_cur = psp_read32(dq + 16), f_first = psp_read32(dq + 20), f_node = psp_read32(dq + 28);
    if (s_node == f_node) return (int32_t)(f_cur - s_cur) / 4;
    return ((int32_t)(f_node - s_node) / 4 - 1) * DQ_SLOTS
         + (int32_t)(f_cur - f_first) / 4
         + (int32_t)(s_last - s_cur) / 4;
}

/* Iterator for element `i`, as the four words deque::erase expects. */
static void dq_iter(uint32_t dq, int32_t i, uint32_t out[4])
{
    uint32_t s_cur = psp_read32(dq + 0), s_first = psp_read32(dq + 4), s_node = psp_read32(dq + 12);
    int32_t off = (int32_t)(s_cur - s_first) / 4 + i;
    int32_t n = off / DQ_SLOTS, k = off % DQ_SLOTS;
    if (k < 0) { k += DQ_SLOTS; n--; }
    uint32_t node = s_node + (uint32_t)(n * 4);
    uint32_t first = psp_read32(node);
    out[0] = first + (uint32_t)(k * 4);
    out[1] = first;
    out[2] = first + DQ_SLOTS * 4;
    out[3] = node;
}

static void call_guest(uint32_t addr, uint32_t a0, uint32_t a1, uint32_t a2)
{
    psp_cpu.r[PSP_REG_A0] = a0;
    psp_cpu.r[PSP_REG_A1] = a1;
    psp_cpu.r[PSP_REG_A2] = a2;
    psp_cpu.r[PSP_REG_RA] = 0;
    psp_call_from_host(addr);
}

void __real_psp_func_0007F438(void);

void __wrap_psp_func_0007F438(void)
{
    /* Escape hatch for A/B runs: the original crashes only on the pause-exit
     * path, so a straight run to gameplay can still use it. */
    if (getenv("EMOTIONALSENSE_PACK_ORIGINAL")) { __real_psp_func_0007F438(); return; }

    static int trace = -1;
    if (trace < 0) trace = getenv("EMOTIONALSENSE_TRACE_PACK") != NULL;

    psp_cpu_state saved = psp_cpu;
    uint32_t mgr = saved.r[PSP_REG_A0], type = saved.r[PSP_REG_A1];
    uint32_t dq = psp_read32(mgr + 72);

    /* Scratch for two iterators on the guest stack, as the original used. */
    uint32_t buf = saved.r[PSP_REG_SP] - 32;

    for (int32_t i = 0; i < dq_size(dq); ) {
        uint32_t it[4];
        dq_iter(dq, i, it);
        uint32_t pack = psp_read32(it[0]);
        if (!pack || psp_read32(pack + 4) != type) { i++; continue; }

        if (trace)
            psp_log("pack remove: type %u at index %d of %d\n",
                    type, i, dq_size(dq));
        for (int w = 0; w < 4; w++) psp_write32(buf + 16 + (uint32_t)w * 4, it[w]);
        psp_cpu.r[PSP_REG_SP] = buf;
        call_guest(0x000B11FCu, dq, buf, buf + 16);   /* deque::erase(out, pos) */
        psp_cpu.r[PSP_REG_SP] = saved.r[PSP_REG_SP];
        /* Not i++: the next element has moved into slot i. */
    }

    psp_cpu.r[PSP_REG_SP] = buf;
    call_guest(0x0006E1F4u, mgr, type, 0);
    psp_cpu = saved;
}

/* 0x61270 -- the check's log formatter. The check itself (0x14868) is called
 * from the same generated unit that defines it, so --wrap cannot see it; this
 * is the same point the gdb script uses. The verdict is in a2..a5 when a1 is
 * one of the check's format strings. */
void __real_psp_func_00061270(void);

void __wrap_psp_func_00061270(void)
{
    psp_cpu_state saved = psp_cpu;
    uint32_t fmt = saved.r[PSP_REG_A1];
    if (getenv("EMOTIONALSENSE_TRACE_UMD") && fmt >= 0xB9A00 && fmt < 0xB9D00)
        psp_log("umd: check vblank=%llu fmt=%x a2=%d a3=%d a4=%d a5=%d\n",
                (unsigned long long)psp_display_vblanks(), fmt,
                (int)saved.r[PSP_REG_A2], (int)saved.r[PSP_REG_A3],
                (int)saved.r[PSP_REG_T0], (int)saved.r[PSP_REG_T1]);
    __real_psp_func_00061270();
}

/* 0x547F0 -- player level, from the profile word at 0x130EAC. PLAY MODE
 * SELECT (0x27C78) and the FreeStyle style submenu (0x2D780) both call it and
 * lock the 4.2T entry below level 5. The level cannot be written in the save
 * -- it shares its word with the title and the experience -- so only those two
 * queries see 5, and only when the unlock is on.
 *
 * The lock itself cannot be wrapped: caller and callee live in the same
 * generated file, and --wrap only catches cross-unit calls. */
void __real_psp_func_000547F0(void);

void __wrap_psp_func_000547F0(void)
{
    static int on = -1;
    uint32_t ra = psp_cpu.r[PSP_REG_RA];
    if (on < 0) {
        const char *e = getenv("EMOTIONALSENSE_UNLOCK");
        on = !(e && !strcmp(e, "0"));
    }
    __real_psp_func_000547F0();
    if (on && (ra == 0x00027CBCu || ra == 0x0002D7B0u) && psp_cpu.r[PSP_REG_V0] < 5)
        psp_cpu.r[PSP_REG_V0] = 5;
}

/* 0x77124 / 0x7718C -- start and stop of a sound player. Byte 36 of the
 * player is its "running" flag. The gameplay scene has exactly one player, the
 * song: it is started from 0x1710C a few frames into scene 3, and a pause (via
 * 0x17168 -> 0x1FAF0) or a GAME OVER stops it. Its flag is therefore 1 exactly
 * while the lanes receive notes, and 0 on PAUSE, GAME OVER and before the
 * start. The player lives in the heap and moves with the mode and the
 * song, so the address is learned here instead of being a constant. The
 * finished song keeps the flag until scene 4 (the result) takes over.
 *
 * The caller of 0x77124 that matters is in another unit (0x17148), so --wrap
 * sees it; the resume path (0x77194 -> 0x77124) is in the same unit and does
 * not matter, the flag is read from memory, not from the calls. */
#define SCENE_WORD    psp_scene_now()   /* found through the pointer at 0xC8B30, see host.c */
#define SCENE_PLAY    3u
#define PLAYER_FLAG   36u
static uint32_t g_song_player;
void djmax_speed_song_start(uint32_t judge);   /* speed.c */

void __real_psp_func_00077124(void);
void __real_psp_func_0007718C(void);

void __wrap_psp_func_00077124(void)
{
    uint32_t obj = psp_cpu.r[PSP_REG_A0];
    if (SCENE_WORD == SCENE_PLAY) { g_song_player = obj; djmax_speed_song_start(obj); }
    if (getenv("EMOTIONALSENSE_TRACE_PLAYER"))
        psp_log("player: start obj=%08X vb=%llu scene=%u ra=%08X\n",
                obj, (unsigned long long)psp_display_vblanks(),
                SCENE_WORD, psp_cpu.r[PSP_REG_RA]);
    __real_psp_func_00077124();
}

void __wrap_psp_func_0007718C(void)
{
    if (getenv("EMOTIONALSENSE_TRACE_PLAYER"))
        psp_log("player: stop  obj=%08X vb=%llu scene=%u ra=%08X\n",
                psp_cpu.r[PSP_REG_A0], (unsigned long long)psp_display_vblanks(),
                SCENE_WORD, psp_cpu.r[PSP_REG_RA]);
    __real_psp_func_0007718C();
}

/* 1 while the player is actively playing a song: the lanes are live and a
 * circle or cross is a key, not a menu choice. */
int djmax_playing(void)
{
    if (SCENE_WORD != SCENE_PLAY) { g_song_player = 0; return 0; }
    return g_song_player && psp_read8(g_song_player + PLAYER_FLAG) != 0;
}

/* 1 while a song has been started and is not running: PAUSE, GAME OVER, the mission's failed menu. Not before the
 * song starts (g_song_player is 0 until then) and not after the result (the scene changes). */
int djmax_song_paused(void)
{
    return SCENE_WORD == SCENE_PLAY && g_song_player && psp_read8(g_song_player + PLAYER_FLAG) == 0;
}

/* ---- Fever, "Respect" mode ------------------------------------------------
 *
 * How the game's Fever works (found in round 27, see docs/fever.md). The judge
 * object (scene +0xC, scene machine at 0xC8B30) owns a Fever object at +0x564
 * ("F"):
 *   F+0x13a  gauge full, waiting for the Fever button      F+0x13b  Fever running
 *   F+0x135  Fever level index (x2 = 1 ... x5 = 4)          F+0x134  highest level
 *   F+0x136  gauge in percent                                F+0x148  AUTO FEVER
 * A MAX 100% hit adds to the gauge (0x25358); when it reaches 100 the game
 * activates Fever (0x254FC) at once if F+0x148 is set (a crew skill sets it),
 * otherwise it waits for the Fever button, id 10 of the key table, CROSS.
 * Activating level index 3 (x4) and 5 calls 0x17E90 -- the same routine the R
 * button uses to speed the lane up one step -- from 0x255B8 / 0x255E8, and
 * remembers it in F+0x13C / F+0x13D so the end of the Fever scrolls back.
 *
 * Respect: F+0x148 is held at 1 by the host (see host.c), and those two calls
 * from the Fever are skipped (the wrap of 0x17E90 is in speed.c, next to the
 * speed system, which it has to share the routine with), so the lane keeps the
 * speed the player chose. R still speeds up. */
int djmax_fever_respect = 0;

/* ---- the play mode: MISSION always plays Original (round 35, R) ------------------
 *
 * The game keeps the play mode in the options struct at 0x130E58, byte +4 (0x130E5C):
 *   0  CLASSIC and the other styles        1  FREE STYLE        2  MISSION
 * The menus write it before the match scene is entered: 0x273B4 (style select, 0),
 * 0x279C4 (FREE STYLE, 1) and 0x3B400 (the mission list, 2, together with +8 = 0xFF).
 * 0x53C7C and the speed buttons (0x1FBC0..) read the same byte (== 2 is "mission"
 * there too).
 *
 * djmax_mission_active() is that byte read live while no match runs, and frozen
 * from the moment the match scene (3) is entered, so nothing flips in the middle of
 * a song. The Fever, the scoring and the speed system of the host consult it: in a
 * mission all three are Original, whatever the options say. */
#define PLAY_MODE_BYTE 0x00130E5Cu
static int g_mission;

int djmax_mission_active(void)
{
    if (psp_scene_now() != 3) g_mission = psp_read8(PLAY_MODE_BYTE) == 2;
    return g_mission;
}

/* host.c, every vblank: keeps the frozen value current while no match runs. */
void djmax_mode_tick(void) { (void)djmax_mission_active(); }

/* 0x163C -- open a message window (a0 manager, a1 message id, a2 1, a3 0, t0 an argument pointer).
 *
 * Round 36c. The result screen announces what the song's level-up or its clear "unlocked": a new item (the box the
 * player opened, and the rows of the unlock table in the game's data), a 3.2T / 4.2T unlock, a special mission. The game
 * decides "new" from the player's LEVEL and from the rows it walks (0x42EB8 / 0x43CD4 / 0x4183C state machine at
 * obj+40), not from the unlock bits: the forced unlock sets every bit from the start, the notice still comes when the
 * level crosses the row's level. So with the unlock options on we do not open these windows:
 *   0x413A8                the box item  ("You have acquired a new character / SS pattern ...": the message id is the
 *                          item type's, from 0x404C0)                          -> only with unlock_items
 *   0x4321C / 0x4326C      the rows' items (halfwords +272 / +274)             -> only with unlock_items
 *   0x413FC / 0x4142C      "3.2T / 4.2T unlocked" (ids 0x1D / 0x1E, when the level is 2 / 5)  -> unlock (modes)
 *   0x430DC / 0x430FC      "new HD pattern" (0x16, 4T/6T) / "new SS pattern" (0x17, the 2T styles): the first play of a
 *                          chart, play mark 0 -> unlock (charts)
 * Nothing else is touched: the "level up" window and the box reveal ("Congratulations") are not 0x163C windows or are
 * not at these call sites, and the special mission (id 0x12, 0x43028) is not part of the unlock, so it stays.
 * The save is written exactly as before (the game still marks the item and the notice flags; only the window is not
 * opened, and the state machine goes on, because it waits for the window's busy flag and a window that was never
 * opened is not busy).
 *
 * EMOTIONALSENSE_TRACE_MSG=1 logs every call: vblank, message id, the call site. */
void __real_psp_func_0000163C(void);

static int unlock_env(const char *name)
{
    const char *e = getenv(name);
    return !(e && !strcmp(e, "0"));
}

void __wrap_psp_func_0000163C(void)
{
    static int trace = -1, unlock = -1, items = -1;
    if (trace < 0) {
        trace = getenv("EMOTIONALSENSE_TRACE_MSG") != NULL;
        /* EMOTIONALSENSE_SHOW_NOTICES=1: keep every notice (test aid: the same run with and without the skip) */
        const char *show = getenv("EMOTIONALSENSE_SHOW_NOTICES");
        const int keep = show && *show && strcmp(show, "0");
        unlock = !keep && unlock_env("EMOTIONALSENSE_UNLOCK");
        items = unlock && unlock_env("EMOTIONALSENSE_UNLOCK_ITEMS");
    }
    const uint32_t ra = psp_cpu.r[PSP_REG_RA];
    int skip = 0;
    if (psp_scene_now() == 4) {
        if (items && (ra == 0x000413A8u || ra == 0x0004321Cu || ra == 0x0004326Cu)) skip = 1;
        if (unlock && (ra == 0x000413FCu || ra == 0x0004142Cu)) skip = 1;
        /* "You have acquired a new HD / SS pattern": the first play of a chart (the play mark of (song, style, diff 1) is 0,
         * 0x54EF4 at 0x42958) -- the pattern was already unlocked by the unlock, so there is nothing new to announce. */
        if (unlock && (ra == 0x000430DCu || ra == 0x000430FCu)) skip = 1;
    }
    if (trace)
        psp_log("msg: vb=%llu id=0x%X ra=0x%08X scene=%u %s\n", (unsigned long long)psp_display_vblanks(),
                (unsigned)psp_cpu.r[PSP_REG_A1] & 0xFFFFu, ra, (unsigned)psp_scene_now(), skip ? "skipped" : "shown");
    if (skip) { psp_cpu.r[PSP_REG_V0] = 0; return; }
    /* EMOTIONALSENSE_MSG_PROBE=<id>: the mission notice's window opens with that message id instead (reads the text of an id) */
    static long probe = -2;
    if (probe == -2) { const char *e = getenv("EMOTIONALSENSE_MSG_PROBE"); probe = e && *e ? strtol(e, NULL, 0) : -1; }
    if (probe >= 0 && ra == 0x00043028u) psp_cpu.r[PSP_REG_A1] = (uint32_t)probe;
    __real_psp_func_0000163C();
}

/* 0x54EF4(table, song, style, difficulty): "this chart has a play mark" (the 2 low bits of the record's first halfword,
 * XOR 0xB07F, are not 0). The result screen (0x427B8) tests it before the song's first mark is written and opens the
 * "new pattern" window when it is 0. EMOTIONALSENSE_TRACE_MSG=1 logs the arguments and the answer. */
void __real_psp_func_00054EF4(void);

void __wrap_psp_func_00054EF4(void)
{
    const uint32_t a0 = psp_cpu.r[PSP_REG_A0], a1 = psp_cpu.r[PSP_REG_A1], a2 = psp_cpu.r[PSP_REG_A2], a3 = psp_cpu.r[PSP_REG_A3],
                   ra = psp_cpu.r[PSP_REG_RA];
    __real_psp_func_00054EF4();
    static int trace = -1;
    if (trace < 0) trace = getenv("EMOTIONALSENSE_TRACE_MSG") != NULL;
    if (trace && psp_scene_now() == 4)
        psp_log("mark: vb=%llu table=0x%X song=%u a2=%u a3=%u -> %u ra=0x%08X\n", (unsigned long long)psp_display_vblanks(), a0,
                a1 & 0xFFu, a2 & 0xFFu, a3 & 0xFFu, psp_cpu.r[PSP_REG_V0], ra);
}

/* 0x55EF4 -- "the song has this pattern" (song a1 < 100, style a2... see 0x427B8); logged with TRACE_MSG. */
void __real_psp_func_00055EF4(void);

void __wrap_psp_func_00055EF4(void)
{
    const uint32_t a0 = psp_cpu.r[PSP_REG_A0], a1 = psp_cpu.r[PSP_REG_A1], a2 = psp_cpu.r[PSP_REG_A2], a3 = psp_cpu.r[PSP_REG_A3],
                   ra = psp_cpu.r[PSP_REG_RA];
    __real_psp_func_00055EF4();
    static int trace = -1;
    if (trace < 0) trace = getenv("EMOTIONALSENSE_TRACE_MSG") != NULL;
    if (trace && psp_scene_now() == 4)
        psp_log("have: vb=%llu a0=0x%X a1=%u a2=%u a3=%u -> %u ra=0x%08X\n", (unsigned long long)psp_display_vblanks(), a0,
                a1 & 0xFFu, a2 & 0xFFu, a3 & 0xFFu, psp_cpu.r[PSP_REG_V0], ra);
}
