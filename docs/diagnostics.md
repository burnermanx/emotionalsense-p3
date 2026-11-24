# Diagnostics: environment variables and tools

Everything here is switched on by an environment variable, without recompiling.
The binary is `./build/game/EmotionalSense-P3-game` in a development tree (the
launcher puts it in `<data>/game/`); the command-line arguments are
`--data --scale --frames --capture --renderer vulkan|software|null --fps`.

## Terminal output (off by default)

The game and the launcher print **nothing** to the terminal by default; only a fatal error
(game data missing, no window, the real-save refusal, a crash notice) gets through, in one or two
lines. The diagnostic log (`confirm: cross`, `render: ...`, `poll[...]`, `exit: ...`, HLE and
trace output) is switched on with any of:

| Switch | Where |
|---|---|
| `--verbose` (`-v`) | game; and the launcher, which passes it on to the game it starts (and prints its own progress) |
| `EMOTIONALSENSE_LOG=1` (or `PSPRECOMP_LOG=1`) | game |
| any `EMOTIONALSENSE_TRACE_*` or `PSPRECOMP_TRACE_*` variable | game: implies the log |

`EMOTIONALSENSE_POLL`, `EMOTIONALSENSE_POKE` and the test routes print through the same log, so
a script that reads them must set `EMOTIONALSENSE_LOG=1` (`scripts/check_scripts.sh` does). The
crash report's "last log lines" are kept with the log **off** as well. Output of other libraries
(Vulkan loader, Mesa, the Gamescope layer) is theirs: it is neither hidden nor changed. Messages go
to stderr (the former stdout lines too); `--version` and `--help` still print on stdout.

## Sync log (for a timing report)

If the notes drift from the music, run the launcher with `--sync-log` (or with
`EMOTIONALSENSE_SYNC_LOG=1` in the environment, the way to turn it on from Steam's launch options)
and play one song. The game's log, with the clock, vblank and audio traces, goes to
`<data folder>/logs/sync-<time>.log` instead of the terminal, and the user name is scrubbed from it
when the game ends. It records the machine (CPU, session type, Gamescope, audio driver), the audio
device the game got, one `clock[...]` line per 60 vblanks (real time, guest time, audio blocks:
the vblank rate and the drift) and one `audio[...]` line per second and channel. The per-frame
picture hash of `EMOTIONALSENSE_TRACE_FPS` is left out on purpose: it would load a small CPU and
change what is being measured.

```
EmotionalSense-P3 --run --sync-log                        # terminal / Desktop Mode
EMOTIONALSENSE_SYNC_LOG=1 %command%                       # Steam launch options (Game Mode)
```

## Renderer report (Vulkan did not start)

On every start the game writes `<data folder>/logs/renderer.log`, whatever the log setting: the
renderer asked for (`requested:`) and the one that runs (`result:`), the environment that matters to
a graphics start (library paths, `LD_PRELOAD`, `VK_*`, `STEAM*`, session and display variables; the
user name in paths is replaced by `~`) and every step of the Vulkan start-up with its `VkResult`
(loader and instance version, layers, extensions, the libraries loaded into the process, instance,
physical devices, device). When Vulkan does not start the game runs the software renderer (1x, slow:
no upscale, and the audio can fall behind) and the launcher says so with a dialog in the language of
the launcher, with the report. `result:` is then `software-fallback (vulkan did not start)`.

A renderer named explicitly (`--renderer vulkan`, the launcher's Renderer = Vulkan option or
`EMOTIONALSENSE_RENDERER=vulkan`) never falls back: the game stops with exit code 3 and the reason.
Leave the option on `auto` for normal play.

This renderer draws offscreen and never presents through Vulkan, so it turns the implicit layers off
for its instance (the Gamescope WSI layer of Game Mode, Steam's overlay and pipeline-cache layers: a
failure inside the Gamescope layer aborts the process). `EMOTIONALSENSE_VK_LAYERS=1` leaves them on.
The launcher also hands the game the system's library environment without Steam's runtime folders in
`LD_LIBRARY_PATH` and without Steam's overlay in `LD_PRELOAD` (`EMOTIONALSENSE_KEEP_STEAM_ENV=1`
keeps them).

```
# Steam Deck, Game Mode: launch options of the shortcut
EMOTIONALSENSE_SYNC_LOG=1 %command%
# Desktop Mode (Konsole), in the folder of the AppImage
EMOTIONALSENSE_SYNC_LOG=1 ./EmotionalSense-P3-0.1.0-linux-x86_64.AppImage --run
```

## Variables

