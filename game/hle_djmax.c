/* emotionalsense -- additional HLE firmware implementations for DJMAX Portable 3.
 *
 * Implements the firmware modules and entry points that DJMAX calls during
 * boot, asset streaming and gameplay which are not yet in psprecomp's base
 * table: sceUmdUser, sceImpose, sceRtc, sceDisplayGetFramePerSec,
 * Kernel_Library (memcpy/memset), sceUtility, sceVaudio, sceAtrac3plus and
 * sceMpeg.
 */

#include <psprecomp/hle.h>
#include "psprecomp/log.h"
#include <psprecomp/cpu.h>
#include <psprecomp/dispatch.h>
#include <psprecomp/mem.h>
#include <psprecomp/render.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#  include <direct.h>
#else
#  include <sys/stat.h>
#endif

static void hle_ok(void) { psp_ret(SCE_KERNEL_ERROR_OK); }

/* EMOTIONALSENSE_TRACE_AV=1: log the audio/video firmware calls and the first
 * bytes of the buffers the game hands us (what is the stream format?). */
static int av_trace(void) {
    static int t = -1;
    if (t < 0) t = getenv("EMOTIONALSENSE_TRACE_AV") != NULL;
    return t;
}
static int mpeg_trace(void) {
    static int t = -1;
    if (t < 0) t = getenv("EMOTIONALSENSE_TRACE_MPEG") != NULL;
    return t;
}
static int g_demux_trace_count;
static void av_hexdump(const char *what, uint32_t addr, uint32_t n) {
    if (!addr) return;
    uint8_t *p = (uint8_t *)psp_mem_ptr(addr, n);
    if (!p) return;
    psp_log("av: %s @0x%08X:", what, addr);
    for (uint32_t i = 0; i < n; i++) psp_log(" %02x", p[i]);
    psp_log_putc('\n');
}

/* ---- sceUmdUser ----------------------------------------------------------
 *
 * DJMAX polls sceUmdGetDriveStat() in 0x000722E4 / 0x0007232C waiting for
 * bit 0x20 (PSP_UMD_READY). Returning 0 leaves the game parked forever on a
 * black screen before it opens a single file. */

#define PSP_UMD_PRESENT  0x02u
#define PSP_UMD_READY    0x20u
#define PSP_UMD_READABLE 0x10u

static uint32_t g_umd_cbid;

