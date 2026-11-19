# Technical notes

How the port works and how it is tested. For using it, see the [README](../README.md).

## How it works

```
your ISO ──► launcher ──► <data>/disc/PSP_GAME/…      (ISO9660 extraction)
                     ├──► <data>/module/eboot.elf     (pspdecrypt, GPL-3.0, separate process)
                     ├──► <data>/gen/djmax_*.c        (allegrexrecomp emit)
                     └──► <data>/game/EmotionalSense-P3-game  (kit compiler: zig on Linux, llvm-mingw on Windows)
```

| Piece | Where |
|---|---|
| Launcher entry point, CLI | [`launcher/es_main.py`](../launcher/es_main.py) |
| Window (Tk canvas), original theme, translations | [`launcher/es_gui.py`](../launcher/es_gui.py), [`es_theme.py`](../launcher/es_theme.py), [`locales/`](../launcher/locales) |
| Prepare (every step idempotent) and kit build | [`launcher/es_prepare.py`](../launcher/es_prepare.py), [`es_build.py`](../launcher/es_build.py) |
| Kit manifest / kit assembly / release guard | [`scripts/make_manifest.py`](../scripts/make_manifest.py), [`make_kit.py`](../scripts/make_kit.py), [`release_guard.py`](../scripts/release_guard.py) |
| Old whole-game pipeline (development only) | [`launcher/es_pipeline.py`](../launcher/es_pipeline.py), [`es_dev.py`](../launcher/es_dev.py) |
| Game host (`main`) | [`game/host.c`](../game/host.c) |
| Toolkit (submodule, pinned) | [`psprecomp/`](../psprecomp) |
| Technical notes | [`docs/`](.): anti-copy, video, audio, unlock table, diagnostics |

### What the fork adds

The pinned submodule is our fork of `psprecomp`, and every change lives there
as a normal commit — there is no separate patch series to apply. `git -C
psprecomp log 5e2ca44..HEAD` lists them; the groups are:

| Group | Why |
|---|---|
| Recompiler | DJMAX's EBOOT keeps its 55 section headers but zeroes their names, so upstream found no `.text`, no import stubs and no module info. Names are recovered from `p_paddr`, the import table and section flags: **195 firmware imports, 5,638 functions, 86.5% of `.text`**. `.data` is a second `PT_LOAD` and must be loaded too, and the constructor heuristic must not run a pointer table in `.rodata`. A `jal` is no longer split from its delay slot when a `lui`+`addiu` constant happens to point at that slot — that bug kept the track, the notes and the HUD off screen. |
| HLE | sysmem, threadman, IoFileMgr and savedata; the raw `umd0:` sector device the disc check reads; GE stall addresses and texture formats; audio history and SasCore; the frame handoff seqlock. |
| Renderer | The Vulkan backend, dirty-rect uploads and write-back, `TEXFILTER` bilinear sampling in both backends, pixel-centre coverage in the software rasterizer, DXT endpoints as desktop 565. |
| Timing | The guest runs on a monotonic clock at the PSP's native 59.94 Hz; one audio device with per-channel rings and a shared prefill target; virtual time advances exactly for guest-timed sleeps; scene-relative input scripts. |
| Instrumentation | GE command/vertex census, GE trace by vblank range and file, screenshot lists, `EMOTIONALSENSE_SCRIPT`, clock and VRAM dumps, F9 census and F12 capture. |
| Platform | SDL2 window backend (upstream's is Win32-only) and the MinGW-w64 build. |

## Status

The whole pipeline runs: ISO → decrypted + relocated module → 91 C files →
native binary (about 22 s on a 16-thread machine).

The game boots through its opening videos and menus, and a song can be played
from the track select to the result screen. Gameplay runs at the PSP's native
59.94 Hz regardless of the monitor's refresh rate: the guest clock, vblank
interrupts and chart timing are all derived from one monotonic clock, and the
window presents completed frames at whatever rate the display allows.

What is known to work:

- **Timing** — the guest runs at the PSP's native 59.94 Hz (1.001 s per 60
  frames). The chart follows the clock, not the frame count.
- **Rendering** — menus, track panels, notes and HUD are drawn from the game's
  own display lists (software and Vulkan backends). The two backends agree to
  a fraction of a pixel, and the track/HUD regions match a PPSSPP reference
  capture within 21/255 per channel.
- **Audio** — one SDL2 device with nine per-channel rings; BGM, keysounds and
  menus share one clock and one prefill target.
