# Changelog: EmotionalSense-P3

All notable changes to this project are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Nothing derived from the game is ever published: no executable, no generated C,
no extracted data, no textures. Releases carry the launcher and the source; you
build the rest from your own disc.

## [Unreleased]

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
