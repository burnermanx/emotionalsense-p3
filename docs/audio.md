# Audio

A single SDL2 device, nine per-channel rings for the guest's channels, a common
prefill target and a time grid per channel. The design lives in
`psprecomp/src/hle/audio_hist.c` (sink and rings), `psprecomp/src/hle/misc.c`
(`sceAudio`) and `game/hle_djmax.c` (`sceVaudio`).

## Architecture

| Item | Value |
|---|---|
| Device | one SDL2 device, 44100 Hz, `AUDIO_S16SYS`, stereo, callback |
| Channels | 9 rings: `sceAudio` 0-7 + `sceVaudio` = channel 8 |
| Ring | 1 s per channel (`RING_SAMPLES` = 44100 stereo frames) |
| Mixing | the callback sums every active ring and saturates to `int16` |
| Volume | applied when samples enter the ring (`vol * sample >> 15`) |
| Silence | leaves the callback only when a ring is empty; never between two blocks of sound |

Channels the game uses in practice:

| Channel | Content | Block |
|---|---|---|
| `ch1` | movie/opening audio | 2048 samples (46.4 ms) |
| `ch2` | keysounds and voices (SasCore mixer) | 256 samples (5.8 ms) |
| `ch8` | Vaudio / BGM | 2048 samples (46.4 ms) |

The device is opened on the first `psp_audio_output`. If SDL fails, the audio is
dropped silently, without taking the game down.

## Prefill (the cushion)

The target is **common to all channels**, so keysounds and BGM have the same
latency:

```
target = max(device period + jitter + margin,
             floor from EMOTIONALSENSE_AUDIO_LATENCY_MS (in samples))
target = max(target, largest guest block + jitter + margin)
```

| Constant | Value |
|---|---|
| measured jitter | 8 ms (`JITTER_SAMPLES`) |
| margin | 10 ms (`MARGIN_SAMPLES`) |
| configurable floor | `EMOTIONALSENSE_AUDIO_LATENCY_MS`, default 40 ms |
| ceiling | half the ring (0.5 s) |
| retiring a silent channel | 100 ms without blocks |

Rules:

- **Start:** a channel is only consumed after it has accumulated the target.
- **Steady state:** the callback delivers silence only with the ring empty.
- **Real underrun:** the callback asks, the ring is empty and the channel is
  active -- it delivers zeros, counts it and goes back to requiring the prefill.
- **End of a voice:** `sceAudioChRelease`/`sceVaudioChRelease` mark the channel
  to drain without counting an underrun.
- When the largest block seen goes up, the global target goes up and the active
  channels prefill again up to the new target.

Measurement with the common target (8000 frames, `--renderer null`):

| Channel | Underruns | Silence | Mean ring |
|---|---|---|---|
| `ch1` | 1 | 1024 samples | 54.2 ms |
| `ch2` | 4 | 2816 samples | 62.8 ms |
| `ch8` | 1 | 1024 samples | 61.6 ms |

The cost is key-to-sound latency, now about 62 ms. What matters is the sync
between keysound and music.

`EMOTIONALSENSE_AUDIO_AHEAD_MS` still exists as an A/B override, default **0**.

## Time grid and the 100 ms tolerance

`audio_pace` (`misc.c`) and `hle_VaudioOutputBlocking` (`game/hle_djmax.c`) keep a
deadline per channel:

```c
if (due + 100000 < now) due = now;   /* restart only after a real stall */
due += play_us;
```

- `AUDIO_GRID_TOLERANCE_US` = 100 ms. The rounds call this the "exact 100 ms
  grid": what is 100 ms is the tolerance, not the period of the grid.
- A late block is paid for in the next block; only a stall longer than 100 ms
  restarts the grid.
- The tolerance is **time**, not a block: the keysound channel plays a block in
  5.8 ms and, in gameplay, the mixer thread waits 10-18 ms for the CPU. With a
  one-block tolerance the grid restarted several times a second and lost about
  11% of the audio (153.5 instead of 172.3 blocks/s).

In `EMOTIONALSENSE_CLOCK=virtual` mode the audio does not pace against the host
clock: `audio_pace` and `hle_VaudioOutputBlocking` only hand over the baton
(`psp_threadman_sleep_real`), and the frame wait owns the time.

## Keys and music with the same latency (round 17)

The ring level is **not** the latency of the sound. A block of 2048 frames that
enters with the ring at 2048 has samples 2048 to 4095 frames ahead; what decides
the sync is the **mean latency per sample**:

```
latency = level at the moment of the enqueue + (block - 1) / 2
```

