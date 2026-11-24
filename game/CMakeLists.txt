# The native game. One executable, built from host.c plus the C that
# allegrexrecomp generated from the decrypted EBOOT.BIN (module "DJMAX").

set(EMOTIONALSENSE_GEN_DIR "${PROJECT_BINARY_DIR}/gen" CACHE PATH
    "Where the pipeline writes the generated C (djmax_*.c)")

# CONFIGURE_DEPENDS: generating the C later makes the next build pick it up.
file(GLOB gen_srcs CONFIGURE_DEPENDS "${EMOTIONALSENSE_GEN_DIR}/djmax_*.c")
if(NOT gen_srcs)
    message(STATUS "emotionalsense: no generated C in ${EMOTIONALSENSE_GEN_DIR} yet -- "
                   "only the toolkit will be built. Run the launcher first.")
    return()
endif()

# ATRAC3/ATRAC3+ (music, previews) and PSMF audio are decoded with libavcodec.
find_package(PkgConfig REQUIRED)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET libavcodec libavutil)

add_executable(emotionalsense host.c crash.c app_icon.c update_overlay.c exit_dialog.c exit_text.c hle_djmax.c guest_patches.c buttons.c score.c speed.c speed_glyphs.c at3_dec.c avc_dec.c ${gen_srcs})
# The file (and so the process) is called EmotionalSense-P3-game: other ports will
# live in separate projects, so the name must say which game it is.
set_target_properties(emotionalsense PROPERTIES OUTPUT_NAME "EmotionalSense-P3-game")
add_dependencies(emotionalsense version_header)
target_include_directories(emotionalsense PRIVATE ${EMOTIONALSENSE_GEN_DIR}
                                                ${PROJECT_BINARY_DIR}/generated)
target_link_libraries(emotionalsense PRIVATE psprecomp PkgConfig::FFMPEG)
if(MSVC)
    target_compile_options(emotionalsense PRIVATE /W1)
else()
    # The generated corpus is machine-written; its warnings are noise here.
    set_source_files_properties(${gen_srcs} PROPERTIES COMPILE_OPTIONS "-w")
endif()
# guest_patches.c replaces a recompiled function; the only caller is in another
# translation unit, so the linker can redirect it and build/gen stays pristine.
if(NOT MSVC)
    target_link_options(emotionalsense PRIVATE
        -Wl,--wrap=psp_func_000547F0
        -Wl,--wrap=psp_func_0007F438
        -Wl,--wrap=psp_func_00061270
        -Wl,--wrap=psp_func_00077124
        -Wl,--wrap=psp_func_0007718C
        -Wl,--wrap=psp_func_00017E90
        -Wl,--wrap=psp_func_00017F04
        -Wl,--wrap=psp_func_0001B40C
        -Wl,--wrap=psp_func_0002D320
        -Wl,--wrap=psp_func_0002D618
        -Wl,--wrap=psp_func_0006D234
        -Wl,--wrap=psp_func_00059AC4
        -Wl,--wrap=psp_func_00042064
        -Wl,--wrap=psp_func_000552C4
        -Wl,--wrap=psp_func_0000163C
        -Wl,--wrap=psp_func_00054EF4
        -Wl,--wrap=psp_func_00055EF4
        -Wl,--build-id=sha1)
endif()
install(TARGETS emotionalsense RUNTIME DESTINATION bin)

# Bring-up aid: function trace on dispatch misses (slower).
option(EMOTIONALSENSE_TRACE "Build the generated code with PSPRECOMP_TRACE" OFF)
if(EMOTIONALSENSE_TRACE)
    target_compile_definitions(emotionalsense PRIVATE PSPRECOMP_TRACE=1)
endif()
