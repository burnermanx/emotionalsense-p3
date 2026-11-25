#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ABI="x86_64"
NDK="${ANDROID_NDK_HOME:-$HOME/Android/Sdk/ndk/27.2.12479018}"
API="26"
JOBS="$(nproc)"

usage() {
    cat <<EOF
Usage: $0 [--abi <x86_64|arm64-v8a>] [--ndk <path>] [--api <level>] [--jobs <n>]
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --abi)  ABI="$2"; shift 2 ;;
        --ndk)  NDK="$2"; shift 2 ;;
        --api)  API="$2"; shift 2 ;;
        --jobs) JOBS="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

case "$ABI" in
    x86_64)
        TARGET_TRIPLE="x86_64-linux-android"
        FFMPEG_ARCH="x86_64"
        OPENSSL_TARGET="android-x86_64"
        TERMUX_ARCH="x86_64"
        LLD_EMULATION="elf_x86_64"
        RT_ARCH="x86_64"
        EXPECTED_ELF_MACHINE="Advanced Micro Devices X86-64"
        ;;
    arm64-v8a)
        TARGET_TRIPLE="aarch64-linux-android"
        FFMPEG_ARCH="aarch64"
        OPENSSL_TARGET="android-arm64"
        TERMUX_ARCH="aarch64"
        LLD_EMULATION="aarch64linux"
        RT_ARCH="aarch64"
        EXPECTED_ELF_MACHINE="AArch64"
        ;;
    *)
        echo "Unsupported ABI: $ABI (expected x86_64 or arm64-v8a)" >&2
        exit 1
        ;;
esac

if [[ ! -d "$NDK/toolchains/llvm/prebuilt/linux-x86_64" ]]; then
    echo "Android NDK not found at: $NDK" >&2
    exit 1
fi

TOOLCHAIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
CC="$TOOLCHAIN/bin/${TARGET_TRIPLE}${API}-clang"
CXX="$TOOLCHAIN/bin/${TARGET_TRIPLE}${API}-clang++"
AR="$TOOLCHAIN/bin/llvm-ar"
NM="$TOOLCHAIN/bin/llvm-nm"
RANLIB="$TOOLCHAIN/bin/llvm-ranlib"
STRIP="$TOOLCHAIN/bin/llvm-strip"

for tool in "$CC" "$CXX" "$AR" "$NM" "$RANLIB" "$STRIP" cmake ninja make python3 readelf curl git tar xz ar; do
    if ! command -v "$tool" >/dev/null 2>&1 && [[ ! -x "$tool" ]]; then
        echo "Missing required tool: $tool" >&2
        exit 1
    fi
done

SDL2_VERSION="2.30.11"
SDL2_URL="https://github.com/libsdl-org/SDL/releases/download/release-${SDL2_VERSION}/SDL2-${SDL2_VERSION}.tar.gz"
FFMPEG_VERSION="7.1"
FFMPEG_URL="https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"
OPENSSL_VERSION="3.4.0"
OPENSSL_URL="https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}/openssl-${OPENSSL_VERSION}.tar.gz"
PSPDECRYPT_REPO="https://github.com/John-K/pspdecrypt.git"
PSPDECRYPT_REV="c156627db7634d395c380c0a9589130f603307fc"
TERMUX_REPO="https://packages.termux.dev/apt/termux-main"
PAGE_FLAGS="-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384"

DL="$ROOT/build-android/dl"
WORK="$ROOT/build-android/work/$ABI"
STAGE="$ROOT/build-android/stage/$ABI"
STAGE_JAVA="$ROOT/build-android/stage/sdl-java"
JNILIBS="$STAGE/jniLibs/$ABI"
KIT="$STAGE/kit/$ABI"

rm -rf "$JNILIBS"
mkdir -p "$DL" "$WORK" "$JNILIBS" "$STAGE/include" "$KIT/objs" "$KIT/include" "$STAGE_JAVA"

download_file() {
    local url="$1"
    local dest="$2"
    if [[ -f "$dest" ]]; then
        return 0
    fi
    echo "==> Downloading $(basename "$dest")"
    curl -fsSL -o "${dest}.tmp" "$url"
    mv "${dest}.tmp" "$dest"
}