Measured this way (`freestyle_4t`, real time, scene 3), the keys (`ch2`, blocks
of 256) always came out 512 frames (11.6 ms) before the music (`ch8`, blocks of
2048): 2560 against 3072 frames, constant, with `dummy` and with `disk`. The
earlier explanation ("`ch8` only keeps its level in multiples of the block, there
is no slack") is right about the **level** and was wrong as a conclusion about
the latency: there is no structural limit, and there is no swing from 0 to 23 ms
(that was the level sampled at the instants of the trace).

The fix is a per-channel addition in `psp_audio_wait`: the channel only waits
when the level goes above `target + trim`. The `trim` is 512 frames for channels
whose last block has up to 256 frames and 0 for the larger ones (the criterion is
the block size, not the channel number). Raising the keys is the safe side: more
ring, no new underrun. The music target and the device period did not change.

| Variable | Effect |
|---|---|
| `EMOTIONALSENSE_KEY_TRIM=<frames>` | addition for the small-block channels; default 512; 0 turns it off |
| `EMOTIONALSENSE_TRACE_AUDIO=1` | per second and per channel, `lat=` = mean latency per sample (frames) in that window |

Comparing `ch2` and `ch8` by that metric (ignoring the first second of each
stretch, which mixes the before and after of the scene change), measured with the
fix: `ch2` and `ch8` at 3071.5 frames (69.6 ms) in every second of gameplay,
difference 0.0 ms, with `dummy` and with `disk`.

Limit: this equalises the latency *inside the recomp*. If the real PSP had
different latencies between `sceAudio` and `sceVaudio`, this cannot tell;
`KEY_TRIM` exists for tuning by ear.

## Measured rates

Measured from the `t=` stamp of the HLE trace (independent of SDL), in three 8 s
windows of gameplay:

| Channel | Windows (blocks/s) | Ideal | Delay against the grid |
|---|---|---|---|
| SasCore mixer (`ch2`, 256 samples) | 172.30 / 172.32 / 172.30 | 172.27 | -2.3 to +5.2 ms, no trend |
| Vaudio (`ch8`, 2048 samples) | 21.54 / 21.53 / 21.53 | 21.53 | within +-1.3 ms |
| movie (`ch1`, 2048 samples) | 21.48 | 21.53 +- 0.1 | -- |

Acceptance targets used in the rounds: mixer **172.3 +- 0.5 blocks/s** and Vaudio
**21.53 +- 0.1 blocks/s** in gameplay.

The old underrun counter did not see the problem: it only looked at the queue at
the moment of entry and did not see silence insertions with the queue still full.
**The rate measurement is the one that counts.**

## Why a sleep must never return early

The anti-copy check measures intervals with the guest clock and demands raw reads
of at least 20/30/50 ms; the emulated delays are 25 ms (seek) and 35 ms (read). A
sleep that returns before its deadline fails the check, the `0x130304` counter
advances and the third failure arms the punishment (see `anti-copy.md`).

| Platform | Rule |
|---|---|
| Linux | `park_us_timed(us, us)` waits the requested real time |
| Windows | `Sleep()` rounds to the system tick and can return early; use a high-resolution timer (`CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`, with `timeBeginPeriod(1)` as a fallback) |
| Virtual | `psp_threadman_sleep_exact` advances `g_vtime_us` by exactly `us` |

The grid's 100 ms tolerance does not authorise returning early: it only lets a
**late** block be paid for in the next one.

## Keysound sync (round 12)

The reported symptom was "slight crackle in the keysounds and voices" and
"keysounds slightly late relative to the music". The evolution:

| Model | `ch2` | `ch8` | Difference | Effect |
|---|---|---|---|---|
| 3 devices, a 70 ms cushion only when empty | 40 ms | 56 ms | ~16 ms ahead | every underrun inserted 70 ms and pushed the channel back |
| 1 device, per-channel target (`block + jitter + margin`) | ~31 ms | ~73 ms | ~42 ms behind | keysounds behind the BGM |
| 1 device, common target | 62.8 ms | 61.6 ms | ~1.2 ms | keysounds and BGM aligned |

The user confirmed the crackle was gone and the audio was "perfect"; later pointed
out the sync, which the common target fixed. If the crackle comes back with the
counters at zero, the suspects are the nearest-neighbour resampling in
`sascore.c` and the fixed envelope of `SetSimpleADSR`; do not touch them before a
retest.

## Measuring the rate from the HLE trace

A trace with a timestamp per line (` t=<us>`, produced by
`EMOTIONALSENSE_TRACE_HLE_THREAD=<uid>`) is enough to find the thread **by role**,
not by a fixed uid:

| Role | Signature |
|---|---|
| `mixer` | the thread that calls `__sceSasCore`/`sceSasCore` (keysounds, `ch2`) |
| `vaudio` | the thread that calls `sceVaudioOutputBlocking` (BGM, `ch8`) |
| `movie` | the thread that calls `sceAudioOutputPannedBlocking` channel 0 (opening) |

Compare against the ideal grid in 8 s windows and print the rate, the minimum/
maximum delay and the delay at the end of the window. A healthy thread stays
within +-20 ms and does not drift; a grid that restarts at every late block loses
rate and drifts backwards. (The script used for the rounds was a research tool and
is not part of the repository.)

```sh
EMOTIONALSENSE_SCRIPT=@scripts/gameplay_fast.script \
EMOTIONALSENSE_LOG=1 EMOTIONALSENSE_POKE=0x088D12CC:0x42C80000 \
EMOTIONALSENSE_TRACE_HLE_THREAD=262154 \
./build/game/EmotionalSense-P3-game --data <copy of the data> --frames 2500 --renderer null \
  > /tmp/rate.log 2>&1
```

**Measurement caution:** the software renderer takes 52-72 ms per frame in the
song select and, being cooperative, takes the audio down with it. Time and audio
measurements outside gameplay must use `--renderer null` or `vulkan`.

## Report on exit

`psprecomp_audio_report()` prints, per channel, blocks, `samples/block`,
underruns, silence samples after the start, `dropped` and the minimum/mean/maximum
ring level (in samples and ms); and, in total, blocks, `maxblocks`, frames and
mean/maximum/minimum dBFS. It is the proof that the audio reached the device, not
only that the guest called the function.
