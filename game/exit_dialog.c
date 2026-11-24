/* emotionalsense -- leave the game from the title screen, with a confirmation.
 *
 * The cancel button (the game's cross in a menu, which is the host's circle in
 * cross-confirm mode; or Escape) pressed on the TITLE asks "Quit the game?".
 * Yes closes the game cleanly (exit code 0, so the launcher that is waiting for
 * it comes back to the front); No, cancel again or Escape closes the dialog and
 * the title is as it was.
 *
 * The title is told by the scene machine (pointer at 0xC8B30, as host.c): scene
 * 1 with the scene object's class 0xCAB7C and its sub-state word (+0x10) at 1.
 * The same class draws the menus after START (sub-state 3); cancel there goes
 * back to the title by itself, which is not touched. No heap address is used.
 *
 * The dialog is drawn by the host over the presented frame (psprecomp's
 * overlay), with texts rasterised in exit_text.c and shapes drawn here: nothing
 * of the game's art, nothing read from the game. While it is open the game reads
 * no buttons at all. */

#include <psprecomp/hle.h>
#include <psprecomp/mem.h>
#include <psprecomp/window.h>

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "exit_text.h"
#include "button_style.h"
#include "psprecomp/log.h"

#define PAD_UP     0x0010u
#define PAD_RIGHT  0x0020u
#define PAD_DOWN   0x0040u
#define PAD_LEFT   0x0080u
#define PAD_CONFIRM 0x2000u     /* what the game reads as circle: its menu "OK" */
#define PAD_CANCEL  0x4000u     /* cross: its menu "Cancel" */

#define SCENE_PTR     0x000C8B30u
#define TITLE_SCENE   1u
#define TITLE_CLASS   0x000CAB7Cu
#define TITLE_SUBSTATE 0x10u    /* 1 title, 3 menus */
#define ARM_FRAMES    20        /* title steady for this many reads before a press counts */

static atomic_int g_open;       /* dialog visible */
static atomic_int g_sel;        /* 0 yes, 1 no */
static int g_lang;
static int g_confirm_cross;     /* the confirm icon: circle (0) or cross (1) */
static int g_enabled = 1;

void es_exit_set_confirm_cross(int cross) { g_confirm_cross = cross != 0; }
void es_exit_enable(int on) { g_enabled = on; }

#ifdef _WIN32
#include <windows.h>
/* Wine/Proton: ntdll exports wine_get_version (real Windows does not). */
static int under_wine(void) {
    HMODULE n = GetModuleHandleA("ntdll.dll");
    return n && GetProcAddress(n, "wine_get_version") != NULL;
}
#endif

/* Steam Game Mode (gamescope) keeps LANG at the system default; the language the
 * user chose is the Steam client's, in ~/.steam/registry.vdf ("language"). Same
 * order as launcher/es_i18n.py. Returns a language index or -1. */
static const char *henv(const char *n) {      /* Wine also exposes the host's as WINE_HOST_<n> */
    const char *v = getenv(n);
    if (v && *v) return v;
#ifdef _WIN32
    char b[96];
    snprintf(b, sizeof b, "WINE_HOST_%s", n);
    v = getenv(b);
    if (v && *v) return v;
#endif
    return NULL;
}