static void hle_UmdActivate(void) {
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_UmdGetDriveStat(void) {
    psp_ret(PSP_UMD_PRESENT | PSP_UMD_READABLE | PSP_UMD_READY);
}

static void hle_UmdRegisterUMDCallBack(void) {
    g_umd_cbid = psp_arg(0);
    /* The drive is already readable here, so the callback the game just handed
     * us would never fire otherwise. DJMAX's handler (0x0005D92C) turns it into
     * the "disc ready" flag the gameplay scene waits on before playing a song. */
    if (g_umd_cbid)
        psp_callback_notify(g_umd_cbid, PSP_UMD_PRESENT | PSP_UMD_READABLE | PSP_UMD_READY);
    if (getenv("EMOTIONALSENSE_TRACE_UMD"))
        psp_log("umd: register cbid=%u valid=%d\n", g_umd_cbid, psp_callback_valid(g_umd_cbid));
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* ---- Kernel_Library (memcpy / memset) ------------------------------------ */

static void hle_KernelMemset(void) {
    uint32_t dst = psp_arg(0);
    uint8_t  val = (uint8_t)psp_arg(1);
    uint32_t len = psp_arg(2);
    void *p = len ? psp_mem_ptr(dst, len) : NULL;
    if (p) {
        memset(p, val, len);
    } else {
        for (uint32_t i = 0; i < len; i++) psp_write8(dst + i, val);
    }
    psp_ret(dst);
}

static void hle_KernelMemcpy(void) {
    uint32_t dst = psp_arg(0), src = psp_arg(1), len = psp_arg(2);
    void *dp = len ? psp_mem_ptr(dst, len) : NULL;
    const void *sp = len ? psp_mem_ptr(src, len) : NULL;
    if (dp && sp) {
        memmove(dp, sp, len);
    } else {
        for (uint32_t i = 0; i < len; i++) psp_write8(dst + i, psp_read8(src + i));
    }
    psp_ret(dst);
}

/* ---- sceDisplay ---------------------------------------------------------- */

static void hle_DisplayGetFramePerSec(void) {
    psp_cpu.f[0] = 59.94006f;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* ---- sceRtc -------------------------------------------------------------- */

static void fill_psp_time(uint32_t ptr, int local) {
    if (!ptr) return;
    time_t now = time(NULL);
    struct tm t;
#ifdef _WIN32
    if (local) localtime_s(&t, &now); else gmtime_s(&t, &now);
#else
    if (local) localtime_r(&now, &t); else gmtime_r(&now, &t);
#endif
    /* ScePspDateTime: u16 year, month, day, hour, minute, second; u32 microsecond */
    psp_write16(ptr + 0,  (uint16_t)(t.tm_year + 1900));
    psp_write16(ptr + 2,  (uint16_t)(t.tm_mon + 1));
    psp_write16(ptr + 4,  (uint16_t)t.tm_mday);
    psp_write16(ptr + 6,  (uint16_t)t.tm_hour);
    psp_write16(ptr + 8,  (uint16_t)t.tm_min);
    psp_write16(ptr + 10, (uint16_t)t.tm_sec);
    psp_write32(ptr + 12, 0);
}

static void hle_RtcGetCurrentClock(void) {
    fill_psp_time(psp_arg(0), 0);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_RtcGetCurrentClockLocalTime(void) {
    fill_psp_time(psp_arg(0), 1);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* ---- confirm button ------------------------------------------------------ */

int djmax_playing(void);          /* guest_patches.c */
static int g_confirm_cross;

/* The swap gate: only outside active play does the host's circle/cross mean
 * "the other one" to the game. */
static int swap_outside_play(void) { return !djmax_playing(); }

/* Cross confirms (and circle cancels) in menus. The game hardcodes the
 * opposite, so the buttons are swapped on the sceCtrl value, never in play. */
/* The button prompts the game draws ("OK", "Cancel", YES/NO) are glyph sprites
 * inside UI atlases. Each rule is a texture (dump key and size) and the
 * rectangles of its circle and cross discs. Numbers only: the images are never
 * stored, the renderer rewrites its own uploaded copy while the swap is on.
 * Found by template matching over a dump of every screen. */
static const psp_glyph_rule k_glyph_rules[] = {
    /* key                 w    h    ax   ay   bx   by   gw  gh  kind */
    { 0x0fba947c8ba1f2b9ull, 512, 256, 379, 146, 320, 146, 18, 18, 'S' },  /* PLAY MODE SELECT */
    { 0x3e86e26d5d754018ull, 128, 128,   1,  75,  22,  75, 18, 18, 'S' },  /* menu glyph strip */
    { 0x43176e9e394d6759ull, 512, 256, 445, 233,   0,   0, 18, 19, 'X' },  /* RESULT "OK" */
    { 0x665bed80d6445bb0ull, 256, 256, 207,  27, 143,  27, 18, 18, 'S' },  /* "Cancel / OK" bar */
    { 0x66d76c818fc78d8aull, 256, 256, 230,  87, 230, 147, 18, 18, 'S' },  /* Yes / No */
    { 0x6df8e6862296785dull, 128,  64,  21,  45,   1,  45, 18, 18, 'S' },  /* YES NO / CANCEL OK */
    { 0x93b1446194f40cc3ull, 256, 256, 128,  38,  68,  38, 18, 18, 'S' },  /* song list: Cancel / OK */
};

void djmax_set_confirm_cross(int cross) {
    g_confirm_cross = cross != 0;
    psp_render_set_glyph_rules(g_confirm_cross ? k_glyph_rules : NULL,
                               g_confirm_cross ? (int)(sizeof k_glyph_rules / sizeof k_glyph_rules[0]) : 0);
    psp_ctrl_set_face_swap(g_confirm_cross, swap_outside_play);
}

/* ---- sceUtility ---------------------------------------------------------- */

static void hle_UtilityGetSystemParamInt(void) {
    uint32_t id = psp_arg(0), out = psp_arg(1);
    uint32_t val = 0;
    if (id == 8) val = 1;   /* PSP_SYSTEMPARAM_ID_INT_LANGUAGE: 1 = English */
    /* PSP_SYSTEMPARAM_ID_INT_BUTTON_PREFERENCE: 0 = circle confirms, 1 =
     * cross confirms. The game never asks (circle is hardcoded; the swap is
     * done on the sceCtrl value, see host.c), but a system dialog would. */
    if (id == 9) val = g_confirm_cross ? 1 : 0;
    if (getenv("EMOTIONALSENSE_TRACE_UTIL"))
        psp_log("util: GetSystemParamInt id=%u -> %u\n", id, val);
    if (out) psp_write32(out, val);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* Utility dialog state machine:
 *   0 = NONE, 1 = INIT, 2 = VISIBLE, 3 = QUIT, 4 = FINISHED */
static uint32_t g_savedata_status;
static uint32_t g_msgdialog_status;
static uint32_t g_install_status;

/* SceUtilitySavedataParam field offsets. The game uses one slot whose
 * directory is gameName + saveName under ms0:/PSP/SAVEDATA. */
#define SD_MODE      0x30
#define SD_GAMENAME  0x3C
#define SD_SAVENAME  0x4C
#define SD_FILENAME  0x64
#define SD_BUF       0x74
#define SD_BUFSIZE   0x78
#define SD_DATASIZE  0x7C

static void host_mkdir(const char *p) {
#ifdef _WIN32
    _mkdir(p);
#else
    mkdir(p, 0755);
#endif
}

static void savedata_mkdirs(const char *dir) {
    char tmp[512];
    snprintf(tmp, sizeof tmp, "%s", dir);
    for (char *p = tmp + 1; *p; p++) {
        if (*p != '/') continue;
        *p = '\0';
        host_mkdir(tmp);
        *p = '/';
    }
    host_mkdir(tmp);
}

static int savedata_write(const char *path, const void *data, uint32_t size) {
    char dir[512];
    snprintf(dir, sizeof dir, "%s", path);
    char *slash = strrchr(dir, '/');
    if (slash) { *slash = '\0'; savedata_mkdirs(dir); }
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    size_t n = fwrite(data, 1, size, f);
    fclose(f);
    return n == size ? 0 : -1;
}

static int savedata_read(const char *path, void *buf, uint32_t cap) {
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    size_t n = fread(buf, 1, cap, f);
    fclose(f);
    return (int)n;
}

static void hle_SavedataInitStart(void) {
    uint32_t params = psp_arg(0);
    if (params) {
        uint32_t mode = psp_read32(params + SD_MODE);
        char game[16] = {0}, save[24] = {0}, file[16] = {0}, path[512];
        psp_str(params + SD_GAMENAME, game, sizeof game);
        psp_str(params + SD_SAVENAME, save, sizeof save);
        psp_str(params + SD_FILENAME, file, sizeof file);
        if (file[0]) {
            char dir[64], esc[192];
            snprintf(dir, sizeof dir, "%s%s", game, save);
            psp_io_escape_name(dir, esc, sizeof esc);
            snprintf(path, sizeof path, "%s/ms/PSP/SAVEDATA/%s/%s",
                     psp_io_root(), esc, file);
            if (getenv("EMOTIONALSENSE_TRACE_UTIL"))
                psp_log("util: SavedataInitStart mode=%u path=%s vblank=%llu size=%u ra=0x%08X\n",
                        mode, path, (unsigned long long)psp_display_vblanks(),
                        psp_read32(params + SD_DATASIZE), psp_cpu.r[PSP_REG_RA]);
            if (mode == 1) {          /* SAVE */
                uint32_t buf = psp_read32(params + SD_BUF);
                uint32_t len = psp_read32(params + SD_DATASIZE);
                void *p = len ? psp_mem_ptr(buf, len) : NULL;
                if (p) savedata_write(path, p, len);
            } else if (mode == 0) {   /* LOAD */
                uint32_t buf = psp_read32(params + SD_BUF);
                uint32_t cap = psp_read32(params + SD_BUFSIZE);
                void *p = cap ? psp_mem_ptr(buf, cap) : NULL;
                int n = p ? savedata_read(path, p, cap) : -1;
                if (n >= 0) psp_write32(params + SD_DATASIZE, (uint32_t)n);
            } else if (mode == 3) {   /* DELETE */
                remove(path);
            }
        }
        /* Offset 44 in SceUtilityDialogCommon is `result`. */
        psp_write32(params + 44, 0);
    }
    g_savedata_status = 2;   /* VISIBLE */
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_SavedataUpdate(void) {
    if (g_savedata_status == 2) g_savedata_status = 3;   /* QUIT */
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_SavedataShutdownStart(void) {
    g_savedata_status = 4;   /* FINISHED */
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_SavedataGetStatus(void) {
    uint32_t s = g_savedata_status;
    if (s == 4) g_savedata_status = 0;
    psp_ret(s);
}

static void hle_MsgDialogInitStart(void) {
    uint32_t params = psp_arg(0);
    if (params) psp_write32(params + 44, 0);
    g_msgdialog_status = 2;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MsgDialogUpdate(void) {
    if (g_msgdialog_status == 2) g_msgdialog_status = 3;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MsgDialogShutdownStart(void) {
    g_msgdialog_status = 4;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MsgDialogGetStatus(void) {
    uint32_t s = g_msgdialog_status;
    if (s == 4) g_msgdialog_status = 0;
    psp_ret(s);
}

static void hle_InstallInitStart(void) {
    uint32_t params = psp_arg(0);
    if (params) psp_write32(params + 44, 0);
    g_install_status = 2;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_InstallUpdate(void) {
    if (g_install_status == 2) g_install_status = 3;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_InstallShutdownStart(void) {
    g_install_status = 4;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_InstallGetStatus(void) {
    uint32_t s = g_install_status;
    if (s == 4) g_install_status = 0;
    psp_ret(s);
}

/* ---- sceVaudio ----------------------------------------------------------- */

static uint32_t g_vaudio_samples = 1024;

static void hle_VaudioChReserve(void) {
    if (av_trace()) psp_log("av: VaudioChReserve samples=%u freq=%u ch=%u\n", psp_arg(0), psp_arg(1), psp_arg(2));
    g_vaudio_samples = psp_arg(0) ? psp_arg(0) : 1024;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_VaudioChRelease(void) {
    psp_audio_channel_stop(8);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* sceVaudioOutputBlocking(vol, buf): the BGM/stream channel. Forward the block
 * to the host sink (channel 8) and let the device pace the caller. */
static void hle_VaudioOutputBlocking(void) {
    int vol = (int)psp_arg(0);
    uint32_t buf_addr = psp_arg(1);
    uint32_t n = g_vaudio_samples;
    const int16_t *buf = buf_addr ? (const int16_t *)psp_mem_ptr(buf_addr, n * 4) : NULL;
    /* EMOTIONALSENSE_EXP_MUSIC_DELAY_MS=ms at EMOTIONALSENSE_EXP_MUSIC_AT=<vblank>:
     * experiment, not behaviour. The music thread stalls once, as if the audio
     * hardware had taken that long to accept a block. */
    {
        static int init, done;
        static uint64_t ms, at;
        if (!init) {
            init = 1;
            const char *e = getenv("EMOTIONALSENSE_EXP_MUSIC_DELAY_MS");
            ms = e ? strtoull(e, NULL, 0) : 0;
            const char *a = getenv("EMOTIONALSENSE_EXP_MUSIC_AT");
            at = a ? strtoull(a, NULL, 0) : 0;
        }
        if (ms && !done && psp_display_vblanks() >= at) {
            done = 1;
            psp_log("exp: music stalled %llu ms at vblank %llu\n",
                    (unsigned long long)ms, (unsigned long long)psp_display_vblanks());
            psp_threadman_sleep_real(ms * 1000u);
        }
    }
    if (av_trace()) {
        static uint32_t blocks;
        if (++blocks % 100 == 1)
            psp_log("vaudio[%llu]: block %u t=%.3f\n",
                    (unsigned long long)psp_display_vblanks(), blocks,
                    (double)psp_clock_us() / 1e6);
    }
    if (buf) psp_audio_output(8, vol, vol, buf, (int)n);

    const uint64_t play_us = (uint64_t)n * 1000000u / 44100u;
    psp_audio_wait(8, play_us);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* ---- sceAtrac3plus -------------------------------------------------------
 *
 * The game hands over a RIFF/WAVE (ATRAC3+, format tag 0xFFFE) whose first
 * `bufsize` bytes are in guest memory and streams the rest through
 * sceAtracGetStreamDataInfo / sceAtracAddStreamData. We keep the whole file in
 * a host buffer instead of emulating the firmware's ring buffer: the guest
 * buffer is only a bounce area, always offered from its start, and each
 * AddStreamData appends the bytes written there to the host copy. Blocks are
 * decoded with libavcodec. */

#include "at3_dec.h"

#define MAX_ATRAC_IDS 8
#define AT3_FRAME_SAMPLES 2048

typedef struct {
    int      used;
    uint32_t buf;          /* guest bounce buffer */
    uint32_t buf_size;
    uint8_t *file;         /* host copy of the file so far */
    uint32_t file_size;    /* total, from the RIFF header */
    uint32_t filled;       /* bytes of `file` valid */
    uint32_t data_off;     /* start of the 'data' chunk payload */
    uint32_t data_size;
    uint32_t align;        /* bytes per frame */
    int      channels;
    uint32_t total_frames;
    uint32_t total_samples;
    uint32_t pos;          /* next frame to decode */
    int      loop_num;
    at3_dec *dec;
} djmax_atrac;

static djmax_atrac g_atrac[MAX_ATRAC_IDS];

static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint32_t le16(const uint8_t *p) { return p[0] | p[1] << 8; }

static void atrac_free(djmax_atrac *a) {
    at3_close(a->dec);
    free(a->file);
    memset(a, 0, sizeof *a);
}

static djmax_atrac *atrac_arg(void) {
    int id = (int)psp_arg(0);
    return (id >= 0 && id < MAX_ATRAC_IDS && g_atrac[id].used) ? &g_atrac[id] : NULL;
}

static void hle_AtracSetDataAndGetID(void) {
    uint32_t buf  = psp_arg(0);
    uint32_t size = psp_arg(1);
    if (av_trace()) {
        psp_log("av: AtracSetDataAndGetID buf=0x%08X size=%u\n", buf, size);
        av_hexdump("atrac hdr", buf, 64);
    }
    const uint8_t *src = size >= 12 ? (const uint8_t *)psp_mem_ptr(buf, size) : NULL;
    if (!src || memcmp(src, "RIFF", 4) || memcmp(src + 8, "WAVE", 4)) {
        psp_ret(0x80630006u);                         /* unknown format */
        return;
    }
    for (int i = 0; i < MAX_ATRAC_IDS; i++) {
        if (g_atrac[i].used) continue;
        djmax_atrac *a = &g_atrac[i];
        memset(a, 0, sizeof *a);
        a->file_size = le32(src + 4) + 8;
        a->channels = 2;
        a->align = 0;
        uint32_t rate = 44100, tag = 0;
        const uint8_t *extra = NULL; int extra_n = 0;
        uint32_t p = 12;
        while (p + 8 <= size) {
            uint32_t csz = le32(src + p + 4);
            const uint8_t *c = src + p + 8;
            if (!memcmp(src + p, "fmt ", 4) && p + 8 + 18 <= size) {
                tag = le16(c); a->channels = (int)le16(c + 2); rate = le32(c + 4);
                a->align = le16(c + 12);
                if (csz >= 18 && le16(c + 16) && p + 8 + 18 + le16(c + 16) <= size) {
                    extra = c + 18; extra_n = (int)le16(c + 16);
                }
            } else if (!memcmp(src + p, "fact", 4) && csz >= 4) {
                a->total_samples = le32(c);
            } else if (!memcmp(src + p, "data", 4)) {
                a->data_off = p + 8;
                a->data_size = csz;
                break;
            }
            p += 8 + csz + (csz & 1);
        }
        if (!a->data_off || !a->align) { psp_ret(0x80630006u); return; }
        if (a->data_off + a->data_size > a->file_size) a->data_size = a->file_size - a->data_off;
        a->total_frames = a->data_size / a->align;
        if (!a->total_samples) a->total_samples = a->total_frames * AT3_FRAME_SAMPLES;
        a->file = malloc(a->file_size);
        if (!a->file) { psp_ret(0x80630006u); return; }
        a->filled = size < a->file_size ? size : a->file_size;
        memcpy(a->file, src, a->filled);
        /* 0xFFFE (extensible) and 0x270 are ATRAC3+/ATRAC3 respectively. */
        a->dec = at3_open(tag != 0x270, a->channels, (int)rate, (int)a->align, extra, extra_n);
        if (!a->dec) {
            psp_log("emotionalsense: cannot open ATRAC decoder (tag 0x%X)\n", tag);
            atrac_free(a);
            psp_ret(0x80630006u);
            return;
        }
        a->used = 1;
        a->buf = buf;
        a->buf_size = size;
        if (av_trace())
            psp_log("av: atrac id=%d file=%u data_off=%u frames=%u align=%u ch=%d samples=%u\n",
                    i, a->file_size, a->data_off, a->total_frames, a->align, a->channels, a->total_samples);
        psp_ret((uint32_t)i);
        return;
    }
    psp_ret(0x80630003u);
}

static void hle_AtracReleaseAtracID(void) {
    int id = (int)psp_arg(0);
    if (id >= 0 && id < MAX_ATRAC_IDS && g_atrac[id].used) atrac_free(&g_atrac[id]);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* Frames sitting decoded-ready in the host copy. */
static uint32_t atrac_ready_frames(const djmax_atrac *a) {
    if (a->filled <= a->data_off) return 0;
    uint32_t have = (a->filled - a->data_off) / a->align;
    if (have > a->total_frames) have = a->total_frames;
    return have > a->pos ? have - a->pos : 0;
}

static void hle_AtracDecodeData(void) {
    /* (atracID, outSamples, outN, outEnd, outRemainFrame) */
    djmax_atrac *a = atrac_arg();
    uint32_t out_samples = psp_arg(1);
    uint32_t out_n       = psp_arg(2);
    uint32_t out_end     = psp_arg(3);
    uint32_t out_remain  = psp_arg(4);
    if (!a) { psp_ret(0x80630003u); return; }

    int n = 0, end = 0;
    int16_t *dst = (int16_t *)psp_mem_ptr(out_samples, AT3_FRAME_SAMPLES * 2u * (uint32_t)a->channels);
    if (a->pos >= a->total_frames) {
        end = 1;
    } else if (atrac_ready_frames(a) > 0 && dst) {
        const uint8_t *blk = a->file + a->data_off + (size_t)a->pos * a->align;
        n = at3_decode(a->dec, blk, (int)a->align, dst, AT3_FRAME_SAMPLES);
        a->pos++;
        if (a->pos >= a->total_frames) end = 1;
    }
    if (dst && n < AT3_FRAME_SAMPLES)
        memset(dst + (size_t)n * a->channels, 0, (size_t)(AT3_FRAME_SAMPLES - n) * a->channels * 2);
    if (av_trace()) {
        static int cnt;
        if (cnt++ < 20 || end) psp_log("av: atrac Decode id=%u pos=%u/%u n=%d end=%d ready=%u\n",
                                       psp_arg(0), a->pos, a->total_frames, n, end, atrac_ready_frames(a));
    }
    if (out_n)      psp_write32(out_n, (uint32_t)n);
    if (out_end)    psp_write32(out_end, (uint32_t)end);
    if (out_remain) psp_write32(out_remain, a->filled >= a->file_size ? (uint32_t)-1 : atrac_ready_frames(a));
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracGetRemainFrame(void) {
    djmax_atrac *a = atrac_arg();
    uint32_t out_remain = psp_arg(1);
    if (out_remain) psp_write32(out_remain, !a || a->filled >= a->file_size
                                            ? (uint32_t)-1 : atrac_ready_frames(a));
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracGetSecondBufferInfo(void) {
    /* (atracID, outPos, outBytes): no second buffer is ever needed. */
    if (psp_arg(1)) psp_write32(psp_arg(1), 0);
    if (psp_arg(2)) psp_write32(psp_arg(2), 0);
    psp_ret(0x80630022u);        /* not needed */
}

static void hle_AtracGetStreamDataInfo(void) {
    /* (atracID, writePtr, writableBytes, readOffset) */
    djmax_atrac *a = atrac_arg();
    uint32_t p_ptr  = psp_arg(1);
    uint32_t p_byts = psp_arg(2);
    uint32_t p_ofs  = psp_arg(3);
    uint32_t writable = 0, ofs = 0, buf = 0;
    if (a) {
        buf = a->buf;
        ofs = a->filled;
        writable = a->file_size - a->filled;
        if (writable > a->buf_size) writable = a->buf_size;
    }
    if (av_trace()) psp_log("av: atrac GetStreamDataInfo id=%u -> ptr=0x%08X writable=%u ofs=%u\n", psp_arg(0), buf, writable, ofs);
    if (p_ptr)  psp_write32(p_ptr, buf);
    if (p_byts) psp_write32(p_byts, writable);
    if (p_ofs)  psp_write32(p_ofs, ofs);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracAddStreamData(void) {
    /* (atracID, bytesToAdd): the game wrote them at the bounce buffer. */
    djmax_atrac *a = atrac_arg();
    uint32_t n = psp_arg(1);
    if (!a) { psp_ret(0x80630003u); return; }
    if (av_trace()) psp_log("av: atrac AddStreamData id=%u n=%u filled=%u/%u\n", psp_arg(0), n, a->filled, a->file_size);
    if (n > a->file_size - a->filled) n = a->file_size - a->filled;
    if (n > a->buf_size) n = a->buf_size;
    const uint8_t *src = n ? (const uint8_t *)psp_mem_ptr(a->buf, n) : NULL;
    if (src) { memcpy(a->file + a->filled, src, n); a->filled += n; }
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracGetChannel(void) {
    djmax_atrac *a = atrac_arg();
    if (psp_arg(1)) psp_write32(psp_arg(1), a ? (uint32_t)a->channels : 2);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracGetMaxSample(void) {
    if (psp_arg(1)) psp_write32(psp_arg(1), AT3_FRAME_SAMPLES);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracGetNextSample(void) {
    djmax_atrac *a = atrac_arg();
    if (psp_arg(1)) psp_write32(psp_arg(1), a && a->pos < a->total_frames ? AT3_FRAME_SAMPLES : 0);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracGetSoundSample(void) {
    djmax_atrac *a = atrac_arg();
    if (psp_arg(1)) psp_write32(psp_arg(1), a ? a->total_samples : 0);
    if (psp_arg(2)) psp_write32(psp_arg(2), (uint32_t)-1);
    if (psp_arg(3)) psp_write32(psp_arg(3), (uint32_t)-1);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracGetNextDecodePosition(void) {
    djmax_atrac *a = atrac_arg();
    if (a && a->pos >= a->total_frames) { psp_ret(0x80630024u); return; }   /* all decoded */
    if (psp_arg(1)) psp_write32(psp_arg(1), a ? a->pos * AT3_FRAME_SAMPLES : 0);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracSetLoopNum(void) {
    djmax_atrac *a = atrac_arg();
    if (a) a->loop_num = (int)psp_arg(1);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_AtracResetPlayPosition(void) {
    /* (atracID, sample, bytesWrite1, bytesWrite2). The host copy keeps every
     * byte streamed so far, so seeking back is just moving the cursor. */
    djmax_atrac *a = atrac_arg();
    if (a) {
        uint32_t f = psp_arg(1) / AT3_FRAME_SAMPLES;
        a->pos = f > a->total_frames ? a->total_frames : f;
        /* If the target is not in the host copy yet, the game must re-stream
         * from there: let GetStreamDataInfo ask for it. */
        uint32_t need = a->data_off + a->pos * a->align;
        if (need > a->filled) a->filled = need;
    }
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* ---- sceMpeg --------------------------------------------------------------
 *
 * The game plays PSMF clips with the usual libpsmfplayer sequence:
 *
 *   RingbufferPut -> GetAtracAu -> AtracDecode -> GetAvcAu ->
 *   AvcDecodeYCbCr -> AvcCsc (to the framebuffer)
 *
 * The ring buffer holds 2048-byte MPEG-PS packs. We let the game's own
 * callback fill the guest ring, demux each new pack right away into two host
 * FIFOs (H.264 Annex-B video, ATRAC3+ frames) and hand access units out of
 * those: the video is decoded with libavcodec's H.264, the audio with the
 * ATRAC3+ decoder shared with sceAtrac. */

#include "avc_dec.h"

#define SCE_MPEG_ERROR_NO_DATA 0x80618001u
#define PS_PKT      2048u
#define AT3_AU_HDR  8u        /* 0F D0 xx xx 00 00 00 00 in front of each frame */
#define AT3_BLOCK   744u      /* ATRAC3+ 132 kbps stereo */

typedef struct { uint8_t *d; size_t head, len, cap; uint64_t pushed; } fifo;

static void fifo_push(fifo *f, const uint8_t *p, size_t n) {
    if (f->head && f->head + f->len + n > f->cap) {
        memmove(f->d, f->d + f->head, f->len);
        f->head = 0;
    }
    if (f->head + f->len + n > f->cap) {
        size_t nc = f->cap ? f->cap : 65536;
        while (nc < f->head + f->len + n) nc *= 2;
        uint8_t *nd = realloc(f->d, nc);
        if (!nd) return;
        f->d = nd; f->cap = nc;
    }
    memcpy(f->d + f->head + f->len, p, n);
    f->len += n;
    f->pushed += n;
}
static uint8_t *fifo_ptr(fifo *f) { return f->d + f->head; }
static void fifo_pop(fifo *f, size_t n) { f->head += n; f->len -= n; if (!f->len) f->head = 0; }
static void fifo_clear(fifo *f) { f->head = f->len = 0; }

typedef struct { uint64_t off, pts, dts; } ts_ent;
#define TS_MAX 256
typedef struct { ts_ent e[TS_MAX]; int n; } ts_list;

static void ts_add(ts_list *t, uint64_t off, uint64_t pts, uint64_t dts) {
    if (t->n == TS_MAX) { memmove(t->e, t->e + 1, (TS_MAX - 1) * sizeof t->e[0]); t->n--; }
    t->e[t->n++] = (ts_ent){ off, pts, dts };
}
/* Timestamps of the latest entry at or before abs offset `off`; drops older ones. */
static void ts_take(ts_list *t, uint64_t off, uint64_t *pts, uint64_t *dts) {
    int k = -1;
    for (int i = 0; i < t->n; i++) if (t->e[i].off <= off) k = i;
    if (k < 0) return;
    *pts = t->e[k].pts; *dts = t->e[k].dts;
    memmove(t->e, t->e + k, (size_t)(t->n - k) * sizeof t->e[0]);
    t->n -= k;
}

/* A PES header carries a timestamp once per GOP in the video and every two or
 * three blocks in the audio. Handing that value to every AU in between made
 * the player's two clocks move in steps (16 frames of video, 2-3 blocks of
 * audio), and its recovery loop read the step as being late. Give each AU its
 * own value: the PES timestamp for the first AU that starts in that PES, then
 * one period per AU, without rounding the period at each step. */
typedef struct { uint64_t pts, dts, count; int valid; } ts_interp;
static ts_interp g_vts_i, g_ats_i;

static uint64_t ts_interp_next(ts_interp *s, uint64_t pts, uint64_t dts,
                               uint64_t num, uint64_t den, uint64_t *out_dts) {
    if (!s->valid || s->pts != pts) {
        s->pts = pts; s->dts = dts; s->count = 0; s->valid = 1;
    } else {
        s->count++;
    }
    *out_dts = s->dts + s->count * num / den;
    return s->pts + s->count * num / den;
}

static uint32_t g_rb_addr, g_rb_data, g_rb_cb, g_rb_cb_arg;
static int32_t  g_rb_total_pkts, g_rb_wp;
static int      g_rb_eof;
static fifo     g_vid, g_aud;
static ts_list  g_vts, g_ats;
static uint64_t g_stream_size;                      /* PSMF stream bytes after offset */
static uint64_t g_raw_demuxed;                      /* raw bytes fed to the demuxer */
static uint8_t  g_sig[PS_PKT];                      /* first packet of the stream */
static int      g_sig_have;
static uint64_t g_vid_consumed, g_aud_consumed;     /* abs offsets already handed out */
static int      g_have_vid_stream, g_have_aud_stream;
static int      g_atrac_read;                       /* the game has consumed MPEG audio */

static uint8_t  g_afr[AT3_AU_HDR + AT3_BLOCK];
static int      g_afr_valid;
static uint8_t *g_au_buf; static size_t g_au_len, g_au_cap;   /* pending video AU */
static int      g_au_valid;

static avc_dec *g_avc;
static at3_dec *g_mpeg_at3;

static uint32_t read_be32(uint32_t addr) {
    return ((uint32_t)psp_read8(addr + 0) << 24) | ((uint32_t)psp_read8(addr + 1) << 16) |
           ((uint32_t)psp_read8(addr + 2) <<  8) | ((uint32_t)psp_read8(addr + 3) <<  0);
}

static uint64_t pes_ts(const uint8_t *b) {
    return ((uint64_t)((b[0] >> 1) & 7) << 30) | ((uint64_t)b[1] << 22) |
           ((uint64_t)(b[2] >> 1) << 15) | ((uint64_t)b[3] << 7) | (b[4] >> 1);
}

static void mpeg_reset_streams(void) {
    fifo_clear(&g_vid); fifo_clear(&g_aud);
    g_vts.n = g_ats.n = 0;
    g_vts_i.valid = g_ats_i.valid = 0;
    g_vid.pushed = g_aud.pushed = 0;
    g_vid_consumed = g_aud_consumed = 0;
    g_afr_valid = g_au_valid = 0;
    g_rb_eof = 0;
    g_stream_size = 0;
    g_raw_demuxed = 0;
    g_sig_have = 0;
    g_demux_trace_count = 0;
    if (g_avc) avc_flush(g_avc);
}

static void demux_pack(const uint8_t *p) {
    if (memcmp(p, "\x00\x00\x01\xba", 4)) return;
    size_t o = 14 + (p[13] & 7);
    while (o + 6 <= PS_PKT && !p[o] && !p[o + 1] && p[o + 2] == 1) {
        uint8_t sid = p[o + 3];
        size_t ln = ((size_t)p[o + 4] << 8) | p[o + 5];
        size_t end = o + 6 + ln;
        if (!ln || end > PS_PKT) end = PS_PKT;
        if (sid == 0xE0 || sid == 0xBD) {
            const uint8_t *b = p + o + 6;
            size_t blen = end - (o + 6);
            if (blen >= 3 && 3 + (size_t)b[2] <= blen) {
                size_t hl = b[2];
                uint64_t pts = 0, dts = 0; int has = 0;
                if ((b[1] & 0x80) && hl >= 5) { pts = dts = pes_ts(b + 3); has = 1; }
                if ((b[1] & 0xC0) == 0xC0 && hl >= 10) dts = pes_ts(b + 8);
                const uint8_t *pay = b + 3 + hl;
                size_t plen = blen - 3 - hl;
                if (mpeg_trace()) {
                    if (g_demux_trace_count++ < 80)
                        psp_log("mpeg[%llu]: pack sid=0x%02X ln=%zu hl=%zu plen=%zu has=%d pts=%llu vidpush=%llu audpush=%llu\n",
                                (unsigned long long)psp_display_vblanks(), sid, ln, hl, plen, has,
                                (unsigned long long)pts, (unsigned long long)g_vid.pushed,
                                (unsigned long long)g_aud.pushed);
                }
                if (sid == 0xBD) {
                    if (plen > 4) { pay += 4; plen -= 4; } else plen = 0;   /* substream header */
                    /* The BGA's audio is never read in gameplay; queueing it
                     * fills the ring and starves the video. */
                    if (plen && g_have_aud_stream) {
                        if (has) ts_add(&g_ats, g_aud.pushed, pts, dts);
                        fifo_push(&g_aud, pay, plen);
                    }
                } else {
                    if (has) ts_add(&g_vts, g_vid.pushed, pts, dts);
                    if (plen) fifo_push(&g_vid, pay, plen);
                }
            }
        }
        o = end;
    }
}

static int32_t rb_free_pkts(void) {
    if (g_rb_eof && g_vid.len == 0 && !g_au_valid)
        return g_rb_total_pkts;
    size_t held = g_vid.len + g_aud.len + g_au_len * (size_t)g_au_valid;
    /* A registered stream the game never reads must not stall the video:
     * drop the oldest audio and carry on. If it does read audio, the cap is
     * what paces the feeder (the opening relies on it). */
    if (held > 1024 * 1024 && g_aud.len && !g_atrac_read) {
        size_t drop = g_aud.len / 2;
        fifo_pop(&g_aud, drop);
        g_aud_consumed += drop;
        held = g_vid.len + g_aud.len + g_au_len * (size_t)g_au_valid;
        if (mpeg_trace()) psp_log("mpeg[%llu]: dropped %zu audio bytes (vid=%zu aud=%zu)\n",
                                  (unsigned long long)psp_display_vblanks(), drop, g_vid.len, g_aud.len);
    }
    if (held > 1024 * 1024) return 0;
    /* Count only full packets: the game treats AvailableSize == total as
     * end-of-stream, and a trailing partial packet would otherwise keep it
     * one short forever. */
    int32_t used = (int32_t)(held / PS_PKT);
    int32_t fr = g_rb_total_pkts - used;
    if (fr < 0) fr = 0;
    return fr;
}

static void rb_sync_struct(void) {
    if (!g_rb_addr) return;
    psp_write32(g_rb_addr + 12, (uint32_t)rb_free_pkts());
}

static void hle_MpegQueryStreamOffset(void) {
    uint32_t buf     = psp_arg(1);
    uint32_t out_ofs = psp_arg(2);
    if (av_trace()) av_hexdump("mpeg hdr", buf, 64);
    uint32_t ofs     = buf ? read_be32(buf + 0x08) : 0x800u;
    if (ofs == 0) ofs = 0x800u;
    if (out_ofs) psp_write32(out_ofs, ofs);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegQueryStreamSize(void) {
    uint32_t buf      = psp_arg(0);
    uint32_t out_size = psp_arg(1);
    uint32_t sz       = buf ? read_be32(buf + 0x0C) : 0;
    if (sz != g_stream_size) {
        g_stream_size = sz;
        g_sig_have = 0;
    }
    if (mpeg_trace()) psp_log("mpeg[%llu]: QueryStreamSize %u\n",
                              (unsigned long long)psp_display_vblanks(), sz);
    if (out_size) psp_write32(out_size, sz);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegQueryMemSize(void) { psp_ret(0x10000u); }

static void hle_MpegRingbufferQueryMemSize(void) {
    int32_t pkts = (int32_t)psp_arg(0);
    if (pkts < 1) pkts = 1;
    psp_ret((uint32_t)(pkts * (2048 + 104)));
}

static void hle_MpegRingbufferConstruct(void) {
    uint32_t rb   = psp_arg(0);
    int32_t  pkts = (int32_t)psp_arg(1);
    uint32_t data = psp_arg(2);
    uint32_t cb   = psp_arg(4);
    uint32_t arg  = psp_arg(5);
    if (av_trace()) psp_log("av: MpegRingbufferConstruct pkts=%d data=0x%08X size=%u cb=0x%08X\n",
                            pkts, data, psp_arg(3), cb);

    g_rb_addr       = rb;
    g_rb_total_pkts = pkts > 0 ? pkts : 512;
    g_rb_wp         = 0;
    g_rb_data       = data;
    g_rb_cb         = cb;
    g_rb_cb_arg     = arg;
    mpeg_reset_streams();
    if (mpeg_trace()) psp_log("mpeg[%llu]: RingbufferConstruct rb=0x%08X pkts=%d data=0x%08X cb=0x%08X arg=0x%08X\n",
                              (unsigned long long)psp_display_vblanks(), rb, g_rb_total_pkts, data, cb, arg);

    if (rb) {
        psp_write32(rb +  0, (uint32_t)g_rb_total_pkts);
        psp_write32(rb +  4, 0);
        psp_write32(rb +  8, 0);
        psp_write32(rb + 12, (uint32_t)g_rb_total_pkts);
        psp_write32(rb + 16, 2048);
        psp_write32(rb + 20, data);
        psp_write32(rb + 24, cb);
        psp_write32(rb + 28, arg);
        psp_write32(rb + 32, data + (uint32_t)g_rb_total_pkts * 2048u);
        psp_write32(rb + 36, 0);
        psp_write32(rb + 40, 0);
    }
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegRingbufferDestruct(void) {
    mpeg_reset_streams();
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegRingbufferAvailableSize(void) {
    psp_ret((uint32_t)rb_free_pkts());
}

/* sceMpegRingbufferPut(ringbuffer, numPackets, available): call the game's
 * read callback to refill the ring, then demux what arrived. */
static void hle_MpegRingbufferPut(void) {
    int32_t want = (int32_t)psp_arg(1);
    int32_t avail = (int32_t)psp_arg(2);
    int32_t free_pkts = rb_free_pkts();
    if (avail < want)      want = avail;
    if (free_pkts < want)  want = free_pkts;
    if (want <= 0 || !g_rb_cb) { psp_ret(0); return; }

    int32_t total = 0;
    while (want > 0) {
        int32_t chunk = want;
        if (chunk > g_rb_total_pkts - g_rb_wp) chunk = g_rb_total_pkts - g_rb_wp;   /* no wrap in one call */
        uint32_t ptr = g_rb_data + (uint32_t)g_rb_wp * PS_PKT;

        psp_cpu_state saved = psp_cpu;
        psp_cpu.r[PSP_REG_A0] = ptr;
        psp_cpu.r[PSP_REG_A1] = (uint32_t)chunk;
        psp_cpu.r[PSP_REG_A2] = g_rb_cb_arg;
        psp_cpu.r[PSP_REG_RA] = 0;
        psp_call_from_host(g_rb_cb);
        int32_t added = (int32_t)psp_cpu.r[PSP_REG_V0];
        psp_cpu = saved;

        if (added < 0) { g_rb_eof = 1; break; }
        if (added == 0) break;
        if (added > chunk) added = chunk;
        for (int32_t i = 0; i < added; i++) {
            const uint8_t *pk = (const uint8_t *)psp_mem_ptr(ptr + (uint32_t)i * PS_PKT, PS_PKT);
            /* The game loops a PSMF by feeding the same stream again through
             * the callback; past the end the callback also reads ahead into
             * the next file. The first packet of the stream is the signature:
             * only it restarts the demuxer, everything else is read-ahead. */
            if (g_stream_size && g_raw_demuxed >= g_stream_size) {
                /* Only the small menu-background PSMF loops through the
                 * callback. The music-select preview and the BGA are one-shot:
                 * looping them keeps the game waiting for the preview to end. */
                if (g_stream_size <= 256 * 1024 && pk && g_sig_have &&
                    !memcmp(pk, g_sig, PS_PKT))
                    g_raw_demuxed = 0;
                else
                    continue;
            }
            if (pk && g_raw_demuxed == 0) {
                memcpy(g_sig, pk, PS_PKT);
                g_sig_have = 1;
            }
            if (pk) demux_pack(pk);
            g_raw_demuxed += PS_PKT;
        }
        total += added;
        g_rb_wp = (g_rb_wp + added) % g_rb_total_pkts;
        want -= added;
    }
    if (mpeg_trace()) {
        psp_log("mpeg[%llu]: RingbufferPut uid=%u req=%d avail=%d free=%d -> %d vid=%zu aud=%zu eof=%d wp=%d raw=%llu",
                (unsigned long long)psp_display_vblanks(), psp_threadman_current_uid(),
                (int)psp_arg(1), avail, free_pkts,
                total, g_vid.len, g_aud.len, g_rb_eof, g_rb_wp,
                (unsigned long long)g_raw_demuxed);
        if (g_rb_cb_arg) {
            uint32_t ctx = psp_read32(g_rb_cb_arg);
            psp_log(" ctx=%08X:", ctx);
            for (int i = 0; i < 6; i++) psp_log(" %08X", psp_read32(ctx + (uint32_t)i * 4));
            psp_log(" | %08X %08X %08X", psp_read32(ctx + 32), psp_read32(ctx + 48), psp_read32(ctx + 64));
        }
        psp_log_putc('\n');
    }
    if (g_rb_addr) {
        psp_write32(g_rb_addr + 8, psp_read32(g_rb_addr + 8) + (uint32_t)total);
        rb_sync_struct();
    }
    psp_ret((uint32_t)total);
}

static void hle_MpegCreate(void) {
    uint32_t mpeg = psp_arg(0);
    uint32_t data = psp_arg(1);
    if (mpeg) psp_write32(mpeg, data ? data : mpeg);
    if (mpeg_trace()) psp_log("mpeg[%llu]: Create mpeg=0x%08X data=0x%08X\n",
                              (unsigned long long)psp_display_vblanks(), mpeg, data);
    mpeg_reset_streams();
    g_have_vid_stream = g_have_aud_stream = 0;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegDelete(void) {
    if (mpeg_trace()) psp_log("mpeg[%llu]: Delete vid=%zu aud=%zu\n",
                              (unsigned long long)psp_display_vblanks(), g_vid.len, g_aud.len);
    mpeg_reset_streams();
    g_have_vid_stream = g_have_aud_stream = 0;
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegRegistStream(void) {
    uint32_t type = psp_arg(1);
    if (type == 0) {
        /* A new video stream starts a new PSMF: rewind the raw read-ahead.
         * The opening registers its stream before the feeder runs, so this
         * does not disturb the read-ahead into the next stream. */
        g_raw_demuxed = 0;
        g_sig_have = 0;
    }
    if (type == 0) g_have_vid_stream = 1;
    if (type == 1) g_have_aud_stream = 1;
    psp_ret(type + 1u);
}

static void hle_MpegMallocAvcEsBuf(void) { psp_ret(1u); }

static void hle_MpegQueryAtracEsSize(void) {
    uint32_t out_es  = psp_arg(1);
    uint32_t out_pcm = psp_arg(2);
    if (out_es)  psp_write32(out_es, 2112u);
    if (out_pcm) psp_write32(out_pcm, 8192u);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void au_write(uint32_t au, uint64_t pts, uint64_t dts, uint32_t es_size) {
    if (!au) return;
    psp_write32(au +  0, (uint32_t)(pts >> 32));
    psp_write32(au +  4, (uint32_t)pts);
    psp_write32(au +  8, (uint32_t)(dts >> 32));
    psp_write32(au + 12, (uint32_t)dts);
    psp_write32(au + 16, 0);
    psp_write32(au + 20, es_size);
}

static void hle_MpegInitAu(void) {
    au_write(psp_arg(2), (uint64_t)-1, (uint64_t)-1, 0);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* Index of the next Annex-B AUD (NAL type 9) at or after `from`, or -1. */
static long find_aud(const uint8_t *d, size_t n, size_t from) {
    for (size_t i = from; i + 4 < n; i++) {
        if (d[i] == 0 && d[i + 1] == 0) {
            if (d[i + 2] == 1 && (d[i + 3] & 0x1f) == 9) return (long)i;
            if (d[i + 2] == 0 && d[i + 3] == 1 && i + 4 < n && (d[i + 4] & 0x1f) == 9) return (long)i;
        }
    }
    return -1;
}

/* Find any Annex-B NAL start code (00 00 01 or 00 00 00 01) at or after `from`. */
static long find_nal_prefix(const uint8_t *d, size_t n, size_t from, uint8_t *out_type) {
    for (size_t i = from; i + 3 < n; i++) {
        if (d[i] == 0 && d[i + 1] == 0) {
            if (d[i + 2] == 1) {
                if (out_type) *out_type = d[i + 3] & 0x1f;
                return (long)i;
            }
            if (d[i + 2] == 0 && i + 4 < n && d[i + 3] == 1) {
                if (out_type) *out_type = d[i + 4] & 0x1f;
                return (long)i;
            }
        }
    }
    return -1;
}

static void hle_MpegGetAvcAu(void) {
    uint32_t au       = psp_arg(2);
    uint32_t out_attr = psp_arg(3);
    if (out_attr) psp_write32(out_attr, 0);

    uint8_t *d = fifo_ptr(&g_vid);
    size_t n = g_vid.len;
    if (mpeg_trace()) psp_log("mpeg[%llu]: GetAvcAu n=%zu vts=%d eof=%d uid=%u\n",
                              (unsigned long long)psp_display_vblanks(), n, g_vts.n, g_rb_eof,
                              psp_threadman_current_uid());
    if (n == 0) { psp_ret(SCE_MPEG_ERROR_NO_DATA); return; }

    /* 1. Find start of AU (prefer AUD 9, fallback to SPS 7, PPS 8, IDR 5, slice 1) */
    long s = find_aud(d, n, 0);
    if (s < 0) {
        uint8_t t = 0;
        s = find_nal_prefix(d, n, 0, &t);
    }
    if (s < 0) { psp_ret(SCE_MPEG_ERROR_NO_DATA); return; }
    if (s > 0) {
        fifo_pop(&g_vid, (size_t)s);
        g_vid_consumed += (uint64_t)s;
        d = fifo_ptr(&g_vid);
        n = g_vid.len;
    }

    /* 2. Find end of AU:
     *    a) Next AUD (NAL 9)
     *    b) Next PES packet boundary with PTS in g_vts
     *    c) Next SPS/AUD start code
     *    d) EOF or backlog available without deadlocking */
    long e = find_aud(d, n, 4);
    if (e < 0 && g_vts.n >= 2 && g_vts.e[1].off > g_vid_consumed) {
        size_t pes_next = (size_t)(g_vts.e[1].off - g_vid_consumed);
        if (pes_next <= n) e = (long)pes_next;
    }
    if (e < 0) {
        for (size_t i = 4; i + 4 < n; i++) {
            uint8_t t = 0;
            long pos = find_nal_prefix(d, n, i, &t);
            if (pos < 0) break;
            if (t == 9 || t == 7) { e = pos; break; }
            i = (size_t)pos + 3;
        }
    }

    size_t len;
    if (e >= 0) len = (size_t)e;
    else if (g_rb_eof && n) len = n;                   /* last AU of the stream */
    else if (g_stream_size && g_raw_demuxed >= g_stream_size && n)
        len = n;                                       /* stream ended on a partial AU */
    else if (n >= 16384 || rb_free_pkts() == 0) len = n; /* avoid circular deadlock */
    else { psp_ret(SCE_MPEG_ERROR_NO_DATA); return; }

    if (len > g_au_cap) {
        uint8_t *nb = realloc(g_au_buf, len);
        if (!nb) { psp_ret(SCE_MPEG_ERROR_NO_DATA); return; }
        g_au_buf = nb; g_au_cap = len;
    }
    memcpy(g_au_buf, d, len);
    g_au_len = len; g_au_valid = 1;
    uint64_t pts = 0, dts = 0, out_dts = 0;
    ts_take(&g_vts, g_vid_consumed, &pts, &dts);
    uint64_t vpts = ts_interp_next(&g_vts_i, pts, dts, 3003, 1, &out_dts);
    if (mpeg_trace()) psp_log("mpeg[%llu]: AvcAu pts=%llu len=%zu\n",
                              (unsigned long long)psp_display_vblanks(),
                              (unsigned long long)vpts, len);
    fifo_pop(&g_vid, len);
    g_vid_consumed += len;
    rb_sync_struct();
    au_write(au, vpts, out_dts, (uint32_t)len);
    if (mpeg_trace()) psp_log("mpeg[%llu]: GetAvcAu AU len=%zu pts=%llu dts=%llu s=%ld e=%ld\n",
                              (unsigned long long)psp_display_vblanks(), len,
                              (unsigned long long)vpts, (unsigned long long)out_dts, s, e);
    if (mpeg_trace() && len <= 64) {
        static int c;
        if (c++ < 6) {
            psp_log("mpeg: small AU bytes:");
            for (size_t i = 0; i < len && i < 64; i++) psp_log(" %02x", g_au_buf[i]);
            psp_log_putc('\n');
        }
    }
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegGetAtracAu(void) {
    uint32_t au       = psp_arg(2);
    uint32_t out_attr = psp_arg(3);
    if (out_attr) psp_write32(out_attr, 0);
    g_atrac_read = 1;

    /* Resync on 0F D0, then take one whole frame: 8-byte header + 744 bytes. */
    for (;;) {
        if (g_aud.len < 2) { psp_ret(SCE_MPEG_ERROR_NO_DATA); return; }
        uint8_t *d = fifo_ptr(&g_aud);
        if (d[0] == 0x0F && d[1] == 0xD0) break;
        fifo_pop(&g_aud, 1); g_aud_consumed++;
    }
    if (g_aud.len < AT3_AU_HDR + AT3_BLOCK) {
        if (mpeg_trace()) psp_log("mpeg[%llu]: GetAtracAu NO_DATA aud=%zu eof=%d\n",
                                  (unsigned long long)psp_display_vblanks(), g_aud.len, g_rb_eof);
        psp_ret(SCE_MPEG_ERROR_NO_DATA); return;
    }
    memcpy(g_afr, fifo_ptr(&g_aud), AT3_AU_HDR + AT3_BLOCK);
    g_afr_valid = 1;
    uint64_t pts = 0, dts = 0, out_dts = 0;
    ts_take(&g_ats, g_aud_consumed, &pts, &dts);
    uint64_t apts = ts_interp_next(&g_ats_i, pts, dts,
                                   2048ull * 90000ull, 44100ull, &out_dts);
    fifo_pop(&g_aud, AT3_AU_HDR + AT3_BLOCK);
    g_aud_consumed += AT3_AU_HDR + AT3_BLOCK;
    rb_sync_struct();
    au_write(au, apts, out_dts, AT3_BLOCK);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegFlushAllStream(void) {
    if (mpeg_trace()) psp_log("mpeg[%llu]: FlushAllStream vid=%zu aud=%zu\n",
                              (unsigned long long)psp_display_vblanks(), g_vid.len, g_aud.len);
    mpeg_reset_streams();
    rb_sync_struct();
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegAvcDecodeDetail(void) {
    uint32_t detail = psp_arg(1);
    if (detail) {
        int w = g_avc ? avc_width(g_avc) : 480;
        int h = g_avc ? avc_height(g_avc) : 272;
        if (w <= 0) w = 480;
        if (h <= 0) h = 272;
        psp_write32(detail +  0, 0);
        psp_write32(detail +  4, 1);
        psp_write32(detail +  8, (uint32_t)w);
        psp_write32(detail + 12, (uint32_t)h);
        psp_write32(detail + 16, 0);
        psp_write32(detail + 20, 0);
        psp_write32(detail + 24, 0);
        psp_write32(detail + 28, 0);
    }
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegAvcDecodeYCbCr(void) {
    uint32_t init_flag = psp_arg(3);
    if (!g_avc) g_avc = avc_open();
    int got = 0;
    if (g_avc && g_au_valid) {
        got = avc_decode(g_avc, g_au_buf, (int)g_au_len);
        g_au_valid = 0;
    }
    if (av_trace()) {
        static int c;
        if (c++ < 30) psp_log("av: avc decode got=%d (%dx%d)\n", got, avc_width(g_avc), avc_height(g_avc));
    }
    if (mpeg_trace() && got <= 0)
        psp_log("mpeg[%llu]: AvcDecodeYCbCr got=%d au_len=%zu au_valid=%d\n",
                (unsigned long long)psp_display_vblanks(), got, g_au_len, g_au_valid);
    if (init_flag) psp_write32(init_flag, 1u);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegAvcDecodeStopYCbCr(void) {
    uint32_t out_status = psp_arg(2);
    if (out_status) psp_write32(out_status, 0);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegAvcQueryYCbCrSize(void) {
    int32_t  w        = (int32_t)psp_arg(2);
    int32_t  h        = (int32_t)psp_arg(3);
    uint32_t out_size = psp_arg(4);
    uint32_t sz       = (uint32_t)(((w + 15) & ~15) * ((h + 15) & ~15) * 3 / 2 + 128);
    if (out_size) psp_write32(out_size, sz);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

/* sceMpegAvcCsc(mpeg, source, range, frameWidth, dest) */
static void hle_MpegAvcCsc(void) {
    uint32_t range  = psp_arg(2);
    uint32_t stride = psp_arg(3);
    uint32_t dest   = psp_arg(4);
    if (stride == 0 || stride > 1024) stride = 512;
    int x = 0, y = 0, w = 480, h = 272;
    if (range) {
        x = (int)psp_read32(range + 0); y = (int)psp_read32(range + 4);
        w = (int)psp_read32(range + 8); h = (int)psp_read32(range + 12);
        if (w > 0 && w <= 64) w *= 16;
        if (h > 0 && h <= 64) h *= 16;
        if (w <= 0 || w > 480 || h <= 0 || h > 272 || x < 0 || y < 0) { x = 0; y = 0; w = 480; h = 272; }
    }
    uint32_t *px = (uint32_t *)psp_mem_ptr(dest, stride * (uint32_t)h * 4u);
    int rc = -1;
    if (px) {
        rc = avc_convert(g_avc, px, (int)stride, x, y, w, h);
        if (rc != 0)
            memset(px, 0, stride * (uint32_t)h * 4u);
    }
    if (av_trace()) {
        static int c;
        if (c++ < 30) psp_log("av: avc csc dest=0x%08X stride=%u range=%dx%d rc=%d\n", dest, stride, w, h, rc);
    }
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_MpegAtracDecode(void) {
    uint32_t out_buf  = psp_arg(2);
    uint32_t init_flg = psp_arg(3);
    int16_t *dst      = (int16_t *)psp_mem_ptr(out_buf, 8192);
    int n = 0;
    if (dst && g_afr_valid) {
        if (!g_mpeg_at3) g_mpeg_at3 = at3_open(1, 2, 44100, (int)AT3_BLOCK, NULL, 0);
        n = at3_decode(g_mpeg_at3, g_afr + AT3_AU_HDR, (int)AT3_BLOCK, dst, 2048);
        g_afr_valid = 0;
    }
    if (dst && n < 2048) memset(dst + (size_t)n * 2, 0, (size_t)(2048 - n) * 4);
    if (av_trace()) {
        static int c;
        if (c++ < 10) psp_log("av: mpeg atrac decode n=%d\n", n);
    }
    if (init_flg) psp_write32(init_flg, 1u);
    psp_ret(SCE_KERNEL_ERROR_OK);
}

static void hle_UmdCheckMedium(void) {
    psp_ret(1u);
}

void djmax_hle_register(void) {
    /* SysMemUserForUser */
    psp_hle_register(0x1B4217BC, "SysMemUserForUser", "sceKernelSetCompiledSdkVersion603_605", hle_ok);

    /* Kernel_Library */
    psp_hle_register(0xA089ECA4, "Kernel_Library", "sceKernelMemset",         hle_KernelMemset);
    psp_hle_register(0x1839852A, "Kernel_Library", "sceKernelMemcpy",         hle_KernelMemcpy);
    psp_hle_register(0xFA835CDE, "Kernel_Library", "sceKernelTryLockLwMutex", hle_ok);

    /* sceUmdUser */
    psp_hle_register(0xC6183D47, "sceUmdUser", "sceUmdActivate",               hle_UmdActivate);
    psp_hle_register(0xE83742BA, "sceUmdUser", "sceUmdDeactivate",             hle_ok);
    psp_hle_register(0x46EBB729, "sceUmdUser", "sceUmdCheckMedium",            hle_UmdCheckMedium);
    psp_hle_register(0x8EF08FCE, "sceUmdUser", "sceUmdWaitDriveStat",          hle_ok);
    psp_hle_register(0x4A9E5E29, "sceUmdUser", "sceUmdWaitDriveStatCB",        hle_ok);
    psp_hle_register(0x6B4A146C, "sceUmdUser", "sceUmdGetDriveStat",           hle_UmdGetDriveStat);
    psp_hle_register(0x56202973, "sceUmdUser", "sceUmdWaitDriveStatWithTimer", hle_ok);
    psp_hle_register(0x6AF9B50A, "sceUmdUser", "sceUmdCancelWaitDriveStat",    hle_ok);
    psp_hle_register(0xAEE7404D, "sceUmdUser", "sceUmdRegisterUMDCallBack",    hle_UmdRegisterUMDCallBack);

    /* scePower (clock setup) */
    psp_hle_register_unnamed(0x469989AD, "scePower", hle_ok);

    /* sceImpose */
    psp_hle_register(0x36AA6E91, "sceImpose", "sceImposeSetLanguageMode", hle_ok);

    /* sceDisplay */
    psp_hle_register(0xDBA6C4C4, "sceDisplay", "sceDisplayGetFramePerSec", hle_DisplayGetFramePerSec);

    /* sceRtc */
    psp_hle_register(0x4CFA57B0, "sceRtc", "sceRtcGetCurrentClock",          hle_RtcGetCurrentClock);
    psp_hle_register(0xE7C27D1B, "sceRtc", "sceRtcGetCurrentClockLocalTime", hle_RtcGetCurrentClockLocalTime);

    /* sceUtility */
    psp_hle_register(0xA5DA2406, "sceUtility", "sceUtilityGetSystemParamInt",            hle_UtilityGetSystemParamInt);
    psp_hle_register(0x2A2B3DE0, "sceUtility", "sceUtilityLoadModule",                   hle_ok);
    psp_hle_register(0xE49BFE92, "sceUtility", "sceUtilityUnloadModule",                 hle_ok);
    psp_hle_register(0x50C4CD57, "sceUtility", "sceUtilitySavedataInitStart",            hle_SavedataInitStart);
    psp_hle_register(0xD4B95FFB, "sceUtility", "sceUtilitySavedataUpdate",               hle_SavedataUpdate);
    psp_hle_register(0x9790B33C, "sceUtility", "sceUtilitySavedataShutdownStart",        hle_SavedataShutdownStart);
    psp_hle_register(0x8874DBE0, "sceUtility", "sceUtilitySavedataGetStatus",            hle_SavedataGetStatus);
    psp_hle_register(0x2AD8E239, "sceUtility", "sceUtilityMsgDialogInitStart",           hle_MsgDialogInitStart);
    psp_hle_register(0x95FC253B, "sceUtility", "sceUtilityMsgDialogUpdate",              hle_MsgDialogUpdate);
    psp_hle_register(0x67AF3428, "sceUtility", "sceUtilityMsgDialogShutdownStart",       hle_MsgDialogShutdownStart);
    psp_hle_register(0x9A1C91D7, "sceUtility", "sceUtilityMsgDialogGetStatus",           hle_MsgDialogGetStatus);
    psp_hle_register(0x24AC31EB, "sceUtility", "sceUtilityGamedataInstallInitStart",     hle_InstallInitStart);
    psp_hle_register(0x4AECD179, "sceUtility", "sceUtilityGamedataInstallUpdate",        hle_InstallUpdate);
    psp_hle_register(0x32E32DCB, "sceUtility", "sceUtilityGamedataInstallShutdownStart", hle_InstallShutdownStart);
    psp_hle_register(0xB57E95D9, "sceUtility", "sceUtilityGamedataInstallGetStatus",     hle_InstallGetStatus);
    psp_hle_register(0x180F7B62, "sceUtility", "sceUtilityGamedataInstallAbort",         hle_ok);

    /* sceVaudio */
    psp_hle_register(0x03B6807D, "sceVaudio", "sceVaudioChReserve",      hle_VaudioChReserve);
    psp_hle_register(0x67585DFD, "sceVaudio", "sceVaudioChRelease",      hle_VaudioChRelease);
    psp_hle_register(0x8986295E, "sceVaudio", "sceVaudioOutputBlocking", hle_VaudioOutputBlocking);
    psp_hle_register(0x346FBE94, "sceVaudio", "sceVaudioSetEffectType",  hle_ok);

    /* sceSasCore */
    psp_hle_register_unnamed(0xE175EF66, "sceSasCore", hle_ok);

    /* sceAtrac3plus */
    psp_hle_register(0x7A20E7AF, "sceAtrac3plus", "sceAtracSetDataAndGetID",          hle_AtracSetDataAndGetID);
    psp_hle_register(0x61EB33F5, "sceAtrac3plus", "sceAtracReleaseAtracID",           hle_AtracReleaseAtracID);
    psp_hle_register(0x6A8C3CD5, "sceAtrac3plus", "sceAtracDecodeData",               hle_AtracDecodeData);
    psp_hle_register(0x9AE849A7, "sceAtrac3plus", "sceAtracGetRemainFrame",           hle_AtracGetRemainFrame);
    psp_hle_register(0x5D268707, "sceAtrac3plus", "sceAtracGetStreamDataInfo",        hle_AtracGetStreamDataInfo);
    psp_hle_register(0x7DB31251, "sceAtrac3plus", "sceAtracAddStreamData",            hle_AtracAddStreamData);
    psp_hle_register(0x83E85EA0, "sceAtrac3plus", "sceAtracGetSecondBufferInfo",      hle_AtracGetSecondBufferInfo);
    psp_hle_register(0x83BF7AFD, "sceAtrac3plus", "sceAtracSetSecondBuffer",          hle_ok);
    psp_hle_register(0xE23E3A35, "sceAtrac3plus", "sceAtracGetNextDecodePosition",    hle_AtracGetNextDecodePosition);
    psp_hle_register(0xA2BBA8BE, "sceAtrac3plus", "sceAtracGetSoundSample",           hle_AtracGetSoundSample);
    psp_hle_register(0x31668BAA, "sceAtrac3plus", "sceAtracGetChannel",               hle_AtracGetChannel);
    psp_hle_register(0xD6A5F2F7, "sceAtrac3plus", "sceAtracGetMaxSample",             hle_AtracGetMaxSample);
    psp_hle_register(0x36FAABFB, "sceAtrac3plus", "sceAtracGetNextSample",            hle_AtracGetNextSample);
    psp_hle_register(0x868120B5, "sceAtrac3plus", "sceAtracSetLoopNum",               hle_AtracSetLoopNum);
    psp_hle_register(0xCA3CA3D2, "sceAtrac3plus", "sceAtracGetBufferInfoForReseting", hle_ok);
    psp_hle_register(0x2DD3E298, "sceAtrac3plus", "sceAtracGetBufferInfoForResetting",hle_ok);
    psp_hle_register(0x644E5607, "sceAtrac3plus", "sceAtracResetPlayPosition",        hle_AtracResetPlayPosition);
    psp_hle_register(0xE88F759B, "sceAtrac3plus", "sceAtracGetInternalErrorInfo",     hle_ok);

    /* sceMpeg */
    psp_hle_register(0x682A619B, "sceMpeg", "sceMpegInit",                    hle_ok);
    psp_hle_register(0x874624D6, "sceMpeg", "sceMpegFinish",                  hle_ok);
    psp_hle_register(0x21FF80E4, "sceMpeg", "sceMpegQueryStreamOffset",       hle_MpegQueryStreamOffset);
    psp_hle_register(0x611E9E11, "sceMpeg", "sceMpegQueryStreamSize",         hle_MpegQueryStreamSize);
    psp_hle_register(0xC132E22F, "sceMpeg", "sceMpegQueryMemSize",            hle_MpegQueryMemSize);
    psp_hle_register(0xD7A29F46, "sceMpeg", "sceMpegRingbufferQueryMemSize",  hle_MpegRingbufferQueryMemSize);
    psp_hle_register(0x37295ED8, "sceMpeg", "sceMpegRingbufferConstruct",     hle_MpegRingbufferConstruct);
    psp_hle_register(0x13407F13, "sceMpeg", "sceMpegRingbufferDestruct",      hle_MpegRingbufferDestruct);
    psp_hle_register(0xB5F6DC87, "sceMpeg", "sceMpegRingbufferAvailableSize", hle_MpegRingbufferAvailableSize);
    psp_hle_register(0xB656A69E, "sceMpeg", "sceMpegRingbufferAvailableSize2",hle_MpegRingbufferAvailableSize);
    psp_hle_register(0xB240A59E, "sceMpeg", "sceMpegRingbufferPut",           hle_MpegRingbufferPut);
    psp_hle_register(0xD8C5F121, "sceMpeg", "sceMpegCreate",                  hle_MpegCreate);
    psp_hle_register(0x606A4649, "sceMpeg", "sceMpegDelete",                  hle_MpegDelete);
    psp_hle_register(0x42560F23, "sceMpeg", "sceMpegRegistStream",            hle_MpegRegistStream);
    psp_hle_register(0x591A4AA2, "sceMpeg", "sceMpegUnRegistStream",          hle_ok);
    psp_hle_register(0xA780CF7E, "sceMpeg", "sceMpegMallocAvcEsBuf",          hle_MpegMallocAvcEsBuf);
    psp_hle_register(0xCEB870B1, "sceMpeg", "sceMpegFreeAvcEsBuf",            hle_ok);
    psp_hle_register(0xF8DCB679, "sceMpeg", "sceMpegQueryAtracEsSize",        hle_MpegQueryAtracEsSize);
    psp_hle_register(0x167AFD9E, "sceMpeg", "sceMpegInitAu",                  hle_MpegInitAu);
    psp_hle_register(0xFE246728, "sceMpeg", "sceMpegGetAvcAu",                hle_MpegGetAvcAu);
    psp_hle_register(0xE1CE83A7, "sceMpeg", "sceMpegGetAtracAu",              hle_MpegGetAtracAu);
    psp_hle_register(0x707B7629, "sceMpeg", "sceMpegFlushAllStream",          hle_MpegFlushAllStream);
    psp_hle_register(0xA11C7026, "sceMpeg", "sceMpegAvcDecodeMode",           hle_ok);
    psp_hle_register(0x0F6C18D7, "sceMpeg", "sceMpegAvcDecodeDetail",         hle_MpegAvcDecodeDetail);
    psp_hle_register(0x0E3C2E9D, "sceMpeg", "sceMpegAvcDecode",               hle_ok);
    psp_hle_register(0x740FCCD1, "sceMpeg", "sceMpegAvcDecodeStop",           hle_ok);
    psp_hle_register(0x4571CC64, "sceMpeg", "sceMpegAvcDecodeFlush",          hle_ok);
    psp_hle_register(0xF0EB1125, "sceMpeg", "sceMpegAvcDecodeYCbCr",          hle_MpegAvcDecodeYCbCr);
    psp_hle_register(0xF2930C9C, "sceMpeg", "sceMpegAvcDecodeStopYCbCr",      hle_MpegAvcDecodeStopYCbCr);
    psp_hle_register(0x211A057C, "sceMpeg", "sceMpegAvcQueryYCbCrSize",       hle_MpegAvcQueryYCbCrSize);
    psp_hle_register(0x67179B1B, "sceMpeg", "sceMpegAvcInitYCbCr",            hle_ok);
    psp_hle_register(0x31BD0272, "sceMpeg", "sceMpegAvcCsc",                  hle_MpegAvcCsc);
    psp_hle_register(0xD2C782E9, "sceMpeg", "sceMpegAvcCscMode",              hle_MpegAvcCsc);
    psp_hle_register(0x800C44DF, "sceMpeg", "sceMpegAtracDecode",             hle_MpegAtracDecode);
}