- **Unlock** — the game starts with every chart, every collectible (DJ icons,
  crew, gear, notes, gallery images, videos) and the two gated modes, 4.2T and
  6.2T. Only bits and charts still locked in the save are touched, so anything
  already earned by playing keeps its state. The writes are made in RAM, but
  the game saves that RAM: the first time it writes the save (leaving the
  result screen of a finished song) the unlock goes into `SD.BIN`, and turning
  `EMOTIONALSENSE_UNLOCK=0` on afterwards does not lock it again. To keep the
  save as it is, back up `SD.BIN` first or run with `EMOTIONALSENSE_UNLOCK=0`
  from the start. `EMOTIONALSENSE_UNLOCK_ITEMS=0` leaves the collectibles
  locked (crews carry gameplay skills such as Anti Break) and follows the same
  rule: items already written to the save stay unlocked.
- **UMD** — the anti-piracy disc check reads a real sector through `umd0:` and
  passes (25 ms seek + 35 ms read, so the failure counter stops advancing).
- **Reference capture** — the track and HUD were compared against PPSSPP
  captures taken at the same game state (research scripts, not part of the repository).

Known bugs and recent findings, in priority order:

- **The opening used to run at 21.5 fps, not 59.94 (fixed).** The blocking audio
  output let the guest run 150 ms ahead of the host clock, so the video player
  believed it was late on every frame and stayed in recovery. The blocking call
  now returns when the block it submitted is due, and the crackle cushion moved
  to host latency (`EMOTIONALSENSE_AUDIO_LATENCY_MS`, default 70 ms). Against
  PPSSPP's headless debugger the opening now matches to 0.02 calls per frame
  (A 0.36, V 0.50, W 1.00, C 1.00). `EMOTIONALSENSE_AUDIO_AHEAD_MS` stays as an
  A/B override, default 0.
- **The mixer and BGM rates were 30% low (fixed).** The blocking output
  restarted its deadline whenever a call arrived after it, which is always, so
  every block lost the guest's work plus the baton handover: the 256-sample
  mixer fell from 172 to 120 blocks/s. Only a stall of more than one block
  restarts the grid now; the mixer is back to 172/s, `ch2` underruns are 0.4%
  and `ch8` 0.08% over a whole song.
- **The opening's recovery was a PTS step (fixed).** `sceMpegGetAvcAu` and
  `sceMpegGetAtracAu` handed every AU the timestamp of the last PES that had
  one, so the player's video clock sat still for 16 frames and its audio clock
  moved in 2–3 block steps; the difference crossed the 4-frame recovery
  threshold several times a second. Each AU now gets its own PTS (one period
  per AU, no per-step rounding). The opening matches PPSSPP from window 0
  (A 0.38/V 0.54/W 1.08/C 0.96), frame 1200 arrives at 20.58 s, and the movie
  channel is back on the exact grid (21.48 blocks/s).
- **The vblank interrupt is 59.94 Hz in the stable regime.** The long-frame
  windows show 60.1–60.9 Hz per window, but that is the frame's own duration,
  not a grid defect; the separate anchor was not applied.
- **The stream change still differs.** PPSSPP has three long frames between
  484 and 510, ours has one at 481 (~27 frames). The title lands at 4624 here
  and 4638 there.

`EMOTIONALSENSE_CLOCK=virtual` works: it no longer hangs, and it decodes the
opening (title at ~1875) and reaches gameplay with `scripts/screens.script`.
The guest clock advances 16.7 ms per vblank; the host runs faster than real
because virtual mode does not wait for wall time. Two runs can differ by ±5
frames because the scheduler is still host threads. The captured *image* can
still differ in scenes with moving
video (the song-select preview), because the capture reads VRAM before the
current frame is presented.

A song with no input ends in a game over after ~34 s: the life drains 7.2 per
BREAK and 14 BREAKs empty it. That is the game's own behaviour, and PPSSPP
does the same at the same instant. To see a whole song, hold the life up with
`EMOTIONALSENSE_POKE=0x088D12CC:0x42C80000`. That heap address went stale (heap
objects move; the 1.02 update moves them too): `EMOTIONALSENSE_POKE` and
`EMOTIONALSENSE_POLL` take a pointer path, `*0xC8B30+0*+0xC*+0x7FC` = the scene
machine's current scene, its judge object (+0xC), the life float (+0x7FC); `*PTR`
reads the word at PTR, `+off` adds, a later `*` reads again. Hold the life with
`EMOTIONALSENSE_POKE='*0xC8B30+0*+0xC*+0x7FC:0x42C80000'`.

