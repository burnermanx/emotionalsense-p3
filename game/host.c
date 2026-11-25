/* emotionalsense -- host for DJMAX Portable 3 (ULKS46236), recompiled.
 *
 * Loads the decrypted module image, points the PSP file system at the
 * extracted disc, opens an SDL window that shows the game and reads keyboard
 * and controller, and runs module_start. The game's own threads do the rest;
 * this thread keeps the scheduler turning until the window is closed.
 *
 *   EmotionalSense-P3-game [--data <dir>] [--scale N]
 *
 * <dir> (default $EMOTIONALSENSE_DATA, else $XDG_DATA_HOME/emotionalsense,
 * else ~/.local/share/emotionalsense) is what the launcher produced:
 *
 *   <dir>/module/eboot.elf     the decrypted EBOOT.BIN
 *   <dir>/disc/PSP_GAME/...    disc0:/ and umd0:/
 *   <dir>/ms/                  ms0:/ (saves)
 *
 * Controls are listed in psprecomp/window.h; Alt+Enter toggles borderless full
 * screen, F11 full screen at the chosen rate. */

#include <psprecomp/cpu.h>
#include "psprecomp/log.h"
#include <psprecomp/mem.h>
#include <psprecomp/hle.h>
#include <psprecomp/module.h>
#include <psprecomp/render.h>
#include <psprecomp/audio_hist.h>
#include <psprecomp/window.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#if defined(__ANDROID__) && defined(PSPRECOMP_SDL2)
#  include <SDL_main.h>
#  include <SDL_system.h>
#endif
#ifdef __ANDROID__
#  include <jni.h>
JNIEXPORT void JNICALL
Java_io_emotionalsense_p3_GameActivity_nativeSetTouchState(
        JNIEnv *env, jclass cls, jint buttons, jint lx, jint ly, jint triggers) {
    (void)env;
    (void)cls;
    psp_window_set_touch_state((uint32_t)buttons, (uint8_t)lx, (uint8_t)ly, (uint32_t)triggers);
}
#endif

#include "app_icon.h"
#include "crash.h"
#include "crash_scrub.h"
#include "update_overlay.h"
#include "host_compat.h"
#include "version.h"

void psp_recomp_register(void);   /* in the generated djmax_register.c */
void psp_trace_watch(uint32_t addr, void (*fn)(uint32_t));   /* psprecomp/dispatch.h */
void djmax_hle_register(void);    /* in hle_djmax.c */
void djmax_set_confirm_cross(int cross);
void es_exit_init(int confirm_cross);   /* exit_dialog.c */
extern int djmax_fever_respect;   /* guest_patches.c */
extern int djmax_speed_respect;   /* speed.c */
int djmax_mission_active(void);   /* guest_patches.c */
void djmax_mode_tick(void);       /* guest_patches.c */
void djmax_speed_tick(void);      /* speed.c */
void djmax_speed_flush(void);
void djmax_speed_set_dir(const char *dir);
int es_speed_overlay(uint32_t *px, int w, int h);
int es_exit_overlay(uint32_t *px, int w, int h);
extern int djmax_score_respect;   /* score.c */
void djmax_score_sample(uint32_t sum, uint32_t count);
void djmax_score_set_dir(const char *dir);

/* .text, .data, .bss, and what a module linked at 0 expects above them. */
#define MODULE_SPAN 0x03000000u

static void default_data_dir(char *out, size_t cap) {
    const char *env = getenv("EMOTIONALSENSE_DATA");
    if (env && *env) { snprintf(out, cap, "%s", env); return; }
#if defined(__ANDROID__) && defined(PSPRECOMP_SDL2)
    const char *internal = SDL_AndroidGetInternalStoragePath();
    if (internal && *internal) { snprintf(out, cap, "%s", internal); return; }
#endif
    host_default_data_dir(out, cap);
}

static int is_file(const char *p) {
    struct stat st;
    return stat(p, &st) == 0 && S_ISREG(st.st_mode);
}

/* Built-in unlocks: every chart, every collectible (DJ icons, crew, gear,
 * notes, gallery images, videos) and the two modes the game gates, 4.2T and
 * 6.2T. Nothing outside that scope (no life, score, combo, autoplay, fever,
 * ranking or male-role cheats). Disable all of it with
 * EMOTIONALSENSE_UNLOCK=0, or only the collectibles with
 * EMOTIONALSENSE_UNLOCK_ITEMS=0 -- crews carry gameplay skills such as
 * Anti Break, so someone may want the songs without them.
 *
 * The writes are made in RAM, but the game saves that RAM: the first time it
 * writes the save (leaving the result of a finished song) the unlock goes into
 * SD.BIN, and turning the variables off later does not lock it again. This is
 * accepted and documented (README, docs/unlock-table.md); we do not restore
 * the original state on save.
 *
 * Songs: the "All Music" CWCheat code is an 8-bit fill with stride -- 1500
 * writes, step 10, at 0x00130ED1 -- not 1500 contiguous bytes. CWCheat
 * addresses are offsets from 0x08800000 and the module loads at 0x08804000,
 * so our address is cheat - 0x4000. That lands on byte 9 of each 10-byte
 * chart record in the song table (100 songs x 5 styles x 3 levels). Bytes 6..9
 * are XORed with 0x8B1C65AD; the top two bits of the decoded byte 9 are the
 * unlock state (0 locked, 1 or 2 unlocked). Only locked records are touched,
 * so charts already unlocked by playing keep their state.
 *
 * Modes: 4.2T is gated on the player level, which is read through 0x547F0 --
 * see the wrap in guest_patches.c. 6.2T is item 108 of the bitmap below, so
 * it needs nothing extra. */
