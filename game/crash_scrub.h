/* Path scrubbing for crash reports (header-only so it can be unit tested).
 * async-signal-safe: no malloc, no libc locale, no stdio. The caller supplies the emitter. */
#ifndef ES_CRASH_SCRUB_H
#define ES_CRASH_SCRUB_H
#include <stddef.h>
#include <string.h>

static int es_scrub_sep(char c) { return c == '/' || c == '\\'; }
static char es_scrub_lower(char c) { return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c; }

/* If s[i..] is "home" or "users" (any case) followed by a separator, return the length of
 * that word plus the separator; otherwise 0. */
static size_t es_scrub_userdir(const char *s, size_t i, size_t n) {
    static const char home[] = "home", users[] = "users";
    for (int k = 0; k < 2; k++) {
        const char *w = k ? users : home;
        size_t wl = k ? 5 : 4, j = 0;
        while (j < wl && i + j < n && s[i + j] && es_scrub_lower(s[i + j]) == w[j]) j++;
        if (j == wl && i + j < n && es_scrub_sep(s[i + j])) return wl + 1;
    }
    return 0;
}

/* Emit s (at most n bytes) with: the exact home folder -> "~"; the user name after
 * <sep>home<sep> / <sep>Users<sep> -> "~"; backslashes -> "/"; control characters dropped. */
static void es_scrub(const char *s, size_t n, const char *home_dir, void (*emit)(char, void *), void *ctx) {
    size_t hl = home_dir ? strlen(home_dir) : 0;
    for (size_t i = 0; i < n && s[i]; ) {
        if (hl >= 3 && i + hl <= n && !strncmp(s + i, home_dir, hl)) { emit('~', ctx); i += hl; continue; }
        if (i == 0 || es_scrub_sep(s[i - 1])) {
            size_t d = es_scrub_userdir(s, i, n);
            if (d) {
                for (size_t k = 0; k < d; k++) emit(es_scrub_sep(s[i + k]) ? '/' : s[i + k], ctx);
                i += d;
                size_t u = 0;
                while (i + u < n && s[i + u] && !es_scrub_sep(s[i + u])) u++;
                if (u) { emit('~', ctx); i += u; }
                continue;
            }
        }
        unsigned char ch = (unsigned char)s[i++];
        if (ch == '\\') emit('/', ctx);
        else if (ch >= 32 || ch == '\t') emit((char)ch, ctx);
    }
}
#endif