The opening is **74.14 s** of video: 70.10 s of opening PSMF plus a 4.04 s logo,
measured from the file headers. It now runs at 59.94 frames per second. The
player decodes one 2048-sample ATRAC block every 2.78 frames, as PPSSPP does,
instead of one per frame; the earlier "~21.5 fps is the native rhythm" reading
was the player in permanent recovery. The song-select preview uses the same
player and has the same cadence.

The GE interpreter still ignores texture-coordinate generation (`TEXFUNC`) and
a few rarely used commands (block transfer, stencil, masks). Gameplay does not
use them; some menu effects might.

`--internal-scale N|auto` (or `EMOTIONALSENSE_INTERNAL_SCALE`) draws into a
`480N x 272N` target instead of 480x272 and presents that, so the geometry is
rasterised at N times the resolution. The default is `auto`: the smallest
scale that covers the window height, which is 4 in a 1080p full-screen window
and 1 when there is no window (headless runs are unaffected). The game samples its textures 1:1 with
nearest filtering, so on its own this only sharpens edges that the GE draws as
geometry; the extra detail comes from the texture work in the next sections.
The software renderer stays at 1x. At 4x the renderer still runs at ~150
frames per second headless, so 60 fps is not at risk. The window follows the
internal scale when `--scale` is not given, and never grows past the usable
screen height, so ask for `--scale N` next to `--internal-scale N` to get an
exact 1:1 window.

`--tex-filter nearest|linear|auto` overrides the game's own TEXFILTER bit
(`EMOTIONALSENSE_TEX_FILTER` sets the same thing). `auto` is the default and
does what the GE was told to do, with one exception: at 2x and above the
decoded video is sampled bilinear, because it is a 480x272 frame stretched
over the whole screen and nearest turns it into visible blocks. `linear`
smooths everything and is the better-looking choice for the art too. Whole-pixel window scaling is used
only when the window is an exact multiple of the frame, so a 1088-tall 4x
frame on a 1080p screen is scaled by aspect instead of losing its bottom rows.

### Texture dump

`EMOTIONALSENSE_TEX_DUMP=<dir>` writes every texture the game repeats, once, as
`<key>.png` plus an `index.txt` with the address, format, size and scene of
each. The key is content, CLUT, format and size, never the address, so the same
art lands in the same file on another screen or another run.

Textures generated per frame (text, counters) never repeat with the same key
and are skipped; video is converted into VRAM and skipped by address. A texture
is written on its third frame, so a static one appears and a per-frame one does
not.

The routes in `scripts/dump/` (listed in `scripts/dump/routes.txt`) walk the game through its menus,
every FreeStyle list, the studio and gameplay in each style; each one is run with
`EMOTIONALSENSE_TEX_DUMP=<dir>` and `EMOTIONALSENSE_SCRIPT=@scripts/dump/<name>.script` on a copy of a
data folder, and the dumps are merged by `launcher/es_texpack.py` (`merge_dump`).

### Texture pack

`<data>/textures/<key>.png` replaces a texture at upload, at any integer
multiple of its original size. Same key as the dump, so a pack is just a dump
directory with the files upscaled — no index and no manifest, and a file that
is not there means "keep the original". `EMOTIONALSENSE_TEX_PACK=<dir>` points
at a pack somewhere else.

The UVs stay in original texels: the vertices are scaled as they are emitted
and the shader sees the pack's size, so wrapping and clamping keep working.
Decoding is lazy, once per texture on its first frame, and the original stays
in use until the replacement is ready. `EMOTIONALSENSE_TEX_PACK_MAX=<MiB>`
(default 1024) caps the decoded size; past it the game keeps the original art
and says so once.

**Experimental, not in v0.1.** The launcher has a hidden option that builds the pack itself; it
appears only when `ES_TEXPACK_PROTO=1` is set in the environment (the Options row and
`--build-textures`; without the variable nothing is downloaded or shown). Packs made by hand
(`<data>/textures/` or `texture_pack` in `launcher.json`) work normally. The release packages do not
contain `texpack/` or `scripts/dump/`; run the launcher from a clone to try it. A later version is
meant to build the pack with TextureDAT2 on the GPU. What the experimental path does
(`launcher/es_texpack.py`): it downloads waifu2x-ncnn-vulkan
and Real-ESRGAN-ncnn-vulkan (pinned URLs and sha256), walks the game along the routes in
`scripts/dump/` (menus, option, lounge, all five FreeStyle lists, studio, gameplay in each style, the
result screens) on a scratch copy of the data -- with a throwaway profile registered in the scratch when
the player has no save yet (`scripts/dump/profile.script`) -- merges the dumps and runs
`texpack/make_tex_pack.py` in the **default profile**, which treats each texture class with its own
method (`texpack/tex_classes.txt`, made by `texpack/classify_textures.py`):

