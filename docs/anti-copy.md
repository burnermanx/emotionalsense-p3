# Anti-copy: the disc timing check

DJMAX Portable 3 checks whether it is running from a UMD or from a copy by
measuring how long two raw disc reads take. If the reads are too fast (cache,
ISO in RAM, an emulator with no latency), the game accumulates failures and, on
the third, arms a punishment that distorts the audio.

This project does **not** skip or disable the check: the HLE emulates a UMD
device with realistic latency, and the game reaches the `OK` verdict by itself.

## Where it is

| Item | Value |
|---|---|
| Check function | `0x14868` (`build/gen/djmax_funcs_011.c:4762`) |
| Return | `0` = OK, up to `13` = failure code |
| Call at the start of scene 3 | `0x17338` -> `0x136BC` |
| Per-frame call (main loop) | `0x5DE54` -> `0x138C4` |
| Result tracker | `0x130158` |
| Tracker state | `0x13027C` |
| State consumers | `0x86178` (BeginRender) and `0x86EB8`, which write `0x123A00` and change GE state |

## What it measures

Two raw reads on the `umd0:` device (ioctl `0x01F100A6` to seek by LBA and
`0x01F30003` for an uncached read of one `0x800`-byte sector), timed with
`0x6FE34` -- which is `sceKernelGetSystemTimeWide / 1000`, converted to float
seconds and truncated to `int32`.

| Measurement | File / LBA | Minimum | Failure code |
|---|---|---|---|
| Read 1 | `disc0:/PSP_GAME/INSDIR/DATA.TPA`, LBA 54496 | 20 ms | `RES_EXCEEDSPEED2` |
| Read 2 | `disc0:/PSP_GAME/USRDIR/STREAMV.TPA`, LBA 469104 | 30 ms | `RES_EXCEEDSPEED3` |
| Seek 2 + read 2 (from the end of CRC 1 to the end of read 2) | -- | 50 ms | `ES` |

Besides the time, the check computes the CRC32 of the first 512 bytes of the
sector it read and compares it with `0xB70C3A6D`.

The full sequence:

1. `sceIoGetstat` on the two `.TPA` files and a read of `st_private[0]` (offset 64
   of `SceIoStat`) as the starting LBA.
2. `sceIoOpen("umd0:")` -- the string is obfuscated as `"vne1;"` in the binary.
3. `sceIoIoctl 0x01F100A6` (seek), a 16-byte input `{u64 lba, u64 pad}`.
4. `sceIoIoctl 0x01F30003` (raw read of N sectors), return value = number of
   sectors.
5. CRC32 and verdict.

## How the emulated device satisfies the check

Everything is in `psprecomp/src/hle/iofilemgr.c`.

| Piece | Behaviour |
|---|---|
| `g_disc_lba[]` | DATA.TPA 54496, STREAMI.TPA 155184, STREAMS.TPA 189024, STREAMV.TPA 469104 |
| `sceIoGetstat` | size at 56/60 and LBA at 64/68 (the real `SceIoStat` layout) |
| `sceIoOpen("umd0:")` | a "raw" handle with no host file, cursor in sectors |
| ioctl `0x01F100A6` | stores the LBA and sleeps `UMD_SEEK_US` = 25 ms |
| ioctl `0x01F30003` | sleeps `UMD_READ_US` = 35 ms before reading the sectors of the extracted file |
| Sectors | read from the already extracted `.TPA` in `<data>/disc/`, so the CRC32 matches |
| `sceIoRead` of files | **no** delay; loading times stay the same |

Result: read 1 = 35 ms (>= 20), read 2 = 35 ms (>= 30), seek 2 + read 2 = 60 ms
(>= 50). The verdict is `OK`. In the verdict log the values show up as
`a2=a3=a4=60` and `a5=35` (in virtual mode, `a5=36`).

The delays use `psp_threadman_sleep_exact()`, which in virtual mode advances the
guest clock by exactly the requested time, without handing over the baton. That
is why the check measures 60/35 ms in `EMOTIONALSENSE_CLOCK=virtual` too
(instead of hundreds of seconds).

The `ms0:/` directory scan at boot (`sceIoDopen`/`sceIoDread` via `0x4FA0`/
`0x5080`, PPSSPP's `HideISOFiles`) stays satisfied because the data folder's
`ms` holds no ISO or CSO.

**The ISO must not be modified.** The correct way is for the HLE to answer like a
real drive.

## The punishment and where it lives

Two failures are forgiven; the third arms the penalty.

| Address | Role |
|---|---|
| `0x130304` | failure counter; this is the one that must stop growing |
| `0x13027C` | tracker state; `0x468D21AC` is the value observed in the OK regime |
| `0x130158` | tracker of the check result |
| `0x123A00` | state that the renderer consumes |

Punishment: `0xF38F1243`, a 60-180 s timer. The punishment state is the counter
`0x130304`: when it reaches the third failure, the timer is armed. The audible
symptom is **keysound distortion** from the 2nd or 3rd song on.

`0x13027C` also shows up as `0xA48B1280` -> `0x38E489EE` (recheck timer), but
that value was not observed in a 10-minute session.

## How to tell whether the check is failing

| Signal | How to see it |
|---|---|
| Verdict | `EMOTIONALSENSE_TRACE_UMD=1` prints `umd: check vblank=... fmt=b9b5c a2=60 a3=60 a4=60 a5=35` |
| Failure | on that same line, `a2`/`a3`/`a4` below 20/30/50 ms |
| Counter | `EMOTIONALSENSE_POLL=0x00130304,0x0013027C`; the counter must not grow |
| Real duration of the ioctls | `PSPRECOMP_TRACE_IO=1` prints `io: ioctl raw seek ... dt=..ms` and `io: ioctl raw read ... dt=..ms` |
| Symptom | keysound distortion after 2-3 songs |

The verdict log is produced by a wrapper of `0x61270` in `game/guest_patches.c`;
`--wrap` cannot intercept `0x14868` because the definition and the calls are in
the same generated unit.

**`0x13027C` alone proves nothing.** It can sit at `0x468D21AC` while the check
is failing. What proves it is the `OK` verdict and the `0x130304` counter standing
still.

## History of the defect

| Version | Behaviour | Effect |
|---|---|---|
| Old HLE | seek with no wait, read with `sleep_real(25000)` | read 1 passed (25 >= 20), read 2 failed (25 < 30); the counter advanced with every song and the third armed the punishment |
| Current | seek 25 ms + read 35 ms | 35/35/60; verdict `OK`, counter standing still |

An old claim that "there is no timing check in this build" was wrong: the check
exists and goes through `sceIoIoctl`, not `sceIoRead`.

Sessions measured with the fix:

| Session | Checks (vblank) | Verdicts |
|---|---|---|
| 3 songs, long script | 4630 (boot), 4919 (1st song), 12121 (2nd song) | `60/60/60/35`, `60/60/60/35`, `61/60/61/35` |
| Round 12, 3 songs | 416, 768, 8576 | all `OK` (`60/60/60/35`) |
| 6000 frames without a script | 4631 | `OK` |

The anti-copy goal is met: the three checks pass and the counter stops. What did
not close at the time was a session of three complete songs, because of the BGA
of the second song -- see `mpeg-psmf.md`.