| Variable | Effect | Notes |
|---|---|---|
| `EMOTIONALSENSE_SCRIPT` | button script: `vb:mask:frames`, `@scene+N:mask:frames`, `@scene#k+N:mask:frames`, or `@file` | at most 256 entries, 16 KB buffer |
| `EMOTIONALSENSE_POLL` | up to 8 addresses; prints when the value changes | suffix `:f` prints a float; does **not** disturb the timing |
| `EMOTIONALSENSE_CLOCK` | `virtual` = deterministic clock driven by `g_vtime_us` | without it, a monotonic clock since boot |
| `EMOTIONALSENSE_UNLOCK` | `0` turns the built-in song unlock off | on by default |
| `EMOTIONALSENSE_DUMP_RAM` | writes `psp_mem.ram` (32 MB) on exit | `PSP_RAM_SIZE`; an old note said 16 MB |
| `EMOTIONALSENSE_DUMP_RANGE` | `0xSTART:0xEND:file`; byte-by-byte dump through `psp_read8` | includes the module image, outside the RAM window |
| `EMOTIONALSENSE_SHOT` | list of vblanks to capture as PPM (`2327,4000,9300`) | writes `frame_<vb>.ppm` |
| `EMOTIONALSENSE_SHOT_DIR` | folder of the captures | must exist; default `/tmp` (`%TEMP%` on Windows) |
| `EMOTIONALSENSE_SHOT_EVERY` | capture every N vblanks | can be combined with `SHOT` |
| `EMOTIONALSENSE_WATCH_W` | write watchpoint on one word | **disturbs** the timing; only changes show up; `EMOTIONALSENSE_WATCH_LIMIT` limits the printed writes (default 8) |
| `EMOTIONALSENSE_TRACE_AV` | audio/video calls and a hexdump of the buffers | see `mpeg-psmf.md` |
| `EMOTIONALSENSE_TRACE_MPEG` | ring buffer, demuxed packets, AUs, PTS | see `mpeg-psmf.md` |
| `EMOTIONALSENSE_TRACE_CLOCK` | every 60 vblanks: real time, guest clock, audio blocks, interrupts, vblanks | one line per window |
| `EMOTIONALSENSE_TRACE_CTRL` | buttons read per frame | |
| `EMOTIONALSENSE_TRACE_INPUT` | bits the script injected | |
| (script masks above 16 bits) | `0x10000` left trigger, `0x20000` right trigger, `0x40000` the E key (stick left), `0x80000` the R key (stick right) | test routes for the 2T triggers; `EMOTIONALSENSE_TRACE_CTRL` prints the stick X (`ax=`) |
| `EMOTIONALSENSE_TRACE_UMD` | registration of the UMD callback and the verdict of the anti-copy check | see `anti-copy.md` |
| `EMOTIONALSENSE_TRACE_UTIL` | `sceUtilityGetSystemParamInt` and `SavedataInitStart` | |
| `EMOTIONALSENSE_TRACE_CB` | callback notification/delivery | |
| `EMOTIONALSENSE_TRACE_VPL` | VPL allocations | |
| `EMOTIONALSENSE_TRACE_PACK` | pack removals (`0x7F438`) | |
| `EMOTIONALSENSE_TRACE_HLE_THREAD=<uid>` | one line per HLE call of that thread: `hle[uid@vblank]: name(a0..a3) ra=0x... t=us` | `all` traces every thread |
| `EMOTIONALSENSE_POKE` | `0xADDR:value[:size]` written at every vblank | debugging only; e.g. life at `0x088D12CC:0x42C80000`; pointer paths are accepted (see the README) |
| `EMOTIONALSENSE_SPEED` / `--speed` | `respect` (default) or `original` | see `speed.md` |
| `EMOTIONALSENSE_BUTTONS` / `--buttons` | `auto` (default), `playstation`, `xbox`, `steamdeck`, `switch` | see `buttons.md` |
| `EMOTIONALSENSE_TRIGGERS_2T` / `--triggers-2t` | `stick` (default) or `lr` | see `buttons.md` |
| `EMOTIONALSENSE_TRACE_PAD` | logs every controller the window opens: name, SDL type, vendor:product and the button style it maps to | what Steam Input reports on a Deck |
| `EMOTIONALSENSE_TRACE_SPEED` | logs the chart header and the judge's tempo at the song-start call sites | |
| `EMOTIONALSENSE_SHOT_OVERLAY` | `1`: the captures (`EMOTIONALSENSE_SHOT`, `--capture`, F12) carry the host overlay (the speed label, the exit dialog) | a test aid; the window's compositor cannot be captured from a script |
| `EMOTIONALSENSE_DEBUG_FB` | logs `SetFrameBuf` changes | |
| `EMOTIONALSENSE_DEBUG_THREADS` | thread creation/end | |
| `EMOTIONALSENSE_DEBUG_VPL` | VPL allocation failures | |
| `EMOTIONALSENSE_FPS` / `--fps` | changes the frame rate (debug tool) | speeds the game up; the default is 59.94 |
| `EMOTIONALSENSE_UNCAP` | `1` removes the frame limit | same |
| `EMOTIONALSENSE_VSYNC` | `0` turns the window's vsync off | |
| `EMOTIONALSENSE_RENDERER` | initial renderer, like `--renderer` | |
| `EMOTIONALSENSE_DATA` | data folder, like `--data` | |
| `EMOTIONALSENSE_PACK_ORIGINAL` | uses the original `0x7F438` (no wrapper) | A/B escape hatch; breaks on leaving the pause |
| `EMOTIONALSENSE_AUDIO_LATENCY_MS` | floor of the prefill target | default 40 ms |
| `EMOTIONALSENSE_AUDIO_AHEAD_MS` | guest audio slack (A/B override) | default 0 |
| `EMOTIONALSENSE_CRASH_TEST` | `segv`, `abort`, `guest` or `hang`: provokes that failure about two seconds into the game, to test the crash report | see "Crash reports" below |
| `EMOTIONALSENSE_CRASH=0` | no crash handler, no log tee | |
| `EMOTIONALSENSE_HANG_SECONDS` | seconds without a vblank before a "hang" report (default 20) | |