static int steam_lang(void) {
    const char *pre = "";
#ifdef _WIN32
    if (!under_wine()) return -1;      /* real Windows: nothing changes */
    pre = "Z:";                        /* the Unix HOME seen from Wine */
#endif
    const char *e = henv("SteamGameLanguage");
    if (e && *e) {
        if (!strncmp(e, "brazilian", 9) || !strncmp(e, "portuguese", 10)) return 1;
        if (!strncmp(e, "spanish", 7) || !strncmp(e, "latam", 5)) return 2;
        if (!strncmp(e, "japanese", 8)) return 3;
        if (!strncmp(e, "koreana", 7)) return 4;
        if (!strncmp(e, "english", 7)) return 0;
    }
    static const char *const sv[] = { "SteamDeck", "SteamGamepadUI", "SteamOS", "GAMESCOPE_WAYLAND_DISPLAY",
                                      "SteamAppId", "SteamGameId", "STEAM_COMPAT_DATA_PATH" };
    int sess = 0;
    for (int i = 0; i < 7 && !sess; i++) sess = henv(sv[i]) != NULL;
    const char *xd = henv("XDG_CURRENT_DESKTOP");
    if (!sess && !(xd && strstr(xd, "gamescope"))) return -1;
    const char *h = henv("HOME");
    if (!h || !*h) return -1;
    static const char *const rel[] = { "/.steam/registry.vdf", "/.local/share/Steam/registry.vdf" };
    for (int r = 0; r < 2; r++) {
        char path[1024];
        snprintf(path, sizeof path, "%s%s%s", pre, h, rel[r]);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        char line[512];
        int res = -1;
        while (fgets(line, sizeof line, f)) {
            char *k = strstr(line, "\"language\"");
            if (!k) continue;
            char *q = strchr(k + 10, '"');
            if (!q) continue;
            q++;
            if (!strncmp(q, "brazilian", 9) || !strncmp(q, "portuguese", 10)) res = 1;
            else if (!strncmp(q, "spanish", 7) || !strncmp(q, "latam", 5)) res = 2;
            else if (!strncmp(q, "japanese", 8)) res = 3;
            else if (!strncmp(q, "koreana", 7)) res = 4;
            else if (!strncmp(q, "english", 7)) res = 0;
            break;
        }
        fclose(f);
        if (res >= 0) return res;
    }
    return -1;
}

static int pick_lang(void) {
    const char *v = getenv("EMOTIONALSENSE_LANG");
    if (!v || !*v || !strcmp(v, "auto")) {
        { int sl = steam_lang(); if (sl >= 0) return sl; }
        static const char *const names[] = { "LC_ALL", "LC_MESSAGES", "LANG" };
        v = NULL;
        for (int i = 0; i < 3 && !v; i++) {
            const char *e = getenv(names[i]);
            if (e && *e && strcmp(e, "C") && strcmp(e, "POSIX")) v = e;
        }
#ifdef _WIN32
        extern int es_windows_ui_lang(void);   /* host.c */
        if (!v) { int l = es_windows_ui_lang(); return l >= 0 ? l : 0; }
#endif
        if (!v) return 0;
    }
    if (!strncmp(v, "pt", 2)) return 1;
    if (!strncmp(v, "es", 2)) return 2;
    if (!strncmp(v, "ja", 2)) return 3;
    if (!strncmp(v, "ko", 2)) return 4;
    return 0;
}

/* Is the game showing its title screen, and has it been for a moment? */
static int on_title(void) {
    if (psp_scene_now() != TITLE_SCENE) return 0;
    uint32_t app = psp_read32(SCENE_PTR);
    if (app < PSP_RAM_BASE || app >= PSP_RAM_BASE + 0x02000000u) return 0;
    uint32_t obj = psp_read32(app + 0);
    if (obj < PSP_RAM_BASE || obj >= PSP_RAM_BASE + 0x02000000u) return 0;
    return psp_read32(obj) == TITLE_CLASS && psp_read32(obj + TITLE_SUBSTATE) == 1;
}

static void open_dialog(void) {
    atomic_store(&g_sel, 1);        /* default: No */
    atomic_store(&g_open, 1);
    psp_log("exit: confirmation opened\n");
}