echo "=== [1/7] SDL2 ${SDL2_VERSION} ($ABI) ==="
SDL2_TAR="$DL/SDL2-${SDL2_VERSION}.tar.gz"
SDL2_SRC="$DL/SDL2-${SDL2_VERSION}"
download_file "$SDL2_URL" "$SDL2_TAR"
if [[ ! -d "$SDL2_SRC" ]]; then
    tar -xf "$SDL2_TAR" -C "$DL"
fi

cmake -S "$SDL2_SRC" -B "$WORK/sdl2" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="$ABI" \
    -DANDROID_PLATFORM="android-$API" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_SHARED_LINKER_FLAGS="$PAGE_FLAGS" \
    -DSDL_STATIC=OFF \
    -DSDL_SHARED=ON \
    -DSDL_TEST=OFF
cmake --build "$WORK/sdl2" -j"$JOBS"
rm -rf "$WORK/sdl2-install"
cmake --install "$WORK/sdl2" --prefix "$WORK/sdl2-install"

cp "$WORK/sdl2/libSDL2.so" "$JNILIBS/libSDL2.so"
"$STRIP" --strip-unneeded "$JNILIBS/libSDL2.so"
rm -rf "$STAGE/include/SDL2"
cp -a "$WORK/sdl2-install/include/SDL2" "$STAGE/include/SDL2"
mkdir -p "$STAGE_JAVA/org/libsdl/app"
cp -a "$SDL2_SRC/android-project/app/src/main/java/org/libsdl/app/"*.java "$STAGE_JAVA/org/libsdl/app/"

echo "=== [2/7] FFmpeg LGPL ${FFMPEG_VERSION} ($ABI) ==="
FFMPEG_TAR="$DL/ffmpeg-${FFMPEG_VERSION}.tar.xz"
FFMPEG_SRC="$DL/ffmpeg-${FFMPEG_VERSION}"
download_file "$FFMPEG_URL" "$FFMPEG_TAR"
if [[ ! -d "$FFMPEG_SRC" ]]; then
    tar -xf "$FFMPEG_TAR" -C "$DL"
fi

rm -rf "$WORK/ffmpeg" "$WORK/ffmpeg-install"
mkdir -p "$WORK/ffmpeg"
(
    cd "$WORK/ffmpeg"
    "$FFMPEG_SRC/configure" \
        --prefix="$WORK/ffmpeg-install" \
        --enable-cross-compile \
        --target-os=android \
        --arch="$FFMPEG_ARCH" \
        --cc="$CC" \
        --cxx="$CXX" \
        --ar="$AR" \
        --nm="$NM" \
        --ranlib="$RANLIB" \
        --strip="$STRIP" \
        --disable-gpl \
        --disable-nonfree \
        --disable-programs \
        --disable-doc \
        --disable-everything \
        --enable-decoder=atrac3,atrac3p,h264 \
        --enable-shared \
        --disable-static \
        --disable-avformat \
        --disable-swscale \
        --disable-swresample \
        --disable-avfilter \
        --disable-avdevice \
        --disable-postproc \
        --disable-network \
        --disable-asm \
        --enable-pic \
        --extra-cflags="-O2 -fPIC" \
        --extra-ldflags="$PAGE_FLAGS"

    # Force clean Android SONAMEs (libavutil.so, libavcodec.so without .so.59)
    sed -i \
        -e 's/^SLIBNAME_WITH_VERSION=.*/SLIBNAME_WITH_VERSION=$(SLIBNAME)/' \
        -e 's/^SLIBNAME_WITH_MAJOR=.*/SLIBNAME_WITH_MAJOR=$(SLIBNAME)/' \
        -e 's/^SLIB_INSTALL_NAME=.*/SLIB_INSTALL_NAME=$(SLIBNAME)/' \
        -e 's/^SLIB_INSTALL_LINKS=.*/SLIB_INSTALL_LINKS=/' \
        ffbuild/config.mak

    make -j"$JOBS"
    make install
)