| Class | Textures | Method |
|---|---|---|
| track (notes, gear, combo, judgments, bars, fever, pause menu: everything written while scene 3 is current) | 32 | 4xTextureDAT2_otf, 100 % |
| interface (panels, buttons, fonts, icons, logos, result effects) | 150 | 4xTextureDAT2_otf 50 % mixed with Lanczos 50 % |
| illustration (covers and discs, eyecatch pictures, character art, backgrounds) | 104 | waifu2x cunet, noise 0 |

All three use the same alpha recipe (the colour of transparent texels is bled in from the nearest
visible ones, the alpha goes through the model as a grey picture, no division). The reference pack of
this project is built on a GPU with TextureDAT2 through ONNX Runtime
(`--model texturedat2-onnx`, CC BY 4.0 model, weights pinned by sha256): 286 textures in about 5 minutes
on an RTX 3060, 145 MiB on disk, 702 MiB decoded, 59.9 fps in gameplay at internal scale 4. TextureDAT2 on
CPU takes about an hour (62 minutes measured on 16 threads for the track and interface classes), so the
launcher uses `--fast-ncnn`: Real-ESRGAN (Vulkan) for those two classes. ncnn runs one worker thread per
stage (`-j 1:1:1`) and one batch per image size: with the default `-j 1:2:2`, 18 of 23 images of size
512x256 differed between two runs. The default profile is deterministic (two runs, byte-identical files).

`texpack/make_tex_pack.py <dump> <pack> --profile dat2` keeps the older single-model
mode. The software renderer ignores packs; the pack's decoded size is capped by
`EMOTIONALSENSE_TEX_PACK_MAX` (default 1024 MiB).

## How correctness is decided

There is no golden output to diff against: the thing being recompiled is a
game. Correctness is decided by four measurements, all of them repeatable on a
machine that has the disc.

**Scripted routes.** `scripts/*.script` drive the game by guest state, not by
frame count: each step is anchored to the first vblank where the scene word at
`0x0805E874` changes, so a longer opening does not break them.
`scripts/check_scripts.sh` runs five of them headless (`--renderer null`,
`SDL_AUDIODRIVER=dummy`) and requires every one to reach gameplay and exit 0:

| route | what it proves |
|---|---|
| `gameplay_fast.script` | boot → title → select → gameplay |
| `screens_fast.script` | the menu screens in between |
| `freestyle_4t.script` | FreeStyle 4T list, 41 songs, enter a song |
| `freestyle_select.script` | the style filter (4T → 3.2T → 4.2T) |
| `mode_42t.script` | PLAY MODE SELECT → 4.2T → gameplay |

**The same frames after a change.** With `EMOTIONALSENSE_CLOCK=virtual` the
guest clock is deterministic, so a route must reach each scene at the same
vblank as before the change. A regression that changes timing shows up as a
different frame number, not as a crash.

**Against PPSSPP.** PPSSPP was booted, navigated by game state, and its
screenshot, GE frame dump and per-frame video and audio call counts were compared
with ours (research scripts, not part of the repository). The track and HUD regions
match the reference within 21/255 per channel.

**The game's own verdict.** The disc check is the game's anti-piracy test: it
reads a real sector through `umd0:` and compares timing. `EMOTIONALSENSE_TRACE_UMD=1`
prints the verdict, and a passing run reports `a2`, `a3` and `a4` above 20, 30
and 50 while the punishment counter at `0x130304` stops advancing. Audio is
checked by rate instead of by ear: the mixer must produce 172.3 blocks per
second and Vaudio 21.53, measured from the HLE trace (see `audio.md`).

`ctest` covers the toolkit itself (rasteriser and emitter), not the game.

What is **not** verified automatically: `check_scripts.sh` needs the extracted
ISO, so it does not run in CI and a green CI run says nothing about the game;
the guest frame drifts by one or two between two identical virtual-clock runs,
so animated screens are compared by eye rather than by image diff; the GPU
paths have only been exercised on one vendor; and real Windows is tested by
hand, not by wine alone.

## Debugging