#define SONG_TABLE 0x00130EC8u   /* 100 songs x 5 styles x 3 levels x 10 bytes */
#define SONG_KEY9  0x8Bu         /* byte 9 of the 0x8B1C65AD XOR key */
#define MODE_TABLE 0x00134960u   /* [5 modes][3 stages] {song id, difficulty} */
#define ITEM_BITS  0x001349A4u   /* 271 ownership bits, 9 words */
#define ITEM_KEYS  0x00138E00u   /* per-word XOR key, read from the module */
#define ITEM_WORDS 9
#define ITEM_COUNT 271

static void unlock_content(void) {
    static int items = -1;
    if (items < 0) {
        const char *e = getenv("EMOTIONALSENSE_UNLOCK_ITEMS");
        items = !(e && !strcmp(e, "0"));
    }
    /* Write every frame, like the cheat, so the table stays unlocked while
     * the game builds and browses the list. Safe here: the main loop calls
     * this while holding the CPU baton, with the guest threads parked. */
    for (uint32_t k = 0; k < 1500; k++) {
        uint32_t a = SONG_TABLE + k * 10 + 9;
        uint8_t  b = psp_read8(a) ^ SONG_KEY9;
        if ((b & 0xC0) == 0) psp_write8(a, (uint8_t)((b | 0x80) ^ SONG_KEY9));
    }

    /* Repair the damage an earlier build did here. 0x134960 is not a mode
     * field: it is the first song id of the [mode][stage] table, and that
     * build ORed 0x20 into it. Valid ids are 0..39 and 0xFF, so a byte in
     * 0x28..0x3F can only be that corruption. Ids 0..7 that became 0x20..0x27
     * are indistinguishable from real ids 32..39 and stay as they are. */
    uint8_t first = psp_read8(MODE_TABLE);
    if (first >= 0x28 && first <= 0x3F) psp_write8(MODE_TABLE, (uint8_t)(first & ~0x20u));

    /* Everything the game counts as owned: DJ icons, crew, gear, notes, the
     * 6.2T mode, gallery images and videos. One bit per item, 271 of them,
     * word 0 XORed with a key that lives in the module (the others are not).
     * Only bits are set, never cleared, so nothing already earned changes. */
    for (uint32_t w = 0; items && w < ITEM_WORDS; w++) {
        uint32_t a = ITEM_BITS + w * 4;
        uint32_t key = psp_read32(ITEM_KEYS + w * 4);
        uint32_t have = psp_read32(a) ^ key;
        uint32_t want = w == ITEM_WORDS - 1 ? (1u << (ITEM_COUNT - 32u * (ITEM_WORDS - 1))) - 1u
                                            : 0xFFFFFFFFu;
        if ((have | want) != have) psp_write32(a, (have | want) ^ key);
    }
}

/* True when `p` is a repository checkout's work/data (a directory called
 * "data" inside one called "work"). Only a development tree has that layout. */
static int data_is_repo_work_data(const char *p) {
    char rp[4096];
#ifdef _WIN32
    if (!_fullpath(rp, p, sizeof rp)) snprintf(rp, sizeof rp, "%s", p);
    for (char *c = rp; *c; c++) if (*c == '\\') *c = '/';
#else
    if (!realpath(p, rp)) snprintf(rp, sizeof rp, "%s", p);
#endif
    size_t n = strlen(rp);
    while (n > 1 && rp[n - 1] == '/') rp[--n] = 0;
    return (n >= 10 && !strcmp(rp + n - 10, "/work/data")) || !strcmp(rp, "work/data");
}

/* Same rule as the launcher: Japanese and Korean confirm with circle, every
 * other language with cross. Linux: LC_ALL, LC_MESSAGES, LANG (first non-empty,
 * "C"/"POSIX" counts as unset); Windows: the UI language. */
#ifdef _WIN32
#include <windows.h>
#endif
#ifdef _WIN32
/* Index into es_exit_lang_codes (en, pt-BR, es, ja, ko) from the Windows UI language. */
int es_windows_ui_lang(void) {
    switch (PRIMARYLANGID(GetUserDefaultUILanguage())) {
    case LANG_PORTUGUESE: return 1;
    case LANG_SPANISH:    return 2;
    case LANG_JAPANESE:   return 3;
    case LANG_KOREAN:     return 4;
    default:              return 0;
    }
}
#endif
static int es_auto_confirm_cross(char *why, size_t n) {
#ifdef _WIN32
    LANGID id = GetUserDefaultUILanguage();
    unsigned prim = PRIMARYLANGID(id);
    snprintf(why, n, "langid 0x%04x", (unsigned)id);
    return !(prim == LANG_JAPANESE || prim == LANG_KOREAN);
#else
    const char *v = NULL;
    static const char *const names[] = { "LC_ALL", "LC_MESSAGES", "LANG" };
    for (int i = 0; i < 3 && !v; i++) {
        const char *e = getenv(names[i]);
        if (e && *e && strcmp(e, "C") && strcmp(e, "POSIX")) v = e;
    }
    if (!v) { snprintf(why, n, "no locale"); return 1; }
    snprintf(why, n, "%.40s", v);
    return !(!strncmp(v, "ja", 2) || !strncmp(v, "ko", 2));
#endif
}

/* EMOTIONALSENSE_FN_WATCH=0xADDR: one line per entry to that guest function
 * (vblank, scene, return address, call chain). Debugging aid. */
