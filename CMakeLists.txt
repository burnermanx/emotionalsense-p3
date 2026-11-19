# emotionalsense -- DJMAX Portable 3 (ULKS46236), statically recompiled.
#
# Builds the psprecomp toolkit (allegrexrecomp, which the launcher's pipeline
# needs) and, once the generated C exists, the native game executable.
#
# The generated C is never committed: the launcher (or scripts/build.sh)
# produces it from your own decrypted EBOOT into ${EMOTIONALSENSE_GEN_DIR}.

cmake_minimum_required(VERSION 3.16)
project(emotionalsense VERSION 0.1.0 LANGUAGES C)

# The version is written in project() above and nowhere else. Everything else
# reads what cmake/version.cmake produces: --version, the first log line, the
# window title and the package names. It runs again on every build and only
# rewrites the header when the strings change, so a commit or a tag cannot
# leave a stale version in the binary.
set(EMOTIONALSENSE_VERSION_H "${CMAKE_CURRENT_BINARY_DIR}/generated/version.h")
set(_version_cmd ${CMAKE_COMMAND}
    "-DSRC=${CMAKE_CURRENT_SOURCE_DIR}"
    "-DOUT=${EMOTIONALSENSE_VERSION_H}"
    "-DVERSION=${PROJECT_VERSION}"
    -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/version.cmake")
execute_process(COMMAND ${_version_cmd})
add_custom_target(version_header ALL COMMAND ${_version_cmd}
    BYPRODUCTS "${EMOTIONALSENSE_VERSION_H}"
    COMMENT "version ${PROJECT_VERSION}")

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

set(PSPRECOMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/psprecomp" CACHE PATH "psprecomp source tree")
if(NOT EXISTS ${PSPRECOMP_DIR}/CMakeLists.txt)
    message(FATAL_ERROR
        "psprecomp not found at ${PSPRECOMP_DIR}. Run: git submodule update --init --recursive")
endif()

# DJMAX runs several game threads (audio, streaming, main loop) that wait on
# each other; it needs the real cooperative scheduler.
set(PSPRECOMP_REAL_THREADS ON CACHE BOOL "DJMAX needs real guest threads" FORCE)
set(PSPRECOMP_VULKAN ON CACHE BOOL "Vulkan renderer" FORCE)
if((UNIX AND NOT APPLE) OR WIN32)
    set(PSPRECOMP_SDL2 ON CACHE BOOL "SDL2 window on Linux and Windows" FORCE)
endif()
add_subdirectory(${PSPRECOMP_DIR} psprecomp)

# psprecomp only registers its tests when it is the top-level project, so the
# rasterizer and recompiler tests are exposed here as well. A cross build
# cannot run them and does not build allegrex_core.
if(NOT CMAKE_CROSSCOMPILING)
    enable_testing()
    add_executable(test_raster ${PSPRECOMP_DIR}/tests/test_raster.c)
    target_link_libraries(test_raster PRIVATE psprecomp)
    add_test(NAME raster COMMAND test_raster)

    add_executable(test_emit ${PSPRECOMP_DIR}/tests/test_emit.c)
    target_link_libraries(test_emit PRIVATE allegrex_core)
    add_test(NAME emit COMMAND test_emit)

    add_executable(test_crash_scrub game/tests/test_crash_scrub.c)
    add_test(NAME crash_scrub COMMAND test_crash_scrub)
endif()

add_subdirectory(game)