```bash
EMOTIONALSENSE_TRACE_CLOCK=1 ./build/game/EmotionalSense-P3-game     # guest vs real clock
PSPRECOMP_TRACE_GE=2300-2400 ./build/game/EmotionalSense-P3-game     # GE commands for a vblank range
EMOTIONALSENSE_SHOT=2327,4000,9300 EMOTIONALSENSE_SHOT_DIR=/tmp ./build/game/EmotionalSense-P3-game
EMOTIONALSENSE_SCRIPT=@scripts/gameplay.script ./build/game/EmotionalSense-P3-game
EMOTIONALSENSE_TRACE_HLE_THREAD=all ./build/game/EmotionalSense-P3-game  # every HLE call
EMOTIONALSENSE_TRACE_AUDIO=1 ./build/game/EmotionalSense-P3-game        # ring levels, underruns
```

`scripts/gameplay.script` drives boot → song select → gameplay relative to the
scene, so it survives a change in the opening's length. `--frames N --capture
out.ppm` runs headless (with `SDL_VIDEODRIVER=dummy`) and writes a PPM.
`EMOTIONALSENSE_CLOCK=virtual` decodes the opening and reaches the title and
gameplay; the guest clock advances 16.7 ms per vblank (the host runs faster
than real because virtual mode does not wait for wall time) and two runs can
differ by a few frames.


### Static relocation

PSP relocations are *segment-relative*. DJMAX's `.data` is a second segment
at `0xCB540`, so every reference into it must be relocated **before**
recompiling (the recompiler bakes addresses into C). The pipeline does this
in [`launcher/es_prxreloc.py`](../launcher/es_prxreloc.py) (27,358 relocations,
4,757 non-trivial). Without it the game called an instruction word
(`0x8FBF0000`) as a function pointer.

## Building for Windows from Linux (development)

The same C sources build for Windows with MinGW-w64; the window, input and audio backends are
the same SDL2 code used on Linux.

```bash
./scripts/install-deps-mingw.sh     # SDL2 + FFmpeg + Vulkan import lib into work/tools/mingw-deps
cmake -B build-win -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=cmake/mingw-w64.cmake \
  -DEMOTIONALSENSE_GEN_DIR=$PWD/build/gen
ninja -C build-win
```

`build-win/game/EmotionalSense-P3-game.exe` needs `SDL2.dll`, the FFmpeg DLLs and
`libwinpthread-1.dll` next to it. The generated C is shared with the Linux build, so run the
Linux pipeline once first. This is for development: the released Windows packages are built by
`release.yml` with the kit (llvm-mingw), and the launcher's data folder is
`EmotionalSense-P3-data` next to the program. The save directory is `ULKS46236<>` on the PSP;
`<` and `>` cannot appear in a Windows file name, so it is stored as `ULKS46236%3C%3E`. The two
are the same save: copy `SD.BIN` between them to move a save across platforms.

Known limits: the Windows game build was validated under Wine and the Windows launcher by CI, not
yet on real Windows hardware; the Vulkan backend needs a driver that exposes `vulkan-1.dll` and
falls back to software rendering when `vkCreateInstance` fails.

## The six targets

`scripts/make_kit.py --target linux-x86_64|linux-aarch64|windows-x86_64|windows-arm64` assembles the
kit of one target (zig for Linux, llvm-mingw for Windows); `scripts/build_launcher.py --target ...`
wraps it with PyInstaller (AppImage and `tar.gz` on Linux, `zip` on Windows). Each target is built
natively by `release.yml` (the arm64 runners are free for public repositories only). The runtime has
no architecture-specific code (no intrinsics, no inline assembly); the recompiler's output is
byte-identical when it runs on x86-64 and on aarch64 (checked by running an aarch64 build of
`allegrexrecomp` under `qemu-aarch64` on the real EBOOT: 91 files, `diff -r` empty).

## Releasing

`scripts/release.sh 0.2.0` checks the tree is clean, sets `project(VERSION)` in `CMakeLists.txt`,
moves the `Unreleased` section of `CHANGELOG.md` into a dated release section, and runs the tests.
It does not tag or push; it prints the commands. Pushing a `v*` tag runs `release.yml`, which checks
the tag against `CMakeLists.txt` and `CHANGELOG.md`, builds the source tarball and the six packages
(each with its kit), runs `scripts/release_guard.py` over every artefact, and opens a **draft**
release with the notes and `SHA256SUMS`. Publishing the draft is the maintainer's action.