static void fn_watch_hit(uint32_t addr) {
    psp_log("fn[%llu] 0x%08X scene=%u ra=0x%08X calls", (unsigned long long)psp_display_vblanks(),
            addr, psp_scene_now(), psp_cpu.r[PSP_REG_RA]);
    int n = 0;
    for (const psp_frame *f = psp_cpu.frame; f && f->ret && n < 8; f = f->prev, n++)
        psp_log(" 0x%08X", f->ret);
    psp_log_putc('\n');
}

/* Resolve an EMOTIONALSENSE_POKE address expression; 0 if a link is not valid. */
static uint32_t poke_resolve(const char *e) {
    uint32_t cur = 0;
    int first = 1;
    if (*e == '*') {
        e++;
        cur = (uint32_t)strtoul(e, (char **)&e, 0);
        cur = psp_mem_ptr(cur, 4) ? psp_read32(cur) : 0;
        first = 0;
    } else {
        cur = (uint32_t)strtoul(e, (char **)&e, 0);
        first = 0;
    }
    (void)first;
    while (cur && *e) {
        if (*e == '+') {
            cur += (uint32_t)strtoul(e + 1, (char **)&e, 0);
        } else if (*e == '*') {
            e++;
            if (cur < 0x08000000u || cur >= 0x0A000000u || !psp_mem_ptr(cur, 4)) return 0;
            cur = psp_read32(cur);
        } else break;
    }
    return cur;
}

/* Fever "Respect": the judge object's Fever (scene machine 0xC8B30 -> scene
 * -> +0xC -> +0x564) gets its own auto-fever flag (+0x148) held at 1; the
 * lane-speed side lives in guest_patches.c. Only while the gameplay scene is
 * current and every link of the path is a RAM pointer. */
static void fever_respect_tick(void) {
    if (!djmax_fever_respect || psp_scene_now() != 3 || djmax_mission_active()) return;   /* MISSION: Original */
    uint32_t a = poke_resolve("*0xC8B30+0*+0xC*+0x564*");
    if (a >= 0x08000000u && a < 0x0A000000u && psp_mem_ptr(a + 0x14C, 4))
        psp_write8(a + 0x148, 1);
}

/* The window has one overlay slot: the exit dialog (dims the frame) over the speed label. */
static int es_overlay_all(uint32_t *px, int w, int h) {
    int a = es_speed_overlay(px, w, h);
    int b = es_exit_overlay(px, w, h);
    return a | b;
}

/* Respect scoring: remember the judge's weight sum and count while a song is
 * played (score.c uses them on the result screen). Read-only. */
static void score_sample_tick(void) {
    if (psp_scene_now() != 3) return;
    uint32_t j = poke_resolve("*0xC8B30+0*+0xC*");
    if (j >= 0x08000000u && j < 0x0A000000u && psp_mem_ptr(j + 0x818, 4))
        djmax_score_sample(psp_read32(j + 0x814), psp_read16(j + 0x810));
}


/* ---- Renderer report ------------------------------------------------------------------------
 * <data>/logs/renderer.log is written on every start, whatever the log setting: which renderer was
 * asked for, which one runs, the environment that matters to a graphics start (names of paths with
 * the user's name replaced by ~) and every step of the Vulkan start-up with its VkResult. The
 * launcher reads the "result:" line to tell the user when Vulkan did not start. */
#ifdef _WIN32
extern char **_environ;
#  define ES_ENVIRON _environ
#else
extern char **environ;
#  define ES_ENVIRON environ
#endif

static char g_rep_home[512];

static int rep_env_wanted(const char *e) {
    static const char *const pre[] = { "LD_LIBRARY_PATH=", "LD_PRELOAD=", "LD_LIBRARY_PATH_ORIG=", "VK_", "STEAM", "Steam",
        "XDG_SESSION_TYPE=", "XDG_CURRENT_DESKTOP=", "DISPLAY=", "WAYLAND_DISPLAY=", "SDL_", "ENABLE_GAMESCOPE_WSI=",
        "DISABLE_GAMESCOPE_WSI=", "GAMESCOPE", "ENABLE_VK_LAYER", "DISABLE_VK_LAYER", "MESA_", "RADV_", "AMD_VULKAN_ICD=",
        "__GLX_VENDOR_LIBRARY_NAME=", "APPIMAGE=", "EMOTIONALSENSE_" };
    for (size_t i = 0; i < sizeof pre / sizeof *pre; i++)
        if (!strncmp(e, pre[i], strlen(pre[i]))) return 1;
    return 0;
}

static void rep_emit(char c, void *ctx) {
    char **p = (char **)ctx;      /* p[0] = write pointer, p[1] = end */
    if (p[0] < p[1]) *p[0]++ = c;
}

/* Append s to the buffer scrubbed (home -> ~, user name in /home/<name> -> ~), line by line (the
 * scrubber drops control characters, so the newlines are kept here). */
static size_t rep_append(char *buf, size_t cap, size_t n, const char *s) {
    while (*s && n + 1 < cap) {
        size_t len = strcspn(s, "\n");
        char *p[2] = { buf + n, buf + cap - 1 };
        es_scrub(s, len, g_rep_home, rep_emit, p);
        if (p[0] < p[1] && s[len] == '\n') *p[0]++ = '\n';
        *p[0] = 0;
        n = (size_t)(p[0] - buf);
        s += len + (s[len] == '\n');
    }
    return n;
}

/* The environment lines for the report, taken before the renderer changes anything. */
static void rep_snapshot_env(char *buf, size_t cap) {
    const char *home =
#ifdef _WIN32
        getenv("USERPROFILE");
#else
        getenv("HOME");
#endif
    if (home && strlen(home) > 2 && strlen(home) < sizeof g_rep_home) snprintf(g_rep_home, sizeof g_rep_home, "%s", home);
    size_t n = 0;
    buf[0] = 0;
    for (char **e = ES_ENVIRON; e && *e; e++) {
        if (!rep_env_wanted(*e)) continue;
        n = rep_append(buf, cap, n, "env ");
        n = rep_append(buf, cap, n, *e);
        n = rep_append(buf, cap, n, "\n");
    }
}

