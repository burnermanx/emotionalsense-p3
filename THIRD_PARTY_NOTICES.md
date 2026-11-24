# Third-party notices

EmotionalSense itself (this repository, including the launcher and the
`psprecomp` runtime) is MIT licensed (`LICENSE`). The launcher package
carries the following third-party software. The game, its data and anything
derived from them are **not** part of any release.

| Component | Where | Licence | Source / notes |
|---|---|---|---|
| **pspdecrypt** | `kit/bin/pspdecrypt` (separate program, run by `subprocess`, never linked) | **GPL-3.0** | https://github.com/John-K/pspdecrypt at revision `c156627db7634d395c380c0a9589130f603307fc`; the source archive of that revision is attached to every release; see `licenses/pspdecrypt-SOURCE-OFFER.txt` |
| **libchdr** | `kit/bin/libchdr.so` or `chdr.dll`, loaded by the launcher with `ctypes` to read CHD disc images | BSD-3-Clause (`licenses/libchdr-LICENSE.txt`) | https://github.com/rtissera/libchdr at revision `47d794151b2b64f6aba60304e6480f4f5a2bc24b`, built by `release.yml`. It carries, compiled in: LZMA SDK 26.02 (public domain), miniz 3.1.2 (MIT), zstd 1.5.7 (BSD-3-Clause, dual GPL-2.0, used under BSD), dr_flac (public domain or MIT-0); their texts are in `licenses/libchdr-deps-LICENSES.txt`. No licence asks for the source to be attached |
| SDL2 | shared library beside the game | zlib | https://libsdl.org |
| FFmpeg (libavcodec, libavutil, libswresample) | shared libraries beside the game | **LGPL-2.1-or-later** (build without `--enable-gpl`) | BtbN/FFmpeg-Builds `lgpl-shared`, FFmpeg n8.1.3-35-gf1acb0a266 (https://github.com/BtbN/FFmpeg-Builds, source https://git.ffmpeg.org/ffmpeg.git). Dynamically linked: replace the shared libraries in the game folder to use another build |
| zig 0.17.1 (Linux kit) | `kit/compiler/` | MIT; contains LLVM/clang/lld (Apache-2.0 with LLVM exceptions) and libc sources (glibc LGPL-2.1, musl MIT, mingw-w64) | https://ziglang.org |
| llvm-mingw (Windows kit) | `kit/compiler/` | Apache-2.0 with LLVM exceptions; mingw-w64 (ZPL-2.1, public domain, BSD parts), winpthreads (MIT) | https://github.com/mstorsjo/llvm-mingw |
| Python 3.13 and Tcl/Tk | launcher runtime | PSF-2.0; Tcl/Tk BSD-style | https://python.org, https://tcl.tk |
| PyInstaller (bootloader) | launcher executable | GPL-2.0 **with the bootloader exception** (the produced executable is not GPL) | https://pyinstaller.org |
| Pillow | launcher | MIT-CMU (HPND) | https://python-pillow.org |
| zig 0.17.1 / llvm-mingw (aarch64 and Windows arm64 kits) | `kit/compiler/` | as above | the same projects, aarch64 builds; SDL2 for Windows arm64 is built from the SDL2 2.30.9 source release |
| Exo 2 Italic (subset) | `assets/generated/Exo2-Italic.ttf` | SIL OFL 1.1 | https://github.com/google/fonts (`ofl/exo2`), commit pinned in `launcher/assets/fonts.json`; `licenses/OFL-exo2.txt` |
| Noto Sans JP, Noto Sans KR (subsets) | `assets/generated/NotoSans*-sub.ttf` | SIL OFL 1.1 | https://github.com/google/fonts (`ofl/notosansjp`, `ofl/notosanskr`); `licenses/OFL-notosans*.txt` |
| Vulkan loader | **not shipped**: the system's `libvulkan` is used | Apache-2.0 | provided by the graphics driver |

## Exit dialog text

`game/exit_text.c` holds the five "Quit the game? / YES / NO" prompts as 8-bit
coverage masks rasterised from **Noto Sans CJK Bold** (SIL OFL 1.1,
https://github.com/notofonts/noto-cjk) by `scripts/gen_exit_text.py`. The texts are
ours; the OFL permits embedding rasterised glyphs, and the masks are not a
font. No game art is used.

`game/speed_glyphs.c` holds the glyphs of the Respect speed label ("AV 2.3": `A`, `V`, the digits and the point) as 8-bit
coverage masks rasterised from the same **Noto Sans CJK Bold** (SIL OFL 1.1) by `scripts/gen_speed_glyphs.py`.

The fonts are subset to the characters the launcher's own text needs; the OFL
permits this and the subsets keep the reserved font name rules (they are not
sold on their own). Translations for Spanish, Japanese and Korean were written
by an AI assistant and are marked in their files as needing native review.