## Button script

| Form | Meaning |
|---|---|
| `700:2000:5` | CIRCLE at vblank 700, for 5 frames |
| `@1+60:0008:5` | START 60 frames after the **first** entry into scene 1, whatever the current scene is |
| `@2#2+80:0020:5` | RIGHT 80 frames after the **2nd** entry into scene 2, and only while the scene is still 2 |
| `@2#0+80:...` | re-arms on every entry into scene 2 |
| `@file` | one entry per line; `#` is a comment only at the start of a line |

Button masks:

| Button | Mask | Button | Mask |
|---|---|---|---|
| SELECT | `0x0001` | TRIANGLE | `0x1000` |
| START | `0x0008` | CIRCLE | `0x2000` |
| UP | `0x0010` | CROSS | `0x4000` |
| RIGHT | `0x0020` | SQUARE | `0x8000` |
| DOWN | `0x0040` | L | `0x0100` |
| LEFT | `0x0080` | R | `0x0200` |

Scene word: **`0x0805E874`** (`app+4`; the app object is `0x0805E870`). With the
1.02 update the object moves; the host finds it through the pointer at `0xC8B30`.

| Value | Scene |
|---|---|
| 1 | title |
| 2 | song select |
| 3 | gameplay |
| 4 | result |

The pause is internal to scene 3: `app+4` stays 3 during the pause.

## Crash reports

If the game crashes, it writes `<data>/crashes/crash-<UTC date-time>.txt` (and, on
Windows, a small `.dmp` next to it). The launcher then offers to open the folder,
copy the report or open a GitHub issue with a short summary filled in; nothing is
sent automatically.

