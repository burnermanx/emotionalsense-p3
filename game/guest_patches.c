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

void __real_psp_func_00077124(void);
void __real_psp_func_0007718C(void);

void __wrap_psp_func_00077124(void)
{
    uint32_t obj = psp_cpu.r[PSP_REG_A0];
    if (SCENE_WORD == SCENE_PLAY) g_song_player = obj;
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
 * from the Fever are skipped, so the lane keeps the speed the player chose.
 * The wrap only answers for those two call sites; R still speeds up. */
int djmax_fever_respect = 0;

void __real_psp_func_00017E90(void);

void __wrap_psp_func_00017E90(void)
{
    uint32_t ra = psp_cpu.r[PSP_REG_RA];
    if (djmax_fever_respect && (ra == 0x000255C0u || ra == 0x000255F0u)) {
        psp_cpu.r[PSP_REG_V0] = 0;   /* "no step taken": the Fever does not remember one either */
        return;
    }
    __real_psp_func_00017E90();
}
