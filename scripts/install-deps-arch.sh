#!/usr/bin/env bash
# emotionalsense -- install everything needed to build on Arch Linux
# (and derivatives: CachyOS, EndeavourOS, Manjaro...).
#
#   ./scripts/install-deps-arch.sh
#
# What each package is for:
#   base-devel        gcc, make, binutils, patch -- the C toolchain
#   git               submodules + fetching pspdecrypt at build time
#   cmake, ninja      building psprecomp, allegrexrecomp and the game
#   clang             compiling pspdecrypt (its Makefile uses clang)
#   openssl, zlib     pspdecrypt links libcrypto and libz
#   sdl2-compat       window, input, audio for the native game (SDL2 API on SDL3)
#   ffmpeg            libavcodec: ATRAC3+ / MPEG decoding for music and videos
#   python, tk        the launcher (Tk window)
set -euo pipefail

PKGS=(
    base-devel git cmake ninja clang
    openssl zlib
    sdl2 ffmpeg
    python tk
)

if ! command -v pacman >/dev/null; then
    echo "pacman not found -- this script is for Arch Linux and derivatives." >&2
    exit 1
fi

# Filter via `pacman -T` so drop-in replacements (e.g. zlib-ng-compat on
# CachyOS providing zlib, or sdl2-compat providing sdl2) are recognized as
# already satisfied and do not trigger a package conflict with `pacman -S`.
mapfile -t MISSING < <(pacman -T "${PKGS[@]}" || true)

if [ "${#MISSING[@]}" -eq 0 ]; then
    echo ":: All dependencies are already installed."
else
    echo ":: Installing missing packages: ${MISSING[*]}"
    sudo pacman -S --needed --noconfirm "${MISSING[@]}"
fi

echo
echo ":: Done. Next:"
echo "     git submodule update --init --recursive"
echo "     python3 launcher/es_main.py     # the launcher"
echo "     python3 launcher/es_main.py --iso /path/to/game.iso --prepare"