cp "$WORK/ffmpeg-install/lib/libavutil.so" "$JNILIBS/libavutil.so"
cp "$WORK/ffmpeg-install/lib/libavcodec.so" "$JNILIBS/libavcodec.so"
"$STRIP" --strip-unneeded "$JNILIBS/libavutil.so" "$JNILIBS/libavcodec.so"
rm -rf "$STAGE/include/libavutil" "$STAGE/include/libavcodec"
cp -a "$WORK/ffmpeg-install/include/libavutil" "$STAGE/include/libavutil"
cp -a "$WORK/ffmpeg-install/include/libavcodec" "$STAGE/include/libavcodec"

echo "=== [3/7] OpenSSL libcrypto.a + pspdecrypt (GPL-3.0, $ABI) ==="
OPENSSL_TAR="$DL/openssl-${OPENSSL_VERSION}.tar.gz"
OPENSSL_SRC="$DL/openssl-${OPENSSL_VERSION}"
download_file "$OPENSSL_URL" "$OPENSSL_TAR"
if [[ ! -d "$OPENSSL_SRC" ]]; then
    tar -xf "$OPENSSL_TAR" -C "$DL"
fi

if [[ ! -f "$WORK/openssl/libcrypto.a" ]]; then
    rm -rf "$WORK/openssl"
    mkdir -p "$WORK/openssl"
    (
        cd "$WORK/openssl"
        export ANDROID_NDK_ROOT="$NDK"
        export PATH="$TOOLCHAIN/bin:$PATH"
        "$OPENSSL_SRC/Configure" "$OPENSSL_TARGET" \
            -D__ANDROID_API__="$API" \
            no-shared no-ssl3 no-apps no-tests no-docs no-ui-console
        make -j"$JOBS" build_libs
    )
fi

if [[ ! -d "$DL/pspdecrypt/.git" ]]; then
    rm -rf "$DL/pspdecrypt"
    git clone "$PSPDECRYPT_REPO" "$DL/pspdecrypt"
fi
git -C "$DL/pspdecrypt" checkout -q "$PSPDECRYPT_REV"

rm -rf "$WORK/pspdecrypt"
cp -a "$DL/pspdecrypt" "$WORK/pspdecrypt"
make -C "$WORK/pspdecrypt" -j"$JOBS" \
    CC="$CC" \
    CXX="$CXX" \
    CFLAGS="-O3 -fPIE -I$OPENSSL_SRC/include -I$WORK/openssl/include" \
    CXXFLAGS="-O3 -fPIE -I$OPENSSL_SRC/include -I$WORK/openssl/include" \
    EXTRA_FLAG="-pie -static-libstdc++ $PAGE_FLAGS -L$WORK/openssl"

cp "$WORK/pspdecrypt/pspdecrypt" "$JNILIBS/libpspdecrypt.so"
"$STRIP" --strip-unneeded "$JNILIBS/libpspdecrypt.so"

echo "=== [4/7] allegrexrecomp (standalone), es_prxreloc, libpsprecomp.a ($ABI) ==="
# Build allegrexrecomp from psprecomp/ directly with SDL2/Vulkan OFF so it does not pull libSDL2.so
cmake -S "$ROOT/psprecomp" -B "$WORK/recomp-cmake" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="$ABI" \
    -DANDROID_PLATFORM="android-$API" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DPSPRECOMP_SDL2=OFF \
    -DPSPRECOMP_VULKAN=OFF \
    -DPSPRECOMP_REAL_THREADS=OFF \
    -DPSPRECOMP_BUILD_RECOMPILER=ON \
    -DCMAKE_C_FLAGS="-I$STAGE/include" \
    -DCMAKE_EXE_LINKER_FLAGS="$PAGE_FLAGS"
cmake --build "$WORK/recomp-cmake" --target allegrexrecomp -j"$JOBS"