/* Called by psprecomp for every read of the pad; returns what the game sees. */
uint32_t es_exit_filter(uint32_t btns) {
    static int title_reads, prev_cancel, prev_dir, prev_confirm, hold_mask;
    int esc = psp_window_take_escape() > 0;
    uint32_t cancel = btns & PAD_CANCEL;

    if (!atomic_load(&g_open)) {
        if (hold_mask) {                      /* just closed: hide the keys still held */
            hold_mask &= (int)btns;
            btns &= ~(uint32_t)(PAD_CANCEL | PAD_CONFIRM | PAD_UP | PAD_DOWN | PAD_LEFT | PAD_RIGHT);
            if (hold_mask) btns &= ~(uint32_t)hold_mask;
        }
        if (!g_enabled) return btns;
        if (on_title()) title_reads++; else title_reads = 0;
        if (title_reads >= ARM_FRAMES && ((cancel && !prev_cancel) || esc)) {
            open_dialog();
            prev_cancel = cancel != 0;
            prev_dir = (int)(btns & (PAD_UP | PAD_DOWN | PAD_LEFT | PAD_RIGHT));
            prev_confirm = (btns & PAD_CONFIRM) != 0;
            return btns & ~(uint32_t)PAD_CANCEL;
        }
        prev_cancel = cancel != 0;
        return btns;
    }

    /* open: the game sees nothing */
    int sel = atomic_load(&g_sel);
    int dir = (int)(btns & (PAD_UP | PAD_DOWN | PAD_LEFT | PAD_RIGHT));
    int newdir = dir & ~prev_dir;
    if (newdir & (PAD_LEFT | PAD_UP)) sel = 0;
    if (newdir & (PAD_RIGHT | PAD_DOWN)) sel = 1;
    atomic_store(&g_sel, sel);
    int confirm = (btns & PAD_CONFIRM) != 0, confirm_edge = confirm && !prev_confirm;
    int cancel_edge = (cancel && !prev_cancel) || esc;
    prev_dir = dir; prev_confirm = confirm; prev_cancel = cancel != 0;
    if (confirm_edge && sel == 0) {
        psp_log("exit: yes, closing the game\n");
        atomic_store(&g_open, 0);
        psp_window_request_close();
        return 0;
    }
    if ((confirm_edge && sel == 1) || cancel_edge) {
        psp_log("exit: dialog closed, staying\n");
        atomic_store(&g_open, 0);
        title_reads = 0;                      /* wait for a steady title again */
        hold_mask = (int)btns;
    }
    return 0;
}

/* ---- drawing -------------------------------------------------------------- */

static void fill(uint32_t *px, int w, int h, int x, int y, int rw, int rh, uint32_t col) {
    unsigned a = col >> 24;
    for (int j = y < 0 ? 0 : y; j < y + rh && j < h; j++)
        for (int i = x < 0 ? 0 : x; i < x + rw && i < w; i++) {
            uint32_t *d = &px[j * w + i];
            if (a == 255) { *d = col; continue; }
            uint32_t o = *d;
            uint32_t r = ((col & 255) * a + (o & 255) * (255 - a)) / 255;
            uint32_t g = (((col >> 8) & 255) * a + ((o >> 8) & 255) * (255 - a)) / 255;
            uint32_t b = (((col >> 16) & 255) * a + ((o >> 16) & 255) * (255 - a)) / 255;
            *d = 0xFF000000u | (b << 16) | (g << 8) | r;
        }
}

static void text(uint32_t *px, int w, int h, const es_exit_text *t, int x, int y, int s, uint32_t col) {
    int rowb = (t->w + 7) / 8;
    for (int j = 0; j < t->h; j++)
        for (int i = 0; i < t->w; i++)
            if (t->bits[j * rowb + i / 8] & (0x80 >> (i % 8)))
                fill(px, w, h, x + i * s, y + j * s, s, s, col);
}