What the report holds: the version and commit, the target, the operating system,
the renderer and the graphics card, the game options, the scene and the vblank,
the guest registers and call stack, the last 200 firmware calls, a native
backtrace with module-relative addresses and the GNU build-id, and the last 200
log lines. It holds no environment variables (only the game's own options), no
save data and no disc content, and the home folder is printed as `~`.

Kinds: `segv`, `bus`, `illegal-instruction`, `fpe`, `abort` (Linux signals);
`access-violation` and `exception` (Windows); `guest` (an indirect call to a
function that was not recompiled); `hang` (no display vblank for 20 s, the game is
left running).

To read a native backtrace, use the binary of the same commit (the build-id in the
report names it) with `addr2line -f -e <binary> <offset>`; the guest function is the
`psp_func_<address>` that contains the first return address of the guest call
stack. `EMOTIONALSENSE_CRASH_TEST=segv|abort|guest|hang` produces each kind.

## Tools

| Tool | What for |
|---|---|
| `scripts/check_scripts.sh` | runs the repository's routes and fails if scene 3 does not appear |
| `scripts/check_delay_slots.py` | fails if a delay slot is separated from its branch/call |
| `scripts/dump/*.script` | texture-dump routes (experimental texture pack, see `docs/technical.md`) |
| `texpack/make_tex_pack.py` | builds a texture pack from a dump (see the README) |
| F9 (window) | GE and imports census |
| F12 (window) | immediate capture |
| F11 (window) | full screen at the chosen rate; Alt+Enter is borderless full screen |

## Known pitfalls

| Pitfall | Detail |
|---|---|
| `_Exit` and stdio | `_Exit` does not run the stdio teardown; the host flushes everything before `host_exit_now(0)`, otherwise trace buffers (such as the one of `PSPRECOMP_TRACE_GE_FILE`) are lost |
| Windows: DLLs next to the exe | `EmotionalSense-P3-game.exe` needs `SDL2.dll`, the FFmpeg DLLs and `libwinpthread-1.dll` next to the executable (or on `PATH`); the launcher's kit copies them |
| Windows: `_Exit` hangs under wine | `_Exit` still runs `ExitProcess`, which waits for the DLL detach and hangs with SDL2's audio thread; `host_compat.h` uses `TerminateProcess` |
| wine: prefix in `/tmp` | do not create a wine prefix directly in `/tmp`; wine refuses. Use a subfolder (for example `/tmp/es-prefix`) |
| `timeout` | does not reliably kill the process (it ignores SIGTERM); use `kill -9` with the PID and be careful with `pkill -f` |
| `--capture` | only writes if the loop ends through `--frames`, not if the window is closed |
| Software renderer | 52-72 ms per frame in the song select and, being cooperative, it takes the audio down with it; measure time/audio with `--renderer null` or `vulkan` |
| `build/gen/` | is generated; editing a `.c` there persists because `ninja -C build` does not regenerate it. Back up and restore |
| `--wrap` | only intercepts calls between translation units; never calls inside the same generated `.c` nor indirect calls through `psp_register` |
| Watchpoint | `EMOTIONALSENSE_WATCH_W` and hardware watchpoints disturb the timing and hide races; `EMOTIONALSENSE_POLL` does not |
| Polling 8 addresses | hid the race of the pause-exit crash; 1-2 addresses did not |
| Comparing Vulkan x software | with the real clock, two Vulkan runs differ by about 37% from time shifts; compare under `EMOTIONALSENSE_CLOCK=virtual` or by composition |
| gdb | cannot attach (ptrace_scope); launch under gdb. `psp_cpu` and `psp_mem` have an unknown type: use `((unsigned*)&psp_cpu)[n]` and `psp_mem_ptr(addr)` |

## Recipes

```sh
# headless with a capture (add EMOTIONALSENSE_LOG=1 or --verbose to see the log)
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  ./build/game/EmotionalSense-P3-game --data <copy of the data> --renderer software \
  --frames 6500 --capture out.ppm

# a gameplay route
EMOTIONALSENSE_SCRIPT=@scripts/gameplay.script \
  ./build/game/EmotionalSense-P3-game --data <copy of the data> --renderer null --frames 4000

# scene state and the anti-copy check
EMOTIONALSENSE_LOG=1 EMOTIONALSENSE_POLL=0x0805E874,0x0013027C,0x00130304 \
  ./build/game/EmotionalSense-P3-game --data <copy of the data> --frames 3000

# guest clock against the real one
EMOTIONALSENSE_TRACE_CLOCK=1 ./build/game/EmotionalSense-P3-game --frames 600

# GE trace over a vblank range
PSPRECOMP_TRACE_GE=2400-2409 ./build/game/EmotionalSense-P3-game --frames 2500
```

Always run on a **copy** of the data folder: the routes finish songs, and the game
then writes the save.

## Measurements made under wine, not on real Windows

Everything measured on the Windows side in this repository was measured under
`wine` on a Linux machine. It holds for what is guest logic (routes, scenes,
unlock, audio block rate) and is **not** a guarantee for what depends on the
driver:

- **Video**: `winevulkan` uses the Linux Vulkan driver, so the Vulkan path works,
  but it proves nothing about a real Windows driver.
- **Audio**: wine's `SDL_AUDIODRIVER=dummy` consumes almost exactly 44,100 Hz
  (mixer 172.31 blocks/s, Vaudio 21.53), while Linux's `dummy` rounds the
  1024-frame period to 23 ms and consumes about 0.9% faster (173.8 and 21.68).
  Neither is the WASAPI of the user's machine.
- **Sleep**: `host_cond_until` never returned before the deadline under wine (0
  cases in 1000 attempts per duration), with a maximum overshoot of 0.17 / 0.39 /
  1.57 / 2.16 ms for 1 / 5 / 25 / 35 ms. The 35 ms case is 0.16 ms inside the
  plan's 2 ms limit, and switching to `CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`
  is not possible without giving up the condition variable; extra delay only
  makes the anti-copy read look slower, which is the safe side.
- **Window, focus, closing with the X, F11, F12**: not exercised (the test is
  headless with `SDL_VIDEODRIVER=dummy`).

Testing on a real Windows is the user's: three songs in a row, close and reopen,
and listen for crackle, delay from key to sound, or distortion after the second
song.
