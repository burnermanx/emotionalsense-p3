/* Crash and hang reports -- see crash.h. */

#if !defined(_WIN32) && !defined(_GNU_SOURCE)
#  define _GNU_SOURCE
#endif

#include "crash.h"
#include "crash_scrub.h"

#include <psprecomp/cpu.h>
#include <psprecomp/dispatch.h>
#include <psprecomp/hle.h>
#include <psprecomp/log.h>
#include <psprecomp/render.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "version.h"

#ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#  include <fcntl.h>
#  include <io.h>
#  include <signal.h>
#else
#  ifndef _GNU_SOURCE
#    define _GNU_SOURCE
#  endif
#  include <errno.h>
#  include <fcntl.h>
#  include <link.h>
#  include <pthread.h>
#  include <signal.h>
#  include <sys/ioctl.h>
#  include <sys/stat.h>
#  include <sys/utsname.h>
#  include <ucontext.h>
#  include <unistd.h>
#  if defined(__GLIBC__)
#    include <execinfo.h>
#  endif
#endif

#define LOG_LINES 200
#define LOG_COLS  240
#define HLE_ENTRIES 200
#define REPORT_MAX (160 * 1024)

/* ---- state, all static so a handler never allocates ---------------------------------------- */

static char g_dir[1024];
static char g_home[512];
static char g_gpu[160];
static char g_log[LOG_LINES][LOG_COLS];
static volatile unsigned g_log_n;
static struct { uint32_t nid, ra; const char *name; uint64_t vb; } g_hle[HLE_ENTRIES];
static volatile unsigned g_hle_n;
static char g_rep[REPORT_MAX];
static size_t g_rep_len;
static volatile int g_busy;
static int g_tee_on;

/* ---- formatting without stdio -------------------------------------------------------------- */

