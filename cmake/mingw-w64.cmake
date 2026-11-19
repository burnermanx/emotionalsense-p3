# Cross-compile the game for 64-bit Windows with MinGW-w64 (GCC).
#
#   scripts/install-deps-mingw.sh
#   cmake -B build-win -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/mingw-w64.cmake \
#         -DEMOTIONALSENSE_GEN_DIR=$PWD/build/gen
#
# The compiler is the distro's mingw-w64-gcc; SDL2, FFmpeg and the Vulkan
# loader import library come from work/tools/mingw-deps (see the script).
set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)

set(TOOLCHAIN_PREFIX x86_64-w64-mingw32)
set(CMAKE_C_COMPILER ${TOOLCHAIN_PREFIX}-gcc)
set(CMAKE_RC_COMPILER ${TOOLCHAIN_PREFIX}-windres)

set(MINGW_DEPS "${CMAKE_CURRENT_LIST_DIR}/../work/tools/mingw-deps"
    CACHE PATH "Prebuilt MinGW dependencies")
set(CMAKE_FIND_ROOT_PATH "${MINGW_DEPS}" "/usr/${TOOLCHAIN_PREFIX}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# FindPkgConfig must not see the host's .pc files.
set(ENV{PKG_CONFIG_LIBDIR} "${MINGW_DEPS}/ffmpeg/lib/pkgconfig")
set(SDL2_DIR "${MINGW_DEPS}/sdl2/cmake" CACHE PATH "SDL2 MinGW package")
