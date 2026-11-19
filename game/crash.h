/* Crash and hang reports: <data>/crashes/crash-<UTC date-time>.txt.
 *
 * Linux: SIGSEGV/SIGBUS/SIGILL/SIGFPE/SIGABRT on an alternate stack, async-signal-safe
 * formatting, native backtrace with module-relative addresses and build-id.
 * Windows: SetUnhandledExceptionFilter + SIGABRT, a small minidump next to the text.
 * Both: a watchdog that writes a "hang" report when the display stops advancing (the game
 * keeps running), a ring of the last firmware calls and of the last log lines, and the
 * recompiler's "indirect call to a function that was not recompiled" failure.
 *
 * The report holds no environment variables (only the game's own options), no save data and
 * no disc content, and the user's home folder is printed as "~". Nothing is sent anywhere. */
#ifndef ES_CRASH_H
#define ES_CRASH_H

/* `data` is the data folder; the reports go to <data>/crashes. Call once, first thing in main,
 * before any other thread exists. */
void es_crash_init(const char *data);

/* Call from the main loop: runs EMOTIONALSENSE_CRASH_TEST=segv|abort|guest|hang once, after
 * about two seconds of game time. Free when the variable is unset. */
void es_crash_tick(void);

/* Before leaving the process: lets the log tee write out what is still in its pipe. */
void es_crash_flush(void);

#endif
