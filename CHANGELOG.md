# Changelog: EmotionalSense-P3

All notable changes to this project are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Nothing derived from the game is ever published: no executable, no generated C,
no extracted data, no textures. Releases carry the launcher and the source; you
build the rest from your own disc.

## [Unreleased]

### Added

- **RecompLauncher support**: `recomplauncher.json` ships at the top of every package and at the root of
  the AppImage. RecompLauncher compares an .iso with the Redump dump (its verified badge; the game's own check still decides), builds
  the game window-less with `--prepare --progress-lines` (`progress <0..1> <step>` lines; an already
  prepared game answers at once), keeps everything in its data folder (`--data`), starts it with `--run`
  and shows the launcher options on the game's page. Without the new flag nothing changes.
- **ZSO images** (`ZISO`, the LZ4 variant of CSO: `maxcso --format=zso`) are read in place like CSO,
  with a pure Python LZ4 block decoder (no new dependency); the "ZSO not supported" message is gone.
  Tested byte for byte against the ISO, with blocks from the reference `lz4` compressor.

- **Speed system** Respect (default) or Original (`EMOTIONALSENSE_SPEED`, `--speed`, launcher row): Auto Velocity,
  R and L in steps of 0.1 from 1.0 to 7.0, the chosen number is the apparent speed on every song (lane speed =
  speed x 100 / the chart's first tempo; tempo changes inside a song keep the game's proportion); a label `AV 2.3`
  replaces the game's speed sprite, which only has art for .0 and .5. The chosen speed is remembered between
  songs and sessions in `<data>/es_speed.json`; the game's own index byte always holds a valid value (`docs/speed.md`).
- **MISSION always plays Original**: in the mission mode the Fever, scoring and speed options do not apply (the
  missions are written for the PSP rules); no Respect record is written for a mission.
- `EMOTIONALSENSE_SHOT_OVERLAY=1` puts the host overlay into captures (test aid).
- **Speed in tenths in the song list and the pause menu** (Respect): R and L in the list step by 0.1, and the list's header
  pill, its "SPEED" badge and the pause menu's pill show `AV 2.3` (the host paints over the digits of the game's sprites, which
  only have art for .0 and .5). The save keeps receiving only what the game writes (byte-identical `SD.BIN` for the same inputs).
  The EFFECTOR menu (the list's triangle) steps its SPEED slot by 0.1 as well, with the same shared value; R / L during the song
  were already steps of 0.1 in every style (4T, 6T, 3.2T, 4.2T, 6.2T).
- **Button symbols** (`button_style`: auto, playstation, xbox, steamdeck, switch; `EMOTIONALSENSE_BUTTONS`, `--buttons`, launcher
  row): the game's button prompts and the launcher's footer and hints show the buttons of the chosen pad (Xbox letters coloured,
  Steam Deck white, Switch positional B A Y X), composed with the confirm button. `auto` follows the controller used last
  (`SDL_GameControllerGetType`, the Steam Deck by its ids or `SteamDeck=1`; Windows/XInput is Xbox). Face buttons are read by
  position on every pad (`SDL_GAMECONTROLLER_USE_BUTTON_LABELS=0`). Vulkan only (`docs/buttons.md`).
  The pills the game draws for the buttons follow the style too, whatever the confirm button: SELECT is the View icon (two
  overlapping squares) on Xbox and Steam Deck and a minus on Switch, and the shoulder pills read L1 / R1 (PlayStation and
  Steam Deck), LB / RB (Xbox) or L / R (Switch); the icons and letters are drawn by code (PlayStation keeps the word SELECT).
  START has no pill in any screen reached, so the Menu icon / plus are only in the table.
- **Triggers in the .2T modes** (`triggers_2t`: stick (default) or lr; `EMOTIONALSENSE_TRIGGERS_2T`, `--triggers-2t`, launcher row):
  LT / RT are the analog stick's left / right in 3.2T, 4.2T and 6.2T songs; the keyboard's E / R always are. `lr` is the 0.1 behaviour.
- **AppImage updates**: the AppImages carry their update information
  (`gh-releases-zsync|burnermanx|emotionalsense-p3|latest|EmotionalSense-P3-*-<target>.AppImage.zsync`) and each
  release publishes the `.zsync` next to them, so AppImageUpdate and similar tools (Gear Lever, AppImageLauncher)
  can update an AppImage in place, downloading only what changed. Takes effect from the first release built with it.

### Changed

- **No repeated "new content" notices with everything unlocked.** The result screen used to announce, after the level up,
  "You have acquired a new character / SS pattern ..." and "3.2 Trax / 4.2T unlocked" even though the unlock options had made
  all of it available from the start (the game decides "new" from the player's level and the rows of its unlock table, not
  from the unlock bits). With the unlock on those windows are not opened; the level-up window and the item-box reveal stay,
  and so does the special-mission notice (missions are not part of the unlock). With `EMOTIONALSENSE_UNLOCK=0` the game's notices
  appear as before (`EMOTIONALSENSE_UNLOCK_ITEMS=0` keeps the item notices). The "You have acquired a new HD / SS pattern"
  window of the first play of a chart is skipped too (the unlock already had the pattern). The special-mission notice stays: the
  game raises it only for a mission that song just unlocked, so it is not shown once every mission is unlocked. `SD.BIN` is written exactly as before
  (`docs/unlock-table.md`).
- The 2T triggers in `stick` mode no longer depend on the song player's flag, only on the gameplay scene and the style byte.
- The title screen's exit dialog looks like the game's own boxes: an opaque black panel with a thin gold outline, the question in
  white with wide spacing, YES / NO in condensed bold capitals with the chosen one on an orange bar, nothing dimmed and no button hints
  (drawn by code at any internal scale and on the software renderer; the texts are rasterised by `scripts/gen_exit_text.py`).
- The launcher's option hint area shows three lines.
- Development version is now `0.2.0-dev` (`--version`, the launcher, the kit manifest);
  `scripts/release.sh` drops the suffix when a release is prepared.

## [0.1.0] - 2026-11-19

First release. DJMAX Portable 3 (`ULKS46236`) boots, plays and saves from your own
ISO, on Linux and Windows, x86-64 and arm64.

### Added

- **Launcher** (Tk window with an original theme, keyboard and gamepad driven, five
  languages: Portuguese of Brazil, English, Spanish, Japanese, Korean). It extracts the
  ISO, decrypts `EBOOT.BIN`, recompiles the game's code to C and compiles it with the
  compiler that travels in its kit (zig on Linux, llvm-mingw on Windows): no CMake, git,
  system compiler or network needed. Command line for scripts (`--iso --prepare`, `--run`,
  `--backup-save`, `--selftest`, `--update`, `--migrate-legacy`).
- **CHD images**: the launcher reads CHD files (`chdman createdvd` or `createcd`, any
  compression) in place of the ISO, with libchdr (BSD-3-Clause) loaded from the kit; the
  file is only read, never converted to a temporary ISO, and the check against the known
  dump is made on the decoded data. `--iso` accepts `.chd`.
- **Quiet by default**: the launcher and the game print nothing to the terminal unless a
  fatal error stops them; `--verbose` (launcher, passed to the game), `--verbose` or
  `EMOTIONALSENSE_LOG=1` (game) and any `EMOTIONALSENSE_TRACE_*` turn the log on. The crash
  report still carries the last log lines. Other libraries' output is untouched.
- **Six packages** (`EmotionalSense-P3-<version>-{windows-x86_64.zip, windows-arm64.zip,
  linux-x86_64.AppImage, linux-x86_64.tar.gz, linux-aarch64.AppImage,
  linux-aarch64.tar.gz}` + `SHA256SUMS`), built natively by `release.yml`;
  `scripts/release_guard.py` fails the release if any artefact holds something derived
  from the game.
- **Portable layout**: everything the launcher and the game write lives in
  `EmotionalSense-P3-data` next to the program (next to the `.AppImage` file, not inside
  it); nothing in the home folder, `%APPDATA%`, `%LOCALAPPDATA%` or the XDG folders. A
  folder that cannot be written to is reported in five languages instead of falling back
  silently; data of earlier development versions is offered for copying, never moved.
- **Crash and hang reports** (`<data>/crashes/crash-<date>-<time>.txt`, plus a minidump on
  Windows): version, system, graphics card, options, guest registers and call stack, the
  last firmware calls and log lines, a native backtrace with the build-id. The launcher
  offers to open the folder, copy the report or open a prefilled GitHub issue
  (`.github/ISSUE_TEMPLATE/crash.yml`); nothing is sent automatically.
- **Fever mode** Respect (default) or Original (`EMOTIONALSENSE_FEVER`, `--fever`).
- **Scoring system** Respect (default, 0 to 1,000,000 from the judgment percentage) or
  Original; display only, both records are kept (`EMOTIONALSENSE_SCORE`, `--score`).
- **Alt+Enter** borderless full screen, "Start in" and "Full screen rate" options;
  **quit from the title** with a confirmation.
- **Confirm button** circle or cross (automatic by language), applied to menus only.
- Optional **data update 1.02** (`--update`, options row); the update's own patcher is never run.
- Internal resolution up to 4x with sub-pixel lane positions; background-video smoothing;
  texture packs; unlock of every song, item and the 4.2T / 6.2T modes (`docs/unlock-table.md`).
- Diagnostics: scene-relative input scripts, polling and poking through pointer paths,
  traces for clock, audio, MPEG, UMD (`docs/diagnostics.md`).
- **CSO images** (CISO v1/v2, as made by PPSSPP/maxcso/ciso) are read by sector like CHD, with no
  temporary ISO (`launcher/es_cso.py`; byte-for-byte test against the ISO). ZSO (LZ4) is recognised
  and refused with a clear message in five languages.
- **Already extracted data is reused**: a data folder that holds the extracted disc is not asked
  for an image again (a new launcher version only redoes the steps after the extraction), and an
  extracted game folder (`PSP_GAME` of ULKS46236) next to the program is offered as the source: it is
  checked and copied, never moved or changed. Incomplete or wrong folders are refused with the reason.
- `--sync-log` / `EMOTIONALSENSE_SYNC_LOG=1`: write the clock, vblank and audio traces to
  `<data>/logs/sync-<time>.log` for a timing report (see `docs/diagnostics.md`).

### Changed

- The guest runs on a monotonic clock at the PSP's native 59.94 Hz; one audio device with
  per-channel rings and a single prefill target; the window presents a completed frame
  instead of reading VRAM while the guest writes it.
- Executables carry the application name: the launcher is `EmotionalSense-P3` and the game
  it starts is `EmotionalSense-P3-game`.

### Fixed

- **Steam Deck (Game Mode) crash of the AppImage**: the launcher started the game and every other
  program with PyInstaller's `_internal` folder first in `LD_LIBRARY_PATH`, so the build machine's
  libstdc++, libz, libzstd, libexpat... shadowed the system's inside the game; a graphics driver
  that then failed to initialise made the Gamescope WSI layer assert and abort the process. Every
  program the launcher starts now gets the environment the system gave the launcher
  (`launcher/es_env.py`, covered by a test).
- Launcher menus react at once: a key or pad press redraws within one 60 Hz frame (the frame goes
  to Tk as raw PPM, about 6x cheaper than PNG+base64; no timer wait), the pad is polled at 120 Hz
  and holds repeat like console menus (350 ms, then about 13 a second, one step per poll).
- The song unlock used to write 1500 contiguous bytes at the wrong address every frame,
  which corrupted the music-select list and hung the game.
- A `jal` is no longer split from its delay slot when a `lui`+`addiu` constant happens to
  point at that slot; this had kept the track, the notes and the HUD off screen.
- The raw `umd0:` sector device the disc check reads is emulated, so the anti-copy check
  passes without touching the ISO.
- The game never leaves an explicit `--data` folder for another one.
- The game is recognised as soon as the file dialog returns (the screen was only redrawn after the
  next key or pad press).
- **Vulkan start-up is now visible and no longer exposed to Steam's layers/runtime** (Steam Deck):
  the game writes `<data>/logs/renderer.log` on every start (which renderer, the relevant
  environment, each Vulkan step with its `VkResult`); when Vulkan does not start the launcher
  shows a dialog (five languages) with the report and an issue link; `--renderer vulkan` is strict
  (exit code 3, no silent software fallback). The offscreen Vulkan instance turns the implicit
  layers off (a failing driver made the Gamescope WSI layer abort the process: reproduced with a
  failing test driver), and the game no longer inherits Steam's runtime folders in
  `LD_LIBRARY_PATH` or its overlay in `LD_PRELOAD`.
- **Launcher on Windows (Proton): the menu scrolled by itself.** A gamepad input that is already
  "pressed" when the controller is first seen (a second, phantom device, a raw HID node) is now
  ignored until it has been released once, directions are tracked per device with a hysteresis
  on the stick, the controller list is no longer closed and reopened every second when a raw
  joystick that is not a game controller is present, and the pad is mute while a system dialog
  (file picker, crash dialog) is open and until what is held is released afterwards.
- **Launcher: every screen is as fluid as the options screen.** The panel gradient was drawn
  pixel by pixel in Python on every frame (60 to 120 ms for the setup, play and loading screens)
  and is now built per row and cached; a screen change shows its first frame inside one 60 Hz
  period; transitions are paced at 60 Hz (they ran at about 30); the loading screen follows the
  worker thread at 8 ms.
- **Linux: the guest's time base was the system clock (`CLOCK_REALTIME`).** Guest time, frame
  pacing and the audio grid now use `CLOCK_MONOTONIC`, like the performance counter on Windows,
  so a time-sync step or a resume cannot move the game's timeline against the music.

### Save compatibility

- The unlock is written into the song table, which is part of `SD.BIN`: a save written
  with the unlock on carries the unlocked charts to any later version and to the PSP. The
  launcher's "Back up save" button keeps a copy first. Respect scores are kept apart, in
  `es_scores.json`.

### Known limitations

- The Windows packages were validated under Wine and by CI, not yet on real hardware; the
  arm64 packages were built, not run on arm64 hardware.
- The Vulkan backend needs a driver that exposes `vulkan-1.dll` (Windows) or
  `libvulkan.so.1` (Linux); it falls back to software rendering when it cannot start.