# Build es_prxreloc, libpsprecomp.a and version_header from top-level CMake
cmake -S "$ROOT" -B "$WORK/es-cmake" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="$ABI" \
    -DANDROID_PLATFORM="android-$API" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DPSPRECOMP_BUILD_RECOMPILER=OFF \
    -DSDL2_INCLUDE_DIR="$STAGE/include;$STAGE/include/SDL2" \
    -DSDL2_LIBRARY="$JNILIBS/libSDL2.so" \
    -DCMAKE_EXE_LINKER_FLAGS="$PAGE_FLAGS" \
    -DCMAKE_SHARED_LINKER_FLAGS="$PAGE_FLAGS"
cmake --build "$WORK/es-cmake" --target es_prxreloc psprecomp version_header -j"$JOBS"

cp "$WORK/recomp-cmake/tools/allegrexrecomp/allegrexrecomp" "$JNILIBS/liballegrexrecomp.so"
cp "$WORK/es-cmake/es_prxreloc" "$JNILIBS/libes_prxreloc.so"
"$STRIP" --strip-unneeded "$JNILIBS/liballegrexrecomp.so" "$JNILIBS/libes_prxreloc.so"

cp "$WORK/es-cmake/psprecomp/libpsprecomp.a" "$KIT/libpsprecomp.a"

echo "=== [5/7] Precompiled host objects + kit manifest ($ABI) ==="
rm -rf "$KIT/objs" "$KIT/include"
mkdir -p "$KIT/objs" "$KIT/include"

HOST_SRCS=$(python3 - "$ROOT/game/CMakeLists.txt" <<'PY'
import re, sys
txt = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"set\(_game_srcs\s+(.*?)\)", txt, re.S)
if not m:
    sys.exit("Could not find _game_srcs in game/CMakeLists.txt")
print(" ".join(re.findall(r"[\w/]+\.c\b", m.group(1))))
PY
)

for src in $HOST_SRCS; do
    obj="$KIT/objs/${src%.c}.o"
    mkdir -p "$(dirname "$obj")"
    "$CC" -fPIC -O2 -DNDEBUG -std=gnu11 \
        -DPSPRECOMP_REAL_THREADS=1 -DPSPRECOMP_SDL2=1 -DPSPRECOMP_VULKAN=1 \
        -I"$ROOT/psprecomp/include" \
        -I"$STAGE/include/SDL2" \
        -I"$STAGE/include" \
        -I"$WORK/es-cmake/generated" \
        -c "$ROOT/game/$src" -o "$obj"
done

cp -a "$ROOT/psprecomp/include/"* "$KIT/include/"
cp "$WORK/es-cmake/generated/version.h" "$KIT/include/version.h"

