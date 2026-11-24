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

#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "exit_text.h"
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
static int g_confirm_cross;     /* confirm with cross (the keys of the dialog do not depend on it) */
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
/* The look of the game's own "Return to game title?" box: an opaque black panel with rounded corners and a thin
 * gold-to-orange outline, the question in white with wide spacing, YES and NO in condensed bold capitals side by
 * side, the chosen one on an orange bar. Shapes from distance functions, texts from the masks of exit_text.c; all in
 * 480x272 units times the scale s (the overlay also runs on the software renderer's frame). The rest of the frame is
 * not dimmed and the panel has no button hints (the game's box has none). */

static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

static void put(uint32_t *px, int w, int x, int y, float r, float g, float b, float a) {
    if (a <= 0.0f) return;
    if (a > 1.0f) a = 1.0f;
    uint32_t o = px[y * w + x];
    float orr = (float)(o & 255), og = (float)((o >> 8) & 255), ob = (float)((o >> 16) & 255);
    px[y * w + x] = 0xFF000000u | ((uint32_t)(b * a + ob * (1 - a) + 0.5f) << 16) |
                    ((uint32_t)(g * a + og * (1 - a) + 0.5f) << 8) | (uint32_t)(r * a + orr * (1 - a) + 0.5f);
}

/* signed distance to a rounded rectangle centred at the origin (half sizes hx, hy, corner radius r) */
static float sd_rrect(float x, float y, float hx, float hy, float r) {
    float qx = fabsf(x) - (hx - r), qy = fabsf(y) - (hy - r);
    float ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0;
    float in = qx > qy ? qx : qy;
    return sqrtf(ox * ox + oy * oy) + (in < 0 ? in : 0) - r;
}

static float mask_at(const es_exit_text *t, float u, float v) {   /* bilinear, u / v in mask pixels */
    u -= 0.5f; v -= 0.5f;
    int x0 = (int)floorf(u), y0 = (int)floorf(v);
    float fx = u - (float)x0, fy = v - (float)y0, sum = 0;
    for (int j = 0; j < 2; j++)
        for (int i = 0; i < 2; i++) {
            int x = x0 + i, y = y0 + j;
            float c = (x >= 0 && y >= 0 && x < t->w && y < t->h) ? (float)t->a[y * t->w + x] / 255.0f : 0.0f;
            sum += c * (i ? fx : 1 - fx) * (j ? fy : 1 - fy);
        }
    return sum;
}

/* the text centred on (cx, cy) in pixels, `alpha` of its colour; 2 / s mask pixels per destination pixel */
static void draw_text(uint32_t *px, int w, int h, const es_exit_text *t, float cx, float cy, int s, unsigned rgb, float alpha) {
    const float k = 2.0f / (float)s;                         /* mask pixels per destination pixel */
    const float tw = (float)t->w / k, th = (float)t->h / k;
    const int x0 = (int)(cx - tw * 0.5f) - 1, y0 = (int)(cy - th * 0.5f) - 1;
    const int sub = s >= 2 ? 1 : 2;                          /* at 1x two taps per axis: the mask is 2x */
    for (int y = y0; y < y0 + (int)th + 3; y++)
        for (int x = x0; x < x0 + (int)tw + 3; x++) {
            if (x < 0 || y < 0 || x >= w || y >= h) continue;
            float c = 0;
            for (int j = 0; j < sub; j++)
                for (int i = 0; i < sub; i++) {
                    float u = ((float)x + ((float)i + 0.5f) / (float)sub - (cx - tw * 0.5f)) * k;
                    float v = ((float)y + ((float)j + 0.5f) / (float)sub - (cy - th * 0.5f)) * k;
                    c += mask_at(t, u, v);
                }
            c /= (float)(sub * sub);
            put(px, w, x, y, (float)(rgb >> 16 & 255), (float)(rgb >> 8 & 255), (float)(rgb & 255), c * alpha);
        }
}

/* The overlay: called by the present thread. Returns 1 when it drew. */
int es_exit_overlay(uint32_t *px, int w, int h) {
    if (!atomic_load(&g_open)) return 0;
    int s = w / 480;
    if (s < 1) s = 1;
    const es_exit_text *tx = es_exit_texts[g_lang];
    const int sel = atomic_load(&g_sel);
    const float S = (float)s;
    /* panel: 452 x 140 units, centred; outline 2 units */
    const float pw = 452.0f * S, ph = 140.0f * S, pcx = (float)w * 0.5f, pcy = (float)h * 0.5f;
    const float rr = 6.0f * S, ow = 2.0f * S;
    const int x0 = (int)(pcx - pw * 0.5f) - 2, x1 = (int)(pcx + pw * 0.5f) + 2;
    const int y0 = (int)(pcy - ph * 0.5f) - 2, y1 = (int)(pcy + ph * 0.5f) + 2;
    for (int y = y0 < 0 ? 0 : y0; y < y1 && y < h; y++)
        for (int x = x0 < 0 ? 0 : x0; x < x1 && x < w; x++) {
            float d = sd_rrect((float)x + 0.5f - pcx, (float)y + 0.5f - pcy, pw * 0.5f, ph * 0.5f, rr);
            float cover = clampf(0.5f - d, 0.0f, 1.0f);                 /* inside the outer edge */
            if (cover <= 0.0f) continue;
            float inner = clampf(0.5f - (d + ow), 0.0f, 1.0f);          /* inside the outline: black */
            float t = clampf(((float)y - (pcy - ph * 0.5f)) / ph, 0.0f, 1.0f);   /* gold at the top, orange at the bottom */
            float gr = 255.0f, gg = 214.0f - 70.0f * t, gb = 70.0f - 70.0f * t;
            float r = gr * (1 - inner), g = gg * (1 - inner), b = gb * (1 - inner);
            put(px, w, x, y, r, g, b, cover);
        }
    /* the question, white, in the upper part */
    draw_text(px, w, h, &tx[0], pcx, pcy - ph * 0.5f + 56.6f * S, s, 0xFFFFFF, 1.0f);
    /* YES and NO: bars 90 x 25 units, 49 units either side of the centre */
    for (int k = 0; k < 2; k++) {
        const float cx = pcx + (k == 0 ? -49.0f : 49.0f) * S, cy = pcy - ph * 0.5f + 111.5f * S;
        if (sel == k) {
            const float bw = 90.0f * S, bh = 25.0f * S;
            for (int y = (int)(cy - bh * 0.5f); y < (int)(cy + bh * 0.5f); y++)
                for (int x = (int)(cx - bw * 0.5f); x < (int)(cx + bw * 0.5f); x++) {
                    if (x < 0 || y < 0 || x >= w || y >= h) continue;
                    float u = ((float)x + 0.5f - (cx - bw * 0.5f)) / bw;      /* 0..1 along the bar */
                    float fade = clampf((u - 0.74f) / 0.26f, 0.0f, 1.0f);    /* the right end darkens to brown */
                    float edge = clampf((0.05f - u) / 0.05f, 0.0f, 1.0f) * 0.35f;
                    float m = fade * fade * (3 - 2 * fade);
                    put(px, w, x, y, 255.0f - 145.0f * m - 60.0f * edge, 154.0f - 70.0f * m - 40.0f * edge, 0.0f, 1.0f);
                }
            draw_text(px, w, h, &tx[1 + k], cx, cy, s, 0xFFFFFF, 1.0f);
        } else {
            draw_text(px, w, h, &tx[1 + k], cx, cy, s, 0x808080, 1.0f);
        }
    }
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