static void write_renderer_report(const char *data, const char *requested, const char *result, const char *env_text) {
    static char buf[40960];
    size_t n = 0;
    char head[512];
    time_t t = time(NULL);
    char ts[64] = "?";
    struct tm *tm = localtime(&t);
    if (tm) strftime(ts, sizeof ts, "%Y-%m-%d %H:%M:%S", tm);
    snprintf(head, sizeof head, "EmotionalSense-P3 renderer report\nversion: %s (psprecomp %s)\ntime: %s\nrequested: %s\nresult: %s\n",
             EMOTIONALSENSE_VERSION_FULL, EMOTIONALSENSE_PSPRECOMP, ts, requested, result);
    n = rep_append(buf, sizeof buf, 0, head);
    n = rep_append(buf, sizeof buf, n, env_text);
    n = rep_append(buf, sizeof buf, n, psp_report_text());
    char dir[1100], path[1200];
    snprintf(dir, sizeof dir, "%s/logs", data);
    host_mkdir(dir);
    snprintf(path, sizeof path, "%s/renderer.log", dir);
    FILE *f = fopen(path, "wb");
    if (f) { fwrite(buf, 1, n, f); fclose(f); }
}

int main(int argc, char **argv) {
    static char data[1024], elf[1200], ms[1200];
    host_stdio_init();
    const char *capture_path = NULL;
    const char *renderer = getenv("EMOTIONALSENSE_RENDERER");
    int scale = 0, max_frames = 0, target_fps = -1, internal_scale = 0;  /* 0 = auto */
    const char *internal_env = getenv("EMOTIONALSENSE_INTERNAL_SCALE");
    if (internal_env && *internal_env) internal_scale = atoi(internal_env);  /* "auto" -> 0 */
    const char *tex_filter = getenv("EMOTIONALSENSE_TEX_FILTER");
    const char *unlock_env = getenv("EMOTIONALSENSE_UNLOCK");
    int unlock = !(unlock_env && !strcmp(unlock_env, "0"));
    /* Which face button confirms in menus: circle (the game's own, default) or
     * cross. The launcher resolves "auto" from the language and passes one of
     * the two. */
    /* EMOTIONALSENSE_FULLSCREEN / --fullscreen: 1 start in (borderless) full
     * screen, 0 in a window, auto (default) the mode used last time, else full
     * screen under gamescope. Alt+Enter and F11 toggle it in the game. */
    const char *fullscreen = getenv("EMOTIONALSENSE_FULLSCREEN");
    /* EMOTIONALSENSE_FEVER / --fever: respect (default) or original. */
    const char *fever = getenv("EMOTIONALSENSE_FEVER");
    /* EMOTIONALSENSE_SCORE / --score: respect (default) or original. */
    const char *score_mode = getenv("EMOTIONALSENSE_SCORE");
    /* EMOTIONALSENSE_SPEED / --speed: respect (default, Auto Velocity in steps of 0.1) or original. */
    const char *speed_mode = getenv("EMOTIONALSENSE_SPEED");
    const char *confirm = getenv("EMOTIONALSENSE_CONFIRM");   /* NULL/""/"auto" -> follow the system language */
    setvbuf(stdout, NULL, _IOLBF, 0);    /* keep log order intact if the game traps */
    default_data_dir(data, sizeof data);
    /* An explicit data directory (--data or EMOTIONALSENSE_DATA) is never
     * second-guessed: if it is wrong the run fails instead of falling back to
     * some other tree -- which could be the user's real save. */
    int data_explicit = getenv("EMOTIONALSENSE_DATA") && *getenv("EMOTIONALSENSE_DATA");
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--data") && i + 1 < argc)          { snprintf(data, sizeof data, "%s", argv[++i]); data_explicit = 1; }
        else if (!strcmp(argv[i], "--scale") && i + 1 < argc)    scale = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--frames") && i + 1 < argc)   max_frames = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--capture") && i + 1 < argc)  capture_path = argv[++i];
        else if (!strcmp(argv[i], "--renderer") && i + 1 < argc) renderer = argv[++i];
        else if (!strcmp(argv[i], "--fps") && i + 1 < argc)      target_fps = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--internal-scale") && i + 1 < argc) internal_scale = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--tex-filter") && i + 1 < argc) tex_filter = argv[++i];
        else if (!strcmp(argv[i], "--confirm") && i + 1 < argc)  confirm = argv[++i];
        else if (!strcmp(argv[i], "--fullscreen") && i + 1 < argc) fullscreen = argv[++i];
        else if (!strcmp(argv[i], "--fever") && i + 1 < argc)    fever = argv[++i];
        else if (!strcmp(argv[i], "--score") && i + 1 < argc)    score_mode = argv[++i];
        else if (!strcmp(argv[i], "--speed") && i + 1 < argc)    speed_mode = argv[++i];
        else if (!strcmp(argv[i], "--verbose") || !strcmp(argv[i], "-v")) psp_log_enable(1);
        else if (!strcmp(argv[i], "--version") || !strcmp(argv[i], "-V")) {
            printf("EmotionalSense-P3-game %s (psprecomp %s)\n",
                   EMOTIONALSENSE_VERSION_FULL, EMOTIONALSENSE_PSPRECOMP);
            return 0;
        }
        else if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            printf("usage: %s [--data <dir>] [--scale N] [--internal-scale N|auto] [--tex-filter nearest|linear|auto] [--fps N] [--frames N] [--capture file.ppm] [--renderer vulkan|software|null (a named renderer never falls back)] [--confirm circle|cross|auto] [--fullscreen 1|0|auto] [--fever respect|original] [--score respect|original] [--speed respect|original] [--verbose] [--version]\n\nNothing is printed by default except fatal errors; --verbose (or EMOTIONALSENSE_LOG=1, or any EMOTIONALSENSE_TRACE_* variable) prints the diagnostic log\n", argv[0]);
            return 0;
        }
    }
    if (target_fps >= 0) {
        psp_threadman_set_fps_limit(target_fps);
    }
    djmax_fever_respect = 1;                      /* the default */
    if (fever && *fever) {
        if (!strcmp(fever, "original")) djmax_fever_respect = 0;
        else if (strcmp(fever, "respect"))
            psp_log("emotionalsense: unknown fever mode '%s', using respect\n", fever);
    }
    djmax_score_respect = 1;                      /* the default */
    if (score_mode && *score_mode) {
        if (!strcmp(score_mode, "original")) djmax_score_respect = 0;
        else if (strcmp(score_mode, "respect"))
            psp_log("emotionalsense: unknown score mode '%s', using respect\n", score_mode);
    }
    djmax_speed_respect = 1;                      /* the default */
    if (speed_mode && *speed_mode) {
        if (!strcmp(speed_mode, "original")) djmax_speed_respect = 0;
        else if (strcmp(speed_mode, "respect"))
            psp_log("emotionalsense: unknown speed mode '%s', using respect\n", speed_mode);
    }
    snprintf(elf, sizeof elf, "%s/module/eboot.elf", data);
    if (!is_file(elf) && !data_explicit) {
        const char *candidates[] = {
            "work/data",
            "work/testdata",
            "../share/emotionalsense",
            NULL
        };
        for (int c = 0; candidates[c]; c++) {
            char try_elf[1200];
            snprintf(try_elf, sizeof try_elf, "%s/module/eboot.elf", candidates[c]);
            if (is_file(try_elf)) {
                snprintf(data, sizeof data, "%s", candidates[c]);
                snprintf(elf, sizeof elf, "%s", try_elf);
                break;
            }
        }
    }
    if (!is_file(elf)) {
        psp_fatal(
            "emotionalsense: game data not found in %s\n"
            "Run the EmotionalSense-P3 launcher with your DJMAX Portable 3 ISO first.\n", data);
        return 1;
    }
    if (data_is_repo_work_data(data)) {
        const char *ok = getenv("EMOTIONALSENSE_ALLOW_REAL_DATA");
        if (!(ok && !strcmp(ok, "1"))) {
            psp_fatal(
                "emotionalsense: %s is the repository's work/data (the real save). Refusing to run "
                "against it; use a copy, or set EMOTIONALSENSE_ALLOW_REAL_DATA=1 on purpose.\n", data);
            return 1;
        }
    }
    psp_out("fever: %s\n", djmax_fever_respect ? "respect" : "original");
    psp_out("score: %s\n", djmax_score_respect ? "respect" : "original");
    psp_out("speed: %s\n", djmax_speed_respect ? "respect" : "original");
    djmax_score_set_dir(data);
    djmax_speed_set_dir(data);
    psp_out("data: %s\n", data);
    snprintf(ms, sizeof ms, "%s/ms", data);
    host_mkdir(ms);
    es_crash_init(data);             /* crash/hang reports in <data>/crashes (game/crash.c) */

    if (psp_mem_init() != 0) { psp_fatal("memory init failed\n"); return 1; }
    psp_module m;
    if (psp_module_load(elf, MODULE_SPAN, &m) != 0) return 1;
    psp_cpu_reset();
    psp_hle_init();
    djmax_hle_register();
    {
        char why[64] = "";
        int cross;
        if (!confirm || !*confirm || !strcmp(confirm, "auto")) {
            cross = es_auto_confirm_cross(why, sizeof why);
            psp_out("confirm: %s (auto, %s)\n", cross ? "cross" : "circle", why);
        } else if (!strcmp(confirm, "cross"))  { cross = 1; psp_out("confirm: cross\n"); }
        else if (!strcmp(confirm, "circle"))   { cross = 0; psp_out("confirm: circle\n"); }
        else {
            psp_log("emotionalsense: unknown --confirm '%s', using auto\n", confirm);
            cross = es_auto_confirm_cross(why, sizeof why);
            psp_out("confirm: %s (auto, %s)\n", cross ? "cross" : "circle", why);
        }
        if (cross) djmax_set_confirm_cross(1);
        es_exit_init(cross);
        psp_window_set_overlay(es_overlay_all);     /* exit dialog + speed label, one slot */
    }
    psp_io_set_root(data);           /* disc0:/ -> <data>/disc, ms0:/ -> <data>/ms */
    {   /* Optional 1.02 data update: <data>/update/DATA.TPA; EMOTIONALSENSE_UPDATE=0 turns it off. */
        const char *u = getenv("EMOTIONALSENSE_UPDATE");
        static char combined[1200];
        if (!(u && !strcmp(u, "0")) && es_update_prepare(data, combined, sizeof combined) == 1) {
            psp_io_set_overlay("disc0:/PSP_GAME/INSDIR/DATA.TPA", combined);
            psp_out("data: update 1.02 overlay active\n");
        }
    }
    psp_recomp_register();
    /* The scene word, so the audio trace can tell a gameplay underrun from a
     * scene-change one. */
    /* The scene machine is a heap object (id at +4) whose pointer lives at
     * 0xC8B30; 0x0805E874 is where it lands with the 1.00 data, and moves by
     * 0x580 with the 1.02 update's bigger resident package. */
    psp_set_scene_source_ptr(0x000C8B30u, 4u, 0x0805E874u);

    {   /* EMOTIONALSENSE_WATCH_W=0xADDR logs the first writes to one word. */
        const char *w = getenv("EMOTIONALSENSE_WATCH_W");
        if (w && *w) psp_mem_watch_write((uint32_t)strtoul(w, NULL, 0));
        /* EMOTIONALSENSE_WATCH_R=0xADDR: which guest functions read one word. */
        const char *wr = getenv("EMOTIONALSENSE_WATCH_R");
        if (wr && *wr) psp_mem_watch_read((uint32_t)strtoul(wr, NULL, 0));
    }

    {
        const char *fw = getenv("EMOTIONALSENSE_FN_WATCH");
        if (fw && *fw) psp_trace_watch((uint32_t)strtoul(fw, NULL, 0), fn_watch_hit);
    }
    if (internal_scale < 0) internal_scale = 0;
    if (internal_scale > 4) internal_scale = 4;
    psp_render_scale = internal_scale ? internal_scale : 1;
    /* EMOTIONALSENSE_SUBPIXEL=0|1: keep the fractional lane positions above 1x
     * (default on; 0 = the original rounding, for comparing). */
    {
        const char *sp = getenv("EMOTIONALSENSE_SUBPIXEL");
        if (sp && *sp) psp_render_subpixel = atoi(sp) != 0;
    }
    /* EMOTIONALSENSE_BGA_SMOOTH=0|blend: 50 % blend on the in-between vblank of
     * the 30 fps background video (default 0 = as the game draws it). */
    {
        const char *bs = getenv("EMOTIONALSENSE_BGA_SMOOTH");
        if (bs && *bs) {
            if (!strcmp(bs, "blend") || !strcmp(bs, "1")) psp_render_bga_smooth = 1;
            else if (strcmp(bs, "0") && strcmp(bs, "off"))
                psp_log("emotionalsense: unknown EMOTIONALSENSE_BGA_SMOOTH '%s', using 0\n", bs);
        }
    }
    if (tex_filter && *tex_filter) {
        if (!strcmp(tex_filter, "nearest"))    psp_render_tex_filter = 0;
        else if (!strcmp(tex_filter, "linear")) psp_render_tex_filter = 1;
        else if (strcmp(tex_filter, "auto"))
            psp_log("emotionalsense: unknown --tex-filter '%s', using auto\n", tex_filter);
    }

    if (renderer && *renderer) {
        if (psp_render_select(renderer) != 0)
            psp_log("emotionalsense: unknown renderer '%s', using %s\n",
                    renderer, psp_render_current()->name);
    } else {
        psp_render_select("vulkan");
    }
    /* The window comes first: with --internal-scale auto the scale is the
     * smallest one that covers the window, and only the window knows how big
     * it ended up. */
    {
        static char state[1200];
        snprintf(state, sizeof state, "%s/window-mode", data);
        psp_window_set_state_file(state);
        int fs = -1;   /* auto */
        if (fullscreen && *fullscreen && strcmp(fullscreen, "auto"))
            fs = (!strcmp(fullscreen, "0") || !strcmp(fullscreen, "window")) ? 0 : 1;
        psp_window_set_start_mode(fs);
    }
    if ((psp_window_set_icon(es_app_icon_rgba, es_app_icon_w, es_app_icon_h),
         psp_window_start("EmotionalSense-P3", scale)) != 0) {
        psp_fatal("could not open a window\n");
        return 1;
    }
    if (!internal_scale) {
        int h = psp_window_height();
        int n = h > 0 ? (h + 271) / 272 : 1;
        psp_render_scale = n < 1 ? 1 : n > 4 ? 4 : n;
    }

    {
        static char env_text[8192];
        rep_snapshot_env(env_text, sizeof env_text);
        const char *asked = renderer && *renderer ? renderer : "auto";
        psp_report_clear();
        if (psp_render_current()->init(480, 272) != 0) {
            const char *failed = psp_render_current()->name;
            psp_report("render: %s init failed\n", failed);
            /* A renderer named on the command line (or EMOTIONALSENSE_RENDERER) is a request, not a
             * preference: the Vulkan one then stops with the reason instead of silently running the
             * slow software renderer (diagnosis, `--renderer vulkan`). */
            if (!strcmp(failed, "vulkan") && !strcmp(asked, "vulkan")) {
                write_renderer_report(data, asked, "failed (vulkan requested, no fallback)", env_text);
                psp_fatal("emotionalsense: the Vulkan renderer did not start and --renderer vulkan allows no fallback; "
                          "see %s/logs/renderer.log\n", data);
                return 3;
            }
            psp_render_select("software");
            psp_report("render: falling back to software\n");
            if (psp_render_scale > 1) {
                psp_report("render: the software renderer draws at 1x; internal scale %d ignored\n", psp_render_scale);
                psp_render_scale = 1;
            }
            write_renderer_report(data, asked, !strcmp(failed, "vulkan") ? "software-fallback (vulkan did not start)" : "software-fallback", env_text);
        } else {
            write_renderer_report(data, asked, psp_render_current()->name, env_text);
        }
    }
    psp_out("EmotionalSense-P3-game %s (psprecomp %s)\n",
           EMOTIONALSENSE_VERSION_FULL, EMOTIONALSENSE_PSPRECOMP);
    psp_out("render: %s, internal scale %d, subpixel lane %s, bga smooth %s\n",
           psp_render_current()->name, psp_render_scale,
           psp_render_subpixel && psp_render_scale > 1 ? "on" : "off",
           psp_render_bga_smooth ? "blend" : "off");
    psp_out("emotionalsense: module 0x%08X..0x%08X, entry 0x%08X, gp 0x%08X, %d constructors\n",
           m.base, m.mem_end, m.entry, m.gp, m.nctors);

    psp_module_start(&m);
    /* EMOTIONALSENSE_POLL=0xADDR[:f][,0xADDR...] prints guest words when they
     * change. The ":f" suffix prints the word as a float, for values like the
     * life bar. Cheaper than a write watchpoint, which perturbs timing enough
     * to hide races. */
    uint32_t poll_addr[8] = {0}, poll_last[8] = {0};
    int poll_float[8] = {0}, poll_width[8] = {0};
    char poll_expr[8][96] = {{0}};     /* a pointer path (see POKE), resolved every vblank */
    int poll_n = 0, poll_scene = -1;   /* "scene" polls the scene word wherever it lives */
    if (getenv("EMOTIONALSENSE_POLL")) {
        const char *s = getenv("EMOTIONALSENSE_POLL");
        while (*s && poll_n < 8) {
            if (!strncmp(s, "scene", 5)) { poll_scene = poll_n; s += 5; poll_addr[poll_n] = 0x0805E874u; }
            else if (*s == '*') {
                size_t n = strcspn(s, ",");
                const char *colon = memchr(s, ':', n);
                size_t pn = colon ? (size_t)(colon - s) : n;
                if (pn >= sizeof poll_expr[0]) break;
                memcpy(poll_expr[poll_n], s, pn);
                s += pn;
            }
            else poll_addr[poll_n] = (uint32_t)strtoul(s, (char **)&s, 0);
            /* suffix: :f float, :b byte, :h halfword (default: word) */
            if (*s == ':' && s[1] == 'f') { poll_float[poll_n] = 1; s += 2; }
            else if (*s == ':' && s[1] == 'b') { poll_width[poll_n] = 1; s += 2; }
            else if (*s == ':' && s[1] == 'h') { poll_width[poll_n] = 2; s += 2; }
            poll_n++;
            if (*s == ',') s++;
        }
    }
    /* EMOTIONALSENSE_POKE="ADDR:value[:size],..." writes every vblank. Only
     * for debugging: it defeats whatever the game is trying to do, which is
     * the point when the goal is to see past a game over.
     *
     * ADDR is either a plain address or a pointer path, so a poke can follow
     * heap objects whose address moves (the 1.02 update shifts the heap):
     *   *0xPTR+off*+off...+off
     * Start with the word stored at 0xPTR ("*0xPTR"); "+off" adds; a later "*"
     * replaces the running value by the word it points at. The poke is skipped
     * while any link is 0 or outside RAM. Example, the life gauge during a
     * song (scene machine at 0xC8B30 -> current scene -> judge -> life):
     *   *0xC8B30+0*+0xC*+0x7FC:0x42C80000
     * It is only valid in the gameplay scene, which is the caller's business
     * (the path walks whatever the scene machine holds right now). */
    uint32_t poke_value[8] = {0};
    int poke_size[8] = {0}, poke_n = 0;
    char poke_expr[8][96];
    if (getenv("EMOTIONALSENSE_POKE")) {
        const char *s = getenv("EMOTIONALSENSE_POKE");
        while (*s && poke_n < 8) {
            const char *colon = strchr(s, ':');
            if (!colon || (size_t)(colon - s) >= sizeof poke_expr[0]) break;
            memcpy(poke_expr[poke_n], s, (size_t)(colon - s));
            poke_expr[poke_n][colon - s] = 0;
            poke_value[poke_n] = (uint32_t)strtoul(colon + 1, (char **)&s, 0);
            poke_size[poke_n] = 4;
            if (*s == ':') poke_size[poke_n] = (int)strtoul(s + 1, (char **)&s, 0);
            poke_n++;
            if (*s == ',') s++;
        }
    }
    /* EMOTIONALSENSE_SNAP="dir@vb1,vb2,...": at each vblank, write the RAM
     * (ram_<vb>.bin) and the module image's first 1.5 MiB (mod_<vb>.bin) into
     * dir. Debugging aid for finding game variables by diffing. */
    static char snap_dir[512];
    uint64_t snap_vb[16] = {0};
    int snap_n = 0, snap_next = 0;
    if (getenv("EMOTIONALSENSE_SNAP")) {
        const char *s = getenv("EMOTIONALSENSE_SNAP");
        const char *at = strchr(s, '@');
        if (at && (size_t)(at - s) < sizeof snap_dir) {
            memcpy(snap_dir, s, (size_t)(at - s));
            s = at + 1;
            while (*s && snap_n < 16) {
                snap_vb[snap_n++] = strtoull(s, (char **)&s, 0);
                if (*s == ',') s++;
            }
        }
    }
    int trace_clock = 0;
    uint64_t clock_vb = 0;
    double clock_t0 = 0;
    {
        const char *t = getenv("EMOTIONALSENSE_TRACE_CLOCK");
        trace_clock = t && *t && strcmp(t, "0");
        if (trace_clock) clock_t0 = host_monotonic_seconds();
    }
    while (!psp_window_closed()) {
        psp_threadman_yield();
        /* EMOTIONALSENSE_TRACE_CLOCK=1: does the guest clock track real time?
         * One line per 60 vblanks, so a whole song shows drift and the audio
         * transport in the same log. */
        if (trace_clock) {
            uint64_t vb = psp_display_vblanks();
            if (vb / 60 != clock_vb / 60) {
                double real = host_monotonic_seconds();
                psp_log("clock[%llu]: real %.3fs guest %.3fs audio_blocks %llu intr %llu vbl %llu\n",
                        (unsigned long long)vb, real - clock_t0,
                        (double)psp_clock_us() / 1e6,
                        (unsigned long long)psp_audio_blocks(),
                        (unsigned long long)psp_intr_deliveries(),
                        (unsigned long long)psp_intr_vblanks());
                clock_vb = vb;
            }
        }
        if (snap_next < snap_n && psp_display_vblanks() >= snap_vb[snap_next]) {
            char fn[640];
            snprintf(fn, sizeof fn, "%s/ram_%llu.bin", snap_dir, (unsigned long long)snap_vb[snap_next]);
            FILE *f = fopen(fn, "wb");
            if (f) { fwrite(psp_mem.ram, 1, PSP_RAM_SIZE, f); fclose(f); }
            snprintf(fn, sizeof fn, "%s/mod_%llu.bin", snap_dir, (unsigned long long)snap_vb[snap_next]);
            f = fopen(fn, "wb");
            if (f) {
                for (uint32_t a = 0; a < 0x180000u; a += 4) { uint32_t v = psp_read32(a); fwrite(&v, 4, 1, f); }
                fclose(f);
            }
            psp_log("snap: vblank %llu\n", (unsigned long long)snap_vb[snap_next]);
            snap_next++;
        }
        for (int i = 0; i < poke_n; i++) {
            uint32_t a = poke_resolve(poke_expr[i]), v = poke_value[i];
            if (!a) continue;
            if (poke_size[i] == 1) psp_write8(a, (uint8_t)v);
            else if (poke_size[i] == 2) psp_write16(a, (uint16_t)v);
            else psp_write32(a, v);
        }
        es_crash_tick();
        djmax_mode_tick();
        fever_respect_tick();
        djmax_speed_tick();
        score_sample_tick();
        if (unlock) unlock_content();
        for (int i = 0; i < poll_n; i++) {
            if (poll_expr[i][0]) {
                poll_addr[i] = poke_resolve(poll_expr[i]);
                if (!poll_addr[i]) continue;
            }
            uint32_t v = i == poll_scene ? psp_scene_now()
                       : poll_width[i] == 1 ? psp_read8(poll_addr[i])
                       : poll_width[i] == 2 ? psp_read16(poll_addr[i]) : psp_read32(poll_addr[i]);
            if (v != poll_last[i]) {
                if (poll_float[i]) {
                    float f;
                    memcpy(&f, &v, 4);
                    psp_log("poll[%llu]: 0x%08X = %g (0x%08X)\n",
                            (unsigned long long)psp_display_vblanks(), poll_addr[i],
                            (double)f, v);
                } else {
                    psp_log("poll[%llu]: 0x%08X = 0x%08X t=%.3f\n",
                            (unsigned long long)psp_display_vblanks(), poll_addr[i], v,
                            (double)psp_clock_us() / 1e6);
                }
                poll_last[i] = v;
            }
        }
        if (max_frames > 0 && psp_display_vblanks() >= (uint64_t)max_frames) {
            if (capture_path) {
                int rc = psp_display_capture(capture_path);
                psp_out("emotionalsense: captured frame %llu (fb=0x%08X, pixels=%llu) -> %s (rc=%d)\n",
                       (unsigned long long)psp_display_vblanks(),
                       psp_display_framebuffer(),
                       (unsigned long long)psp_render_pixels(),
                       capture_path, rc);
            }
            break;
        }
    }
    /* Flush any final windowed frame, then report the non-blocking audio
     * transport: how many BGM frames reached the SDL2 queue and how loud the
     * loudest one got (proof the music actually played, dBFS referenced to a
     * full-scale int16). */
    fflush(stdout);
    psprecomp_audio_report();
    /* Phase 0 census: what the game asked the GE for, and which firmware calls
     * it made, at the moment the run ends. */
    psp_ge_dump_stats(psp_log_stream());
    psp_tex_dump_report(psp_log_stream());
    psp_tex_pack_report(psp_log_stream());
    psp_hle_dump_counts(psp_log_stream(), 40);
    {
        const char *vram = getenv("EMOTIONALSENSE_DUMP_VRAM");
        if (vram && *vram) {
            FILE *f = fopen(vram, "wb");
            if (f) { fwrite(psp_mem.vram, 1, PSP_VRAM_SIZE, f); fclose(f); }
        }
    }
    {
        const char *dump = getenv("EMOTIONALSENSE_DUMP_RAM");
        if (dump && *dump) {
            FILE *f = fopen(dump, "wb");
            if (f) { fwrite(psp_mem.ram, 1, PSP_RAM_SIZE, f); fclose(f); }
        }
    }
    {   /* EMOTIONALSENSE_DUMP_RANGE=0xSTART:0xEND:file dumps guest memory,
         * including the module image, which is outside the RAM window. */
        const char *r = getenv("EMOTIONALSENSE_DUMP_RANGE");
        if (r && *r) {
            char *end;
            uint32_t lo = (uint32_t)strtoul(r, &end, 0);
            uint32_t hi = (*end == ':') ? (uint32_t)strtoul(end + 1, &end, 0) : lo;
            const char *path = (*end == ':') ? end + 1 : NULL;
            FILE *f = path && *path ? fopen(path, "wb") : NULL;
            if (f) {
                for (uint32_t a = lo; a < hi; a++) {
                    uint8_t b = psp_read8(a);
                    fwrite(&b, 1, 1, f);
                }
                fclose(f);
            }
        }
    }
    /* _Exit skips stdio teardown, so trace files still buffered here would be
     * lost. Flush them before leaving. */
    es_crash_flush();
    djmax_speed_flush();
    host_exit_now(0);
    return 0;
}