/* A ring (circle button) or a cross, 14 units wide, drawn from primitives. */
/* The confirm and cancel icons follow the button style (buttons.c): a 5x7 letter on the button. */
bs_look djmax_button_for_action(int confirm);   /* buttons.c */
static void letter_icon(uint32_t *px, int w, int h, int x, int y, int s, char L, uint32_t rgb) {
    static const char *const G[4][7] = {
        { "01110", "10001", "10001", "11111", "10001", "10001", "10001" },   /* A */
        { "11110", "10001", "10001", "11110", "10001", "10001", "11110" },   /* B */
        { "10001", "10001", "01010", "00100", "01010", "10001", "10001" },   /* X */
        { "10001", "10001", "01010", "00100", "00100", "00100", "00100" },   /* Y */
    };
    const char *const *g = G[L == 'A' ? 0 : L == 'B' ? 1 : L == 'x' ? 2 : 3];
    const uint32_t col = 0xFF000000u | ((rgb & 0xFF) << 16) | (rgb & 0xFF00u) | ((rgb >> 16) & 0xFF);
    fill(px, w, h, x, y, 14 * s, 14 * s, 0xFF101010u);
    for (int j = 0; j < 7; j++)
        for (int i = 0; i < 5; i++)
            if (g[j][i] == '1') fill(px, w, h, x + (2 + 2 * i) * s, y + 2 * j * s, 2 * s, 2 * s, col);
}
static void icon(uint32_t *px, int w, int h, int x, int y, int s, int cross, uint32_t col) {
    const bs_look l = djmax_button_for_action((cross != 0) == (g_confirm_cross != 0));
    if (l.letter) { letter_icon(px, w, h, x, y, s, l.letter, l.rgb); return; }
    cross = l.shows == 'x';
    const int n = 14;
    for (int j = 0; j < n; j++)
        for (int i = 0; i < n; i++) {
            int on;
            if (cross) {
                on = (abs(i - j) <= 1) || (abs(i - (n - 1 - j)) <= 1);
            } else {
                int dx = 2 * i - (n - 1), dy = 2 * j - (n - 1);
                int d2 = dx * dx + dy * dy;
                on = d2 <= (n - 1) * (n - 1) && d2 >= (n - 5) * (n - 5);
            }
            if (on) fill(px, w, h, x + i * s, y + j * s, s, s, col);
        }
}

/* The overlay: called by the present thread. Returns 1 when it drew. */
int es_exit_overlay(uint32_t *px, int w, int h) {
    if (!atomic_load(&g_open)) return 0;
    int s = w / 480;
    if (s < 1) s = 1;
    const es_exit_text *tx = es_exit_texts[g_lang];
    int sel = atomic_load(&g_sel);
    /* dim the whole frame, then a box in the middle (all in 480x272 units * s) */
    fill(px, w, h, 0, 0, w, h, 0x90000000u);
    int bw = 300, bh = 110;
    int bx = (w - bw * s) / 2, by = (h - bh * s) / 2;
    fill(px, w, h, bx - 2 * s, by - 2 * s, (bw + 4) * s, (bh + 4) * s, 0xFFE8E8E8u);
    fill(px, w, h, bx, by, bw * s, bh * s, 0xF0201810u);
    text(px, w, h, &tx[0], bx + (bw * s - tx[0].w * s) / 2, by + 14 * s, s, 0xFFFFFFFFu);
    for (int k = 0; k < 2; k++) {
        const es_exit_text *o = &tx[1 + k];
        int cx = bx + (k == 0 ? bw / 4 : 3 * bw / 4) * s;
        int ox = cx - (o->w * s) / 2, oy = by + 62 * s;
        int pw = (o->w + 36) * s;
        if (sel == k) {
            fill(px, w, h, cx - pw / 2, oy - 6 * s, pw, (o->h + 12) * s, 0xFF2090E0u);
            text(px, w, h, o, ox, oy, s, 0xFF000000u);
        } else {
            fill(px, w, h, cx - pw / 2, oy - 6 * s, pw, (o->h + 12) * s, 0x60808080u);
            text(px, w, h, o, ox, oy, s, 0xFFE0E0E0u);
        }
    }
    /* what the buttons do: confirm icon = pick, cancel icon = No */
    icon(px, w, h, bx + 8 * s, by + (bh - 20) * s, s, g_confirm_cross, 0xFFC8C8C8u);
    icon(px, w, h, bx + (bw - 22) * s, by + (bh - 20) * s, s, !g_confirm_cross, 0xFF9A9A9Au);
    return 1;
}

void es_exit_init(int confirm_cross) {
    g_lang = pick_lang();
    g_confirm_cross = confirm_cross != 0;
    {   const char *e = getenv("EMOTIONALSENSE_EXIT_DIALOG");
        if (e && !strcmp(e, "0")) g_enabled = 0; }
    psp_out("exit dialog: %s, language %s\n", g_enabled ? "on" : "off", es_exit_lang_codes[g_lang]);
    psp_ctrl_set_filter(es_exit_filter);
    psp_window_set_overlay(es_exit_overlay);
}