static void ap_n(const char *s, size_t n) {
    if (g_rep_len + 1 >= sizeof g_rep) return;
    if (g_rep_len + n >= sizeof g_rep) n = sizeof g_rep - g_rep_len - 1;
    memcpy(g_rep + g_rep_len, s, n);
    g_rep_len += n;
}
static void ap_s(const char *s) { ap_n(s, strlen(s)); }
static void ap_c(char c) { ap_n(&c, 1); }
static void ap_u(uint64_t v) {
    char t[24]; int i = 0;
    do { t[i++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (i) ap_c(t[--i]);
}
static void ap_x(uint64_t v, int digits) {
    static const char hx[] = "0123456789abcdef";
    ap_n("0x", 2);
    for (int i = digits - 1; i >= 0; i--) ap_c(hx[(v >> (4 * i)) & 15]);
}
static void ap_scrub_emit(char c, void *ctx) { (void)ctx; ap_c(c); }
/* Text with the user's name/home folder replaced by ~ and control characters dropped. */
static void ap_scrub(const char *s, size_t n) { es_scrub(s, n, g_home, ap_scrub_emit, NULL); }
static const char *base_name(const char *p) {
    const char *b = p;
    for (; *p; p++) if (*p == '/' || *p == '\\') b = p + 1;
    return b;
}

static void ap_time(void) {
    time_t t = time(NULL);
    long days = (long)(t / 86400), rem = (long)(t % 86400);
    long z = days + 719468, era = (z >= 0 ? z : z - 146096) / 146097, doe = z - era * 146097;
    long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365, y = yoe + era * 400;
    long doy = doe - (365 * yoe + yoe / 4 - yoe / 100), mp = (5 * doy + 2) / 153;
    long d = doy - (153 * mp + 2) / 5 + 1, m = mp < 10 ? mp + 3 : mp - 9;
    if (m <= 2) y++;
    char b[32];
    b[0] = (char)('0' + y / 1000 % 10); b[1] = (char)('0' + y / 100 % 10); b[2] = (char)('0' + y / 10 % 10);
    b[3] = (char)('0' + y % 10); b[4] = '-'; b[5] = (char)('0' + m / 10); b[6] = (char)('0' + m % 10);
    b[7] = '-'; b[8] = (char)('0' + d / 10); b[9] = (char)('0' + d % 10); b[10] = ' ';
    b[11] = (char)('0' + rem / 36000 % 10); b[12] = (char)('0' + rem / 3600 % 10); b[13] = ':';
    b[14] = (char)('0' + rem / 600 % 6); b[15] = (char)('0' + rem / 60 % 10); b[16] = ':';
    b[17] = (char)('0' + rem / 10 % 6); b[18] = (char)('0' + rem % 10);
    ap_n(b, 19);
    ap_s(" UTC");
}

/* ---- modules (build-id) --------------------------------------------------------------------- */

typedef struct { char path[256]; uintptr_t base, lo, hi; char id[72]; } mod_t;
static mod_t g_mod[24];
static int g_nmod;

static const mod_t *mod_of(uintptr_t a) {
    for (int i = 0; i < g_nmod; i++)
        if (a >= g_mod[i].lo && a < g_mod[i].hi) return &g_mod[i];
    return NULL;
}

#ifndef _WIN32
static int phdr_cb(struct dl_phdr_info *info, size_t sz, void *u) {
    (void)sz; (void)u;
    if (g_nmod >= (int)(sizeof g_mod / sizeof g_mod[0])) return 1;
    mod_t *m = &g_mod[g_nmod];
    memset(m, 0, sizeof *m);
    m->base = (uintptr_t)info->dlpi_addr;
    m->lo = (uintptr_t)-1;
    for (int i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr) *p = &info->dlpi_phdr[i];
        if (p->p_type == PT_LOAD) {
            uintptr_t lo = m->base + p->p_vaddr, hi = lo + p->p_memsz;
            if (lo < m->lo) m->lo = lo;
            if (hi > m->hi) m->hi = hi;
        } else if (p->p_type == PT_NOTE) {
            const unsigned char *q = (const unsigned char *)(m->base + p->p_vaddr), *e = q + p->p_memsz;
            while (q + 12 <= e) {
                uint32_t nsz, dsz, ty;
                memcpy(&nsz, q, 4); memcpy(&dsz, q + 4, 4); memcpy(&ty, q + 8, 4);
                const unsigned char *name = q + 12, *desc = name + ((nsz + 3) & ~3u);
                if (ty == 3 && nsz == 4 && !memcmp(name, "GNU", 4) && dsz <= 32) {
                    static const char hx[] = "0123456789abcdef";
                    for (uint32_t k = 0; k < dsz; k++) {
                        m->id[2 * k] = hx[desc[k] >> 4];
                        m->id[2 * k + 1] = hx[desc[k] & 15];
                    }
                }
                q = desc + ((dsz + 3) & ~3u);
            }
        }
    }
    if (m->lo == (uintptr_t)-1) return 0;
    if (info->dlpi_name && *info->dlpi_name) snprintf(m->path, sizeof m->path, "%s", info->dlpi_name);
    else {
        ssize_t n = readlink("/proc/self/exe", m->path, sizeof m->path - 1);
        m->path[n > 0 ? n : 0] = 0;
    }
    g_nmod++;
    return 0;
}
#endif

/* ---- the report ---------------------------------------------------------------------------- */

static const char *const k_env[] = {
    "EMOTIONALSENSE_FEVER", "EMOTIONALSENSE_SCORE", "EMOTIONALSENSE_SPEED", "EMOTIONALSENSE_CONFIRM", "EMOTIONALSENSE_UPDATE",
    "EMOTIONALSENSE_UNLOCK", "EMOTIONALSENSE_UNLOCK_ITEMS", "EMOTIONALSENSE_INTERNAL_SCALE",
    "EMOTIONALSENSE_RENDERER", "EMOTIONALSENSE_FULLSCREEN", "EMOTIONALSENSE_REFRESH",
    "EMOTIONALSENSE_LANG", "EMOTIONALSENSE_BGA_SMOOTH", "EMOTIONALSENSE_KEY_TRIM", NULL };

static void ap_kv(const char *k, const char *v) { ap_s(k); ap_s(": "); ap_s(v); ap_c('\n'); }

static void report_body(const char *kind, const char *detail, uintptr_t fault_addr, uintptr_t native_pc,
                        void *const *frames, int nframes) {
    g_rep_len = 0;
    ap_s("EmotionalSense-P3 crash report\n");
    ap_kv("kind", kind);
    if (detail && *detail) ap_kv("detail", detail);
    ap_s("time: "); ap_time(); ap_c('\n');
    ap_kv("version", EMOTIONALSENSE_VERSION_FULL);
    ap_kv("psprecomp", EMOTIONALSENSE_PSPRECOMP);
#if defined(__x86_64__) || defined(_M_X64)
    ap_kv("target", "x86_64");
#elif defined(__aarch64__) || defined(_M_ARM64)
    ap_kv("target", "aarch64");
#else
    ap_kv("target", "other");
#endif
#ifdef _WIN32
    {
        OSVERSIONINFOEXA vi; memset(&vi, 0, sizeof vi);
        typedef LONG (WINAPI *RtlGetVersionFn)(OSVERSIONINFOEXA *);
        HMODULE nt = GetModuleHandleA("ntdll.dll");
        RtlGetVersionFn f = nt ? (RtlGetVersionFn)(void *)GetProcAddress(nt, "RtlGetVersion") : NULL;
        vi.dwOSVersionInfoSize = sizeof vi;
        ap_s("os: Windows ");
        if (f && f(&vi) == 0) { ap_u(vi.dwMajorVersion); ap_c('.'); ap_u(vi.dwMinorVersion); ap_s(" build "); ap_u(vi.dwBuildNumber); }
        else ap_s("?");
        if (GetProcAddress(GetModuleHandleA("ntdll.dll"), "wine_get_version")) ap_s(" (Wine)");
        ap_c('\n');
    }
#else
    {
        struct utsname u;
        ap_s("os: ");
        if (uname(&u) == 0) { ap_s(u.sysname); ap_c(' '); ap_s(u.release); ap_c(' '); ap_s(u.machine); }
        else ap_s("?");
        ap_c('\n');
    }
#endif
    ap_s("renderer: ");
    {
        const psp_render_backend *r = psp_render_current();
        ap_s(r && r->name ? r->name : "?");
        ap_s(", internal scale "); ap_u((uint64_t)psp_render_scale);
    }
    ap_c('\n');
    ap_s("gpu: "); ap_s(g_gpu[0] ? g_gpu : "(not reported)"); ap_c('\n');
    ap_s("options:");
    for (int i = 0; k_env[i]; i++) {
        const char *v = getenv(k_env[i]);
        if (v && *v) { ap_s(" "); ap_s(k_env[i] + 15); ap_c('='); ap_scrub(v, 32); }
    }
    {
        const char *tp = getenv("EMOTIONALSENSE_TEX_PACK");
        ap_s(" texture_pack="); ap_s(tp && *tp ? "yes" : "no");
    }
    ap_c('\n');
    ap_s("scene: "); ap_u(psp_scene_now());
    ap_s("  vblank: "); ap_u(psp_display_vblanks());
    ap_s("  guest clock us: "); ap_u(psp_clock_us()); ap_c('\n');
    if (fault_addr) { ap_s("fault address: "); ap_x(fault_addr, 16); ap_c('\n'); }
    if (native_pc) {
        ap_s("native pc: ");
        ap_x(native_pc, 16);
        const mod_t *m = mod_of(native_pc);
        if (m) { ap_c(' '); ap_scrub(base_name(m->path), 80); ap_c('+'); ap_x(native_pc - m->base, 8); }
        ap_c('\n');
    }

    /* guest */
    ap_s("\n[guest]\n");
    ap_s("pc "); ap_x(psp_cpu.pc, 8); ap_s("  ra "); ap_x(psp_cpu.r[PSP_REG_RA], 8);
    ap_s("  sp "); ap_x(psp_cpu.r[PSP_REG_SP], 8); ap_s("  gp "); ap_x(psp_cpu.r[PSP_REG_GP], 8);
    ap_s("  hi "); ap_x(psp_cpu.hi, 8); ap_s("  lo "); ap_x(psp_cpu.lo, 8); ap_c('\n');
    {
        static const char *const rn[32] = { "zero","at","v0","v1","a0","a1","a2","a3","t0","t1","t2","t3",
            "t4","t5","t6","t7","s0","s1","s2","s3","s4","s5","s6","s7","t8","t9","k0","k1","gp","sp","fp","ra" };
        for (int i = 0; i < 32; i++) {
            ap_s(rn[i]); ap_c(' '); ap_x(psp_cpu.r[i], 8);
            ap_c(i % 4 == 3 ? '\n' : ' ');
        }
    }
    {
        uint32_t last = psp_trace_last();
        if (last) { ap_s("function: psp_func_"); ap_x(last, 8); ap_s(" (last traced entry)\n"); }
        else ap_s("function: unknown (tracing is off in release builds); use the return addresses below: "
                  "the function that contains the first one is psp_func_<its start>\n");
    }
    ap_s("guest call stack (return addresses, innermost first):\n");
    {
        int d = 0;
        for (const psp_frame *f = psp_cpu.frame; f && d < 32; f = f->prev, d++) {
            ap_s("  ["); ap_u((uint64_t)d); ap_s("] "); ap_x(f->ret, 8); ap_c('\n');
        }
        if (!d) ap_s("  (empty)\n");
    }

    /* last firmware calls */
    ap_s("\n[last firmware calls, oldest first]\n");
    {
        unsigned n = g_hle_n, cnt = n < HLE_ENTRIES ? n : HLE_ENTRIES;
        for (unsigned i = n - cnt; i < n; i++) {
            const typeof(g_hle[0]) *h = &g_hle[i % HLE_ENTRIES];
            ap_s("vb "); ap_u(h->vb); ap_c(' '); ap_x(h->nid, 8); ap_c(' ');
            ap_s(h->name ? h->name : "(unimplemented)"); ap_s("  ra "); ap_x(h->ra, 8); ap_c('\n');
        }
    }

    /* native backtrace */
    ap_s("\n[native backtrace]\n");
    for (int i = 0; i < nframes; i++) {
        uintptr_t a = (uintptr_t)frames[i];
        const mod_t *m = mod_of(a);
        ap_s("#"); ap_u((uint64_t)i); ap_c(' '); ap_x(a, 16);
        if (m) { ap_c(' '); ap_scrub(base_name(m->path), 80); ap_c('+'); ap_x(a - m->base, 8); }
        ap_c('\n');
    }
    if (!nframes) ap_s("(unavailable)\n");
    ap_s("modules (symbolise with the binary of the same commit: addr2line -f -e <binary> <offset>):\n");
    for (int i = 0; i < g_nmod; i++) {
        ap_s("  "); ap_scrub(base_name(g_mod[i].path), 80);
        ap_s(" base "); ap_x(g_mod[i].base, 16);
        ap_s(g_mod[i].id[0] ? " build-id " : " id ");
        ap_s(g_mod[i].id[0] ? g_mod[i].id : "(none)");
        ap_c('\n');
    }

    /* log ring */
    ap_s("\n[last log lines]\n");
    {
        unsigned n = g_log_n, cnt = n < LOG_LINES ? n : LOG_LINES;
        for (unsigned i = n - cnt; i < n; i++) {
            const char *ln = g_log[i % LOG_LINES];
            ap_scrub(ln, LOG_COLS);
            ap_c('\n');
        }
    }
}

#ifdef _WIN32
#define RENAME_BASE_SEP '\\'
#else
#define RENAME_BASE_SEP '/'
#endif

/* Writes g_rep to <dir>/crash-<stamp>[-n].txt; returns the path (static) or NULL. */
static char g_path[1200];
static const char *write_report(void) {
    char stamp[24];
    {   /* reuse ap_time's maths for the file name */
        size_t save = g_rep_len; (void)save;
        time_t t = time(NULL);
        long days = (long)(t / 86400), rem = (long)(t % 86400);
        long z = days + 719468, era = (z >= 0 ? z : z - 146096) / 146097, doe = z - era * 146097;
        long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365, y = yoe + era * 400;
        long doy = doe - (365 * yoe + yoe / 4 - yoe / 100), mp = (5 * doy + 2) / 153;
        long d = doy - (153 * mp + 2) / 5 + 1, m = mp < 10 ? mp + 3 : mp - 9;
        if (m <= 2) y++;
        int i = 0;
        stamp[i++] = (char)('0' + y / 1000 % 10); stamp[i++] = (char)('0' + y / 100 % 10);
        stamp[i++] = (char)('0' + y / 10 % 10); stamp[i++] = (char)('0' + y % 10);
        stamp[i++] = (char)('0' + m / 10); stamp[i++] = (char)('0' + m % 10);
        stamp[i++] = (char)('0' + d / 10); stamp[i++] = (char)('0' + d % 10); stamp[i++] = '-';
        stamp[i++] = (char)('0' + rem / 36000 % 10); stamp[i++] = (char)('0' + rem / 3600 % 10);
        stamp[i++] = (char)('0' + rem / 600 % 6); stamp[i++] = (char)('0' + rem / 60 % 10);
        stamp[i++] = (char)('0' + rem / 10 % 6); stamp[i++] = (char)('0' + rem % 10);
        stamp[i] = 0;
    }
    for (int n = 0; n < 50; n++) {
        char suffix[8] = "";
        if (n) { suffix[0] = '-'; suffix[1] = (char)('0' + n / 10); suffix[2] = (char)('0' + n % 10); suffix[3] = 0; }
        snprintf(g_path, sizeof g_path, "%s%ccrash-%s%s.txt", g_dir, RENAME_BASE_SEP, stamp, suffix);
#ifdef _WIN32
        HANDLE h = CreateFileA(g_path, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE) { if (GetLastError() == ERROR_FILE_EXISTS) continue; return NULL; }
        DWORD w; WriteFile(h, g_rep, (DWORD)g_rep_len, &w, NULL); CloseHandle(h);
#else
        int fd = open(g_path, O_WRONLY | O_CREAT | O_EXCL, 0644);
        if (fd < 0) { if (errno == EEXIST) continue; return NULL; }
        size_t off = 0;
        while (off < g_rep_len) {
            ssize_t w = write(fd, g_rep + off, g_rep_len - off);
            if (w <= 0) break;
            off += (size_t)w;
        }
        close(fd);
#endif
        return g_path;
    }
    return NULL;
}

static void notice(const char *path) {
    /* Tell the player where it is (stderr only; the launcher also looks in the folder). */
    const char *m1 = "\nEmotionalSense-P3: a crash report was written to ";
#ifdef _WIN32
    DWORD w; HANDLE e = GetStdHandle(STD_ERROR_HANDLE);
    if (e && e != INVALID_HANDLE_VALUE) {
        WriteFile(e, m1, (DWORD)strlen(m1), &w, NULL);
        WriteFile(e, path, (DWORD)strlen(path), &w, NULL);
        WriteFile(e, "\n", 1, &w, NULL);
    }
#else
    if (write(2, m1, strlen(m1)) < 0) {}
    if (write(2, path, strlen(path)) < 0) {}
    if (write(2, "\n", 1) < 0) {}
#endif
}

static void wait_tee(void);

/* ---- log tee ------------------------------------------------------------------------------- */

typedef struct { int rd, saved; int is_err; } tee_t;
static tee_t g_tee[2];

static void log_line(const char *s, size_t n) {
    unsigned idx = g_log_n % LOG_LINES;
    if (n >= LOG_COLS) n = LOG_COLS - 1;
    memcpy(g_log[idx], s, n);
    g_log[idx][n] = 0;
    g_log_n++;
    static const char gpu_tag[] = "render: Vulkan backend initialized on ";
    if (!strncmp(s, gpu_tag, sizeof gpu_tag - 1) && !g_gpu[0]) {
        size_t m = n - (sizeof gpu_tag - 1);
        if (m >= sizeof g_gpu) m = sizeof g_gpu - 1;
        memcpy(g_gpu, s + sizeof gpu_tag - 1, m);
        g_gpu[m] = 0;
    }
}

/* Logging off: our own diagnostic lines never reach the terminal, so the tee above does not see
 * them; psprecomp hands each complete line here to keep the report's "last log lines" filled. */
static void ring_hook(const char *s, size_t n) { log_line(s, n); }

#ifdef _WIN32
#  define TEE_READ(fd, b, n) _read(fd, b, (unsigned)(n))
#  define TEE_WRITE(fd, b, n) _write(fd, b, (unsigned)(n))
static DWORD WINAPI tee_thread(LPVOID arg)
#else
#  define TEE_READ(fd, b, n) read(fd, b, n)
#  define TEE_WRITE(fd, b, n) write(fd, b, n)
static void *tee_thread(void *arg)
#endif
{
    tee_t *t = (tee_t *)arg;
    char buf[4096], line[LOG_COLS * 2];
    size_t ll = 0;
    for (;;) {
        int n = (int)TEE_READ(t->rd, buf, sizeof buf);
        if (n <= 0) break;
        int off = 0;
        while (off < n) {
            int w = (int)TEE_WRITE(t->saved, buf + off, n - off);
            if (w <= 0) break;
            off += w;
        }
        for (int i = 0; i < n; i++) {
            if (buf[i] == '\n') { log_line(line, ll); ll = 0; }
            else if (ll < sizeof line) line[ll++] = buf[i];
        }
    }
#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

static int pipe_pending(int rd) {
#ifdef _WIN32
    DWORD avail = 0;
    HANDLE h = (HANDLE)_get_osfhandle(rd);
    if (!PeekNamedPipe(h, NULL, 0, NULL, &avail, NULL)) return 0;
    return (int)avail;
#else
    int avail = 0;
    if (ioctl(rd, FIONREAD, &avail) != 0) return 0;
    return avail;
#endif
}

static void tee_start(int fd, int idx) {
    int p[2];
#ifdef _WIN32
    if (_pipe(p, 1 << 16, _O_BINARY) != 0) return;
    g_tee[idx].saved = _dup(fd);
    if (g_tee[idx].saved < 0 || _dup2(p[1], fd) != 0) return;
    _close(p[1]);
#else
    if (pipe(p) != 0) return;
    g_tee[idx].saved = dup(fd);
    if (g_tee[idx].saved < 0 || dup2(p[1], fd) < 0) return;
    close(p[1]);
#endif
    g_tee[idx].rd = p[0];
    g_tee[idx].is_err = fd == 2;
#ifdef _WIN32
    HANDLE th = CreateThread(NULL, 0, tee_thread, &g_tee[idx], 0, NULL);
    if (th) CloseHandle(th);
    else return;
#else
    pthread_t th;
    if (pthread_create(&th, NULL, tee_thread, &g_tee[idx]) != 0) return;
    pthread_detach(th);
#endif
    g_tee_on = 1;
}

static void sleep_ms(int ms) {
#ifdef _WIN32
    Sleep((DWORD)ms);
#else
    struct timespec ts = { ms / 1000, (long)(ms % 1000) * 1000000L };
    nanosleep(&ts, NULL);
#endif
}

static void wait_tee(void) {
    if (!g_tee_on) return;
    for (int i = 0; i < 30; i++) {
        if (!pipe_pending(g_tee[0].rd) && !pipe_pending(g_tee[1].rd)) break;
        sleep_ms(10);
    }
    sleep_ms(10);
}

void es_crash_flush(void) {
    fflush(NULL);
    wait_tee();
}

/* ---- hooks --------------------------------------------------------------------------------- */

static void hle_hook(uint32_t nid, const char *name) {
    unsigned i = g_hle_n % HLE_ENTRIES;
    g_hle[i].nid = nid;
    g_hle[i].name = name;
    g_hle[i].ra = psp_cpu.r[PSP_REG_RA];
    g_hle[i].vb = psp_display_vblanks();
    g_hle_n++;
}

static void emit(const char *kind, const char *detail, uintptr_t fault, uintptr_t pc,
                 void *const *frames, int nframes) {
    wait_tee();
    report_body(kind, detail, fault, pc, frames, nframes);
    const char *p = write_report();
    if (p) notice(p);
}

#ifdef _WIN32

typedef struct { DWORD ThreadId; PEXCEPTION_POINTERS ExceptionPointers; BOOL ClientPointers; } MDEI;
typedef BOOL (WINAPI *MiniDumpWriteDumpFn)(HANDLE, DWORD, HANDLE, int, MDEI *, void *, void *);

static void write_minidump(PEXCEPTION_POINTERS ep) {
    HMODULE dh = LoadLibraryA("dbghelp.dll");
    MiniDumpWriteDumpFn fn = dh ? (MiniDumpWriteDumpFn)(void *)GetProcAddress(dh, "MiniDumpWriteDump") : NULL;
    if (!fn) return;
    char dp[1200];
    snprintf(dp, sizeof dp, "%s", g_path);
    size_t n = strlen(dp);
    if (n > 4) memcpy(dp + n - 4, ".dmp", 5);
    HANDLE h = CreateFileA(dp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    MDEI mei = { GetCurrentThreadId(), ep, FALSE };
    fn(GetCurrentProcess(), GetCurrentProcessId(), h, 0 /* MiniDumpNormal: small */, ep ? &mei : NULL, NULL, NULL);
    CloseHandle(h);
}

static void win_module_info(void) {
    g_nmod = 0;
    HMODULE exe = GetModuleHandleA(NULL);
    if (!exe) return;
    mod_t *m = &g_mod[g_nmod];
    memset(m, 0, sizeof *m);
    GetModuleFileNameA(exe, m->path, sizeof m->path);
    m->base = (uintptr_t)exe;
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)exe;
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)((const char *)exe + dos->e_lfanew);
    m->lo = m->base;
    m->hi = m->base + nt->OptionalHeader.SizeOfImage;
    /* No GNU build-id on PE: the timestamp and size identify the link. */
    snprintf(m->id, sizeof m->id, "timestamp-%08lx-size-%08lx", (unsigned long)nt->FileHeader.TimeDateStamp,
             (unsigned long)nt->OptionalHeader.SizeOfImage);
    g_nmod = 1;
}

static LONG WINAPI win_filter(PEXCEPTION_POINTERS ep) {
    if (__sync_lock_test_and_set(&g_busy, 1)) return EXCEPTION_CONTINUE_SEARCH;
    DWORD code = ep ? ep->ExceptionRecord->ExceptionCode : 0;
    char det[64];
    snprintf(det, sizeof det, "exception 0x%08lx", (unsigned long)code);
    void *frames[62];
    int nf = (int)RtlCaptureStackBackTrace(0, 62, frames, NULL);
    uintptr_t pc = 0, fa = 0;
    if (ep) {
        pc = (uintptr_t)ep->ExceptionRecord->ExceptionAddress;
        if (ep->ExceptionRecord->NumberParameters >= 2 && code == EXCEPTION_ACCESS_VIOLATION)
            fa = (uintptr_t)ep->ExceptionRecord->ExceptionInformation[1];
    }
    emit(code == EXCEPTION_ACCESS_VIOLATION ? "access-violation" : "exception", det, fa, pc, frames, nf);
    write_minidump(ep);
    es_crash_flush();
    TerminateProcess(GetCurrentProcess(), code ? (UINT)code : 3u);   /* no WER dialog, no debugger */
    return EXCEPTION_EXECUTE_HANDLER;
}

static void win_abort(int sig) {
    (void)sig;
    if (__sync_lock_test_and_set(&g_busy, 1)) return;
    void *frames[62];
    int nf = (int)RtlCaptureStackBackTrace(0, 62, frames, NULL);
    emit("abort", "SIGABRT", 0, 0, frames, nf);
    write_minidump(NULL);
    TerminateProcess(GetCurrentProcess(), 3);
}

#else  /* POSIX */

static uintptr_t uc_pc(void *uc) {
    (void)uc;
#if defined(__x86_64__)
    return uc ? (uintptr_t)((ucontext_t *)uc)->uc_mcontext.gregs[REG_RIP] : 0;
#elif defined(__aarch64__)
    return uc ? (uintptr_t)((ucontext_t *)uc)->uc_mcontext.pc : 0;
#else
    return 0;
#endif
}

static void on_signal(int sig, siginfo_t *si, void *uc) {
    if (__sync_lock_test_and_set(&g_busy, 1)) _exit(128 + sig);
    const char *kind = sig == SIGSEGV ? "segv" : sig == SIGBUS ? "bus" : sig == SIGILL ? "illegal-instruction"
                     : sig == SIGFPE ? "fpe" : "abort";
    void *frames[64];
    int nf = 0;
#if defined(__GLIBC__)
    nf = backtrace(frames, 64);
#endif
    char det[48];
    snprintf(det, sizeof det, "signal %d", sig);
    emit(kind, det, (sig == SIGSEGV || sig == SIGBUS || sig == SIGFPE || sig == SIGILL) ? (uintptr_t)si->si_addr : 0,
         uc_pc(uc), frames, nf);
    signal(sig, SIG_DFL);
    raise(sig);
}

#endif

/* The recompiler's "indirect call to a function that was not recompiled": a guest failure. */
static void guest_miss(uint32_t addr) {
    fprintf(stderr, "psprecomp: indirect call to 0x%08X (ra=0x%08X, sp=0x%08X), which is not a recompiled function.\n",
            addr, psp_cpu.r[PSP_REG_RA], psp_cpu.r[PSP_REG_SP]);
    fflush(NULL);
    if (!__sync_lock_test_and_set(&g_busy, 1)) {
        char det[64];
        snprintf(det, sizeof det, "indirect call to 0x%08x which is not a recompiled function", (unsigned)addr);
        void *frames[64];
        int nf = 0;
#if defined(__GLIBC__) && !defined(_WIN32)
        nf = backtrace(frames, 64);
#elif defined(_WIN32)
        nf = (int)RtlCaptureStackBackTrace(0, 62, frames, NULL);
#endif
        emit("guest", det, 0, 0, frames, nf);
#ifdef _WIN32
        write_minidump(NULL);
#endif
    }
    es_crash_flush();
    abort();
}

/* ---- watchdog ------------------------------------------------------------------------------ */

static int g_hang_s = 20;
static int g_test_hang;

#ifdef _WIN32
static DWORD WINAPI watchdog(LPVOID arg)
#else
static void *watchdog(void *arg)
#endif
{
    (void)arg;
    uint64_t last = psp_display_vblanks();
    int still = 0, reported = 0;
    for (;;) {
        sleep_ms(1000);
        uint64_t now = psp_display_vblanks();
        if (now != last) { last = now; still = 0; reported = 0; continue; }
        if (++still >= g_hang_s && !reported && !g_busy) {
            reported = 1;
            char det[80];
            snprintf(det, sizeof det, "no display vblank for %d s (the game was left running)", still);
            void *frames[1];
            if (!__sync_lock_test_and_set(&g_busy, 1)) {
                emit("hang", det, 0, 0, frames, 0);
                g_busy = 0;
            }
            if (g_test_hang) {
                fflush(NULL);
#ifdef _WIN32
                TerminateProcess(GetCurrentProcess(), 3);
#else
                _exit(3);
#endif
            }
        }
    }
#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

/* ---- public -------------------------------------------------------------------------------- */

void es_crash_init(const char *data) {
    const char *home =
#ifdef _WIN32
        getenv("USERPROFILE");
#else
        getenv("HOME");
#endif
    if (home && strlen(home) > 2 && strlen(home) < sizeof g_home) snprintf(g_home, sizeof g_home, "%s", home);
    snprintf(g_dir, sizeof g_dir, "%s/crashes", data);
#ifdef _WIN32
    CreateDirectoryA(g_dir, NULL);
#else
    mkdir(g_dir, 0755);
#endif
    const char *off = getenv("EMOTIONALSENSE_CRASH");
    if (off && !strcmp(off, "0")) return;

    const char *t = getenv("EMOTIONALSENSE_TEE");
    if (!(t && !strcmp(t, "0"))) {
        tee_start(1, 0);
        tee_start(2, 1);
    }
    psp_log_set_line_hook(ring_hook);
    psp_hle_trace_hook = hle_hook;
    psp_set_miss_handler(guest_miss);
    const char *hs = getenv("EMOTIONALSENSE_HANG_SECONDS");
    if (hs && atoi(hs) > 0) g_hang_s = atoi(hs);
    const char *ct = getenv("EMOTIONALSENSE_CRASH_TEST");
    if (ct && !strcmp(ct, "hang")) { g_test_hang = 1; if (!(hs && atoi(hs) > 0)) g_hang_s = 3; }

#ifdef _WIN32
    win_module_info();
    SetUnhandledExceptionFilter(win_filter);
    signal(SIGABRT, win_abort);
    _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
    HANDLE wt = CreateThread(NULL, 0, watchdog, NULL, 0, NULL);
    if (wt) CloseHandle(wt);
#else
    dl_iterate_phdr(phdr_cb, NULL);
#if defined(__GLIBC__)
    { void *warm[2]; backtrace(warm, 2); }          /* load libgcc now, not inside a handler */
#endif
    static char altstack[1 << 16];
    stack_t ss = { .ss_sp = altstack, .ss_size = sizeof altstack, .ss_flags = 0 };
    sigaltstack(&ss, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_signal;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_RESETHAND | SA_NODEFER;
    sigemptyset(&sa.sa_mask);
    int sigs[] = { SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT };
    for (unsigned i = 0; i < sizeof sigs / sizeof sigs[0]; i++) sigaction(sigs[i], &sa, NULL);
    pthread_t wt;
    if (pthread_create(&wt, NULL, watchdog, NULL) == 0) pthread_detach(wt);
#endif
}

void es_crash_tick(void) {
    static int mode = -1;
    if (mode < 0) {
        const char *e = getenv("EMOTIONALSENSE_CRASH_TEST");
        mode = !e || !*e ? 0 : !strcmp(e, "segv") ? 1 : !strcmp(e, "abort") ? 2 : !strcmp(e, "guest") ? 3
             : !strcmp(e, "hang") ? 4 : 0;
    }
    if (mode <= 0 || psp_display_vblanks() < 120) return;
    int m = mode;
    mode = 0;
    fprintf(stderr, "emotionalsense: crash test '%s'\n", m == 1 ? "segv" : m == 2 ? "abort" : m == 3 ? "guest" : "hang");
    if (m == 1) { *(volatile int *)0 = 1; }
    else if (m == 2) abort();
    else if (m == 3) psp_call_from_host(0x0DEAD000u);
    else for (;;) sleep_ms(1000);                    /* the main loop never turns: no vblank */
}
