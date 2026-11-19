/* Small host differences that do not deserve #ifdefs in the game code. */
#ifndef EMOTIONALSENSE_HOST_COMPAT_H
#define EMOTIONALSENSE_HOST_COMPAT_H

#include <stdio.h>
#include <stdlib.h>

#ifdef _WIN32
#  include <direct.h>
#  include <windows.h>

static void host_mkdir(const char *p) { _mkdir(p); }

static double host_monotonic_seconds(void) {
    static LARGE_INTEGER freq;
    LARGE_INTEGER now;
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&now);
    return (double)now.QuadPart / (double)freq.QuadPart;
}

static void host_default_data_dir(char *out, size_t cap) {
    const char *appdata = getenv("APPDATA");
    snprintf(out, cap, "%s\\emotionalsense", appdata ? appdata : ".");
}

/* _Exit still runs ExitProcess, which waits on DLL detach (SDL2's audio thread
 * blocks it under wine). TerminateProcess is the Windows "leave now". */
static void host_exit_now(int code) {
    TerminateProcess(GetCurrentProcess(), (UINT)code);
    for (;;) { }
}

static void host_stdio_init(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
}
#else
#  include <sys/stat.h>
#  include <time.h>

static void host_mkdir(const char *p) { mkdir(p, 0755); }

static double host_monotonic_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + ts.tv_nsec / 1e9;
}

static void host_default_data_dir(char *out, size_t cap) {
    const char *xdg = getenv("XDG_DATA_HOME");
    if (xdg && *xdg) { snprintf(out, cap, "%s/emotionalsense", xdg); return; }
    const char *home = getenv("HOME");
    snprintf(out, cap, "%s/.local/share/emotionalsense", home ? home : ".");
}

static void host_exit_now(int code) { _Exit(code); }
static void host_stdio_init(void) { }
#endif

#endif
