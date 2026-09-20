# Standalone 32-bit check (configure with -A Win32). Not part of the normal build.
#
# HKCU is shared between 32- and 64-bit processes, so a 32-bit OpenXR game sees the manifest of this x64-only
# layer. This project builds a genuine 32-bit OpenXR client (real 32-bit Khronos loader + 32-bit mock runtime)
# so tests/test_installed_layer.py can prove that such a game still starts. See scripts\build-win32-check.ps1.
cmake_minimum_required(VERSION 3.20)
project(vrtread_win32_check LANGUAGES CXX)

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
  message(FATAL_ERROR "This project is the 32-bit check: configure it with -A Win32.")
endif()

include(FetchContent)
set(VRTREAD_OPENXR_SDK_TAG "release-1.1.63" CACHE STRING "Khronos OpenXR-SDK tag")
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_API_LAYERS OFF CACHE BOOL "" FORCE)
set(BUILD_CONFORMANCE_TESTS OFF CACHE BOOL "" FORCE)
set(DYNAMIC_LOADER OFF CACHE BOOL "" FORCE)
set(BUILD_WITH_SYSTEM_JSONCPP OFF CACHE BOOL "" FORCE)
FetchContent_Declare(openxr_sdk
  GIT_REPOSITORY https://github.com/KhronosGroup/OpenXR-SDK.git
  GIT_TAG ${VRTREAD_OPENXR_SDK_TAG}
  GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(openxr_sdk)

set(OUT "${CMAKE_CURRENT_BINARY_DIR}/bin")
file(MAKE_DIRECTORY "${OUT}")

# __stdcall exports are decorated on x86 (_name@8); the loader looks the negotiate function up by its plain
# name, so the 32-bit runtime needs a .def file.
add_library(vrtread_mock_runtime32 SHARED ../mock_runtime/mock_runtime.cpp mock_runtime32.def)
target_compile_features(vrtread_mock_runtime32 PRIVATE cxx_std_17)
target_link_libraries(vrtread_mock_runtime32 PRIVATE OpenXR::headers)
target_compile_definitions(vrtread_mock_runtime32 PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)

add_executable(vrtread_probe32 probe32.cpp)
target_compile_features(vrtread_probe32 PRIVATE cxx_std_17)
target_link_libraries(vrtread_probe32 PRIVATE OpenXR::openxr_loader OpenXR::headers)
target_compile_definitions(vrtread_probe32 PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX
  VRTREAD_RUNTIME32_JSON="${OUT}/vrtread_mock_runtime32.json")

foreach(target vrtread_mock_runtime32 vrtread_probe32)
  foreach(config "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
    set_target_properties(${target} PROPERTIES RUNTIME_OUTPUT_DIRECTORY${config} "${OUT}")
  endforeach()
endforeach()

file(WRITE "${OUT}/vrtread_mock_runtime32.json"
"{
  \"file_format_version\": \"1.0.0\",
  \"runtime\": {
    \"name\": \"VRTreadmill Mock Runtime (32-bit)\",
    \"library_path\": \".\\\\vrtread_mock_runtime32.dll\"
  }
}
")