python3 - "$ROOT/game/CMakeLists.txt" "$KIT/kit_manifest.json" "$ABI" "$API" "$TARGET_TRIPLE" "$LLD_EMULATION" "$RT_ARCH" "$HOST_SRCS" <<'PY'
import json, re, sys
gtxt = open(sys.argv[1], encoding="utf-8").read()
out_path, abi, api, triple, lld_emu, rt_arch, host_srcs = (
    sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], sys.argv[6], sys.argv[7], sys.argv[8].split()
)
wraps = re.findall(r"--wrap=(\w+)", gtxt)
manifest = {
    "format": 2,
    "abi": abi,
    "api": api,
    "compiler": "clang",
    "clang_bin": "libclang21.so",
    "lld_bin": "liblld.so",
    "clang_target": f"{triple}{api}",
    "lld_emulation": lld_emu,
    "rt_builtin": f"clang-res/lib/linux/libclang_rt.builtins-{rt_arch}-android.a",
    "defines": [
        "PSPRECOMP_REAL_THREADS=1",
        "PSPRECOMP_SDL2=1",
        "PSPRECOMP_VULKAN=1",
    ],
    "generated_cflags": ["-O2", "-DNDEBUG", "-std=gnu11", "-fPIC", "-w"],
    "wrap": wraps,
    "host_objs": [f"objs/{s[:-2]}.o" for s in host_srcs],
    "static_libs": ["libpsprecomp.a"],
    "shared_libs": ["libSDL2.so", "libavcodec.so", "libavutil.so"],
    "android_sys_libs": ["vulkan", "log", "android", "m", "dl", "c"],
    "ldflags": ["-shared", "-z", "max-page-size=16384", "-z", "common-page-size=16384", "--build-id=sha1"],
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY

echo "=== [6/7] On-device compiler: Termux clang + lld + minimal NDK sysroot ($ABI) ==="
TERMUX_DL="$DL/termux"
TERMUX_IDX="$TERMUX_DL/Packages-${TERMUX_ARCH}"
mkdir -p "$TERMUX_DL"
if [[ ! -f "$TERMUX_IDX" ]]; then
    download_file "$TERMUX_REPO/dists/stable/main/binary-${TERMUX_ARCH}/Packages" "$TERMUX_IDX"
fi

TERMUX_EXTRACT="$WORK/termux-extract"
rm -rf "$TERMUX_EXTRACT"
mkdir -p "$TERMUX_EXTRACT"

TERMUX_PKGS=(clang lld libllvm libc++ libffi libiconv liblzma libxml2 zlib zstd)
PKG_FILENAMES=$(python3 - "$TERMUX_IDX" "${TERMUX_PKGS[@]}" <<'PY'
import sys
idx_path = sys.argv[1]
want = set(sys.argv[2:])
found = {}
cur_pkg = None
for line in open(idx_path, encoding="utf-8", errors="replace"):
    if line.startswith("Package: "):
        cur_pkg = line.split(": ", 1)[1].strip()
    elif line.startswith("Filename: ") and cur_pkg in want:
        found[cur_pkg] = line.split(": ", 1)[1].strip()
    elif line.strip() == "":
        cur_pkg = None
missing = want - set(found)
if missing:
    sys.exit(f"Missing Termux packages in index: {sorted(missing)}")
for pkg in sys.argv[2:]:
    print(found[pkg])
PY
)

for rel in $PKG_FILENAMES; do
    deb="$TERMUX_DL/$(basename "$rel")"
    download_file "$TERMUX_REPO/$rel" "$deb"
    mkdir -p "$TERMUX_EXTRACT/tmp"
    ar x "$deb" --output "$TERMUX_EXTRACT/tmp"
    tar -xf "$TERMUX_EXTRACT/tmp/data.tar."* -C "$TERMUX_EXTRACT"
    rm -rf "$TERMUX_EXTRACT/tmp"
done

TUSR="$TERMUX_EXTRACT/data/data/com.termux/files/usr"
CLANG_BIN=$(find "$TUSR/bin" -maxdepth 1 -type f -name 'clang-[0-9]*' ! -name '*.sh' | head -n1)
if [[ -z "$CLANG_BIN" ]]; then
    echo "ERROR: clang binary not found in Termux package" >&2
    exit 1
fi

cp "$CLANG_BIN" "$JNILIBS/libclang21.so"
cp "$TUSR/bin/lld" "$JNILIBS/liblld.so"
cp "$TUSR/lib/libLLVM.so" "$JNILIBS/libLLVM.so"
cp "$TUSR/lib/libclang-cpp.so" "$JNILIBS/libclang-cpp.so"
cp "$TUSR/lib/libc++_shared.so" "$JNILIBS/libc++_shared.so"
cp "$TUSR/lib/libffi.so" "$JNILIBS/libffi.so"
cp "$TUSR/lib/libiconv.so" "$JNILIBS/libiconv.so"
cp "$TUSR/lib/libz.so.1."* "$JNILIBS/libz1_.so"
cp "$TUSR/lib/libzstd.so.1."* "$JNILIBS/libzstd1_.so"
cp "$TUSR/lib/libxml2.so.16."* "$JNILIBS/libxml2_16.so"
chmod 755 "$JNILIBS/"*.so

# Patch SONAME/NEEDED versioned .so names to Android jniLibs-compatible lib*.so (same byte length)
python3 - "$JNILIBS" <<'PY'
from pathlib import Path
import sys
jnilibs = Path(sys.argv[1])
repls = [
    (b"libz.so.1\x00", b"libz1_.so\x00"),
    (b"libzstd.so.1\x00", b"libzstd1_.so\x00"),
    (b"libxml2.so.16\x00", b"libxml2_16.so\x00"),
]
for p in jnilibs.glob("*.so"):
    data = p.read_bytes()
    orig = data
    for old, new in repls:
        assert len(old) == len(new)
        data = data.replace(old, new)
    if data != orig:
        p.write_bytes(data)
PY

echo "--- Staging Clang resource headers + compiler-rt + minimal NDK sysroot ---"
rm -rf "$STAGE/kit/zig-lib" "$KIT/clang-res" "$KIT/ndk-sysroot"
mkdir -p "$KIT/clang-res/include" "$KIT/clang-res/lib/linux"
CLANG_RES_INC=$(find "$TUSR/lib/clang" -maxdepth 2 -type d -name include | head -n1)
cp -a "$CLANG_RES_INC/"* "$KIT/clang-res/include/"
RT_BUILTIN=$(find "$TOOLCHAIN/lib/clang" -name "libclang_rt.builtins-${RT_ARCH}-android.a" | head -n1)
cp "$RT_BUILTIN" "$KIT/clang-res/lib/linux/"

SYSROOT="$TOOLCHAIN/sysroot"
SYSROOT_LIB="$SYSROOT/usr/lib/${TARGET_TRIPLE}/${API}"
KIT_SYSROOT="$KIT/ndk-sysroot"
mkdir -p "$KIT_SYSROOT/usr/include" "$KIT_SYSROOT/usr/lib"
(cd "$SYSROOT/usr/include" && tar --exclude='c++' -cf - .) | (cd "$KIT_SYSROOT/usr/include" && tar -xf -)
for f in crtbegin_so.o crtend_so.o libc.so libm.so libdl.so liblog.so libandroid.so libvulkan.so; do
    cp "$SYSROOT_LIB/$f" "$KIT_SYSROOT/usr/lib/$f"
done

echo "=== [7/7] Validation: ELF headers (ET_DYN), 16KB alignment (0x4000), SONAMEs, NEEDED ==="
for f in "$JNILIBS"/*.so; do
    echo "--- Checking $(basename "$f") ($(du -h "$f" | cut -f1)) ---"
    readelf -h "$f" | grep -E "Class:|Machine:|Type:"
    if ! readelf -h "$f" | grep -q "Machine:.*${EXPECTED_ELF_MACHINE}"; then
        echo "ERROR: $f has wrong ELF machine" >&2
        exit 1
    fi
    if ! readelf -h "$f" | grep -q "Type:.*DYN"; then
        echo "ERROR: $f is not ET_DYN (PIE/shared)" >&2
        exit 1
    fi
    python3 - "$f" <<'PY'
import subprocess, sys
out = subprocess.check_output(["readelf", "-lW", sys.argv[1]], text=True)
loads = [line for line in out.splitlines() if line.strip().startswith("LOAD")]
if not loads:
    sys.exit(f"No LOAD segments in {sys.argv[1]}")
for line in loads:
    align_str = line.strip().split()[-1]
    align = int(align_str, 16)
    if align < 0x4000:
        sys.exit(f"ERROR: {sys.argv[1]} has LOAD segment with align {align_str} < 0x4000:\n{line}")
print(f"  LOAD align OK ({len(loads)} segments, >= 0x4000)")
PY
done

echo "--- Checking SONAMEs and critical NEEDED ---"
for lib in libSDL2.so libavutil.so libavcodec.so; do
    soname=$(readelf -d "$JNILIBS/$lib" | sed -n 's/.*(SONAME).*Library soname: \[\(.*\)\]/\1/p')
    echo "  $lib -> SONAME=$soname"
    if [[ "$soname" != "$lib" ]]; then
        echo "ERROR: expected SONAME $lib, got $soname" >&2
        exit 1
    fi
done

if readelf -d "$JNILIBS/libpspdecrypt.so" | grep -q "libc++_shared.so"; then
    echo "ERROR: libpspdecrypt.so must not depend on libc++_shared.so" >&2
    exit 1
fi
if readelf -d "$JNILIBS/liballegrexrecomp.so" | grep -q "libSDL2.so"; then
    echo "ERROR: liballegrexrecomp.so must not depend on libSDL2.so" >&2
    exit 1
fi

echo "=== DONE: $STAGE ==="
