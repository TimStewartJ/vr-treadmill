cmake_minimum_required(VERSION 3.20)

# Keep VERSION in sync with pyproject.toml (tests/test_versions.py enforces it).
project(vrtread_openxr_layer VERSION 0.2.0 LANGUAGES CXX RC)

if(NOT WIN32)
  message(FATAL_ERROR "The VR Treadmill OpenXR layer is Windows-only.")
endif()
if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "The VR Treadmill OpenXR layer is x64-only. 32-bit processes skip it via the manifest.")
endif()

option(VRTREAD_BUILD_TESTS "Build the layer test harness (downloads the Khronos OpenXR SDK loader)" OFF)

set(VRTREAD_LAYER_OUTPUT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/bin")

configure_file(src/version.rc.in "${CMAKE_CURRENT_BINARY_DIR}/version.rc" @ONLY)

add_library(vrtread_openxr_layer SHARED
  src/layer.cpp
  "${CMAKE_CURRENT_BINARY_DIR}/version.rc"
)

target_compile_features(vrtread_openxr_layer PRIVATE cxx_std_17)
target_include_directories(vrtread_openxr_layer PRIVATE include)
target_compile_definitions(vrtread_openxr_layer PRIVATE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
  VRTREAD_LAYER_VERSION="${PROJECT_VERSION}"
)

# A layer DLL that fails to load can make xrCreateInstance fail for the whole game (the loader treats
# "no layer could be loaded" as fatal). Link the CRT statically so the DLL only imports kernel32.
set_property(TARGET vrtread_openxr_layer PROPERTY
  MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

if(MSVC)
  target_compile_options(vrtread_openxr_layer PRIVATE /W4 /WX /permissive- /utf-8)
  target_link_options(vrtread_openxr_layer PRIVATE /guard:cf)
  target_compile_options(vrtread_openxr_layer PRIVATE /guard:cf)
endif()

set_target_properties(vrtread_openxr_layer PROPERTIES OUTPUT_NAME "vrtread_openxr_layer")
foreach(config "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
  set_target_properties(vrtread_openxr_layer PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY${config} "${VRTREAD_LAYER_OUTPUT_DIR}"
    LIBRARY_OUTPUT_DIRECTORY${config} "${VRTREAD_LAYER_OUTPUT_DIR}"
    ARCHIVE_OUTPUT_DIRECTORY${config} "${CMAKE_CURRENT_BINARY_DIR}/lib"
  )
endforeach()

# The manifest is static and sits next to the DLL (relative library_path), so the pair can be
# copied anywhere and registered without generating files at runtime.
add_custom_command(TARGET vrtread_openxr_layer POST_BUILD
  COMMAND "${CMAKE_COMMAND}" -E copy_if_different
          "${CMAKE_CURRENT_SOURCE_DIR}/manifest/XR_APILAYER_VRTREAD_treadmill.json"
          "${VRTREAD_LAYER_OUTPUT_DIR}/XR_APILAYER_VRTREAD_treadmill.json"
  VERBATIM
)

if(VRTREAD_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
