#!/bin/sh
# Cross-build dependencies for the Windows port (see cmake/mingw-w64.cmake).
#
# The compiler comes from the distro; SDL2 and FFmpeg ship prebuilt MinGW
# import libraries, and the Vulkan loader import library is generated from
# the declarations in psprecomp/src/vk_mini.h. Everything lands in
# work/tools/mingw-deps (ignored by git), so a clean checkout only needs this
# script once.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
deps=${MINGW_DEPS:-$root/work/tools/mingw-deps}
sdl_ver=2.30.9
ffmpeg_url=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-lgpl-shared-8.1.zip

command -v x86_64-w64-mingw32-gcc >/dev/null || {
    echo "missing x86_64-w64-mingw32-gcc; install it first:" >&2
    echo "  sudo pacman -S mingw-w64-gcc" >&2
    exit 1
}

mkdir -p "$deps"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if [ ! -f "$deps/sdl2/cmake/sdl2-config.cmake" ]; then
    echo "fetching SDL2 $sdl_ver"
    curl -fsSL -o "$tmp/sdl2.tar.gz" "https://libsdl.org/release/SDL2-devel-$sdl_ver-mingw.tar.gz"
    tar -xzf "$tmp/sdl2.tar.gz" -C "$tmp"
    rm -rf "$deps/sdl2"
    mv "$tmp/SDL2-$sdl_ver" "$deps/sdl2"
fi

if [ ! -f "$deps/ffmpeg/lib/pkgconfig/libavcodec.pc" ]; then
    echo "fetching FFmpeg"
    curl -fsSL -o "$tmp/ffmpeg.zip" "$ffmpeg_url"
    unzip -q "$tmp/ffmpeg.zip" -d "$tmp"
    rm -rf "$deps/ffmpeg"
    mv "$tmp"/ffmpeg-n8.1-latest-win64-lgpl-shared-8.1 "$deps/ffmpeg"
fi

# The loader is a system DLL (the video driver provides it), so only the
# import library is needed at link time.
if [ ! -f "$deps/lib/libvulkan-1.a" ]; then
    echo "generating the Vulkan loader import library"
    mkdir -p "$deps/lib"
    {
        echo "LIBRARY vulkan-1.dll"
        echo "EXPORTS"
        grep -o "VKAPI_CALL vk[A-Za-z0-9_]*" "$root/psprecomp/src/vk_mini.h" |
            sed 's/VKAPI_CALL //' | sort -u
    } > "$deps/vulkan-1.def"
    x86_64-w64-mingw32-dlltool -d "$deps/vulkan-1.def" -l "$deps/lib/libvulkan-1.a"
fi

echo "OK: $deps"
echo "configure with:"
echo "  cmake -B build-win -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/mingw-w64.cmake \\"
echo "        -DEMOTIONALSENSE_GEN_DIR=$root/build/gen"
