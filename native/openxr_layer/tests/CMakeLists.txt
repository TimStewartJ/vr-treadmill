# Test harness for the VR Treadmill OpenXR layer.
#
# The "game" (vrtread_layer_tests.exe) talks to the REAL Khronos OpenXR loader, which loads the layer DLL
# exactly as it would inside a game, on top of a mock runtime that simulates SteamVR's input system.

include(FetchContent)

set(VRTREAD_OPENXR_SDK_TAG "release-1.1.63" CACHE STRING "Khronos OpenXR-SDK tag used for the loader and headers in tests")

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

set(VRTREAD_TEST_BIN "${CMAKE_CURRENT_BINARY_DIR}/bin")
file(MAKE_DIRECTORY "${VRTREAD_TEST_BIN}")

function(vrtread_test_output target)
  foreach(config "" _DEBUG _RELEASE _RELWITHDEBINFO _MINSIZEREL)
    set_target_properties(${target} PROPERTIES RUNTIME_OUTPUT_DIRECTORY${config} "${VRTREAD_TEST_BIN}")
  endforeach()
endfunction()

# --- mock runtime ---------------------------------------------------------------------------------------
add_library(vrtread_mock_runtime SHARED mock_runtime/mock_runtime.cpp)
target_compile_features(vrtread_mock_runtime PRIVATE cxx_std_17)
target_link_libraries(vrtread_mock_runtime PRIVATE OpenXR::headers)
target_compile_definitions(vrtread_mock_runtime PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
if(MSVC)
  target_compile_options(vrtread_mock_runtime PRIVATE /W4 /permissive- /utf-8)
endif()
vrtread_test_output(vrtread_mock_runtime)
file(WRITE "${VRTREAD_TEST_BIN}/vrtread_mock_runtime.json"
"{
  \"file_format_version\": \"1.0.0\",
  \"runtime\": {
    \"name\": \"VRTreadmill Mock Runtime\",
    \"library_path\": \".\\\\vrtread_mock_runtime.dll\"
  }
}
")

# --- the test game --------------------------------------------------------------------------------------
add_executable(vrtread_layer_tests
  main.cpp
  test_layer.cpp
  test_negotiation.cpp
  test_policy.cpp
)
target_compile_features(vrtread_layer_tests PRIVATE cxx_std_17)
target_include_directories(vrtread_layer_tests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/../include")
target_link_libraries(vrtread_layer_tests PRIVATE OpenXR::openxr_loader OpenXR::headers advapi32)
target_compile_definitions(vrtread_layer_tests PRIVATE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
  VRTREAD_TEST_LAYER_DIR="${VRTREAD_LAYER_OUTPUT_DIR}"
  VRTREAD_TEST_RUNTIME_JSON="${VRTREAD_TEST_BIN}/vrtread_mock_runtime.json"
  VRTREAD_TEST_RUNTIME_DLL="${VRTREAD_TEST_BIN}/vrtread_mock_runtime.dll"
)
if(MSVC)
  target_compile_options(vrtread_layer_tests PRIVATE /W4 /permissive- /utf-8)
endif()
vrtread_test_output(vrtread_layer_tests)
add_dependencies(vrtread_layer_tests vrtread_openxr_layer vrtread_mock_runtime)

add_test(NAME layer_tests COMMAND vrtread_layer_tests)
set_tests_properties(layer_tests PROPERTIES TIMEOUT 180)

# --- ABI probe: hand-written header vs official headers ---------------------------------------------------
add_executable(vrtread_abi_probe_minimal abi_probe.cpp)
target_include_directories(vrtread_abi_probe_minimal PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../include")
vrtread_test_output(vrtread_abi_probe_minimal)

add_executable(vrtread_abi_probe_official abi_probe.cpp)
target_compile_definitions(vrtread_abi_probe_official PRIVATE VRTREAD_ABI_OFFICIAL)
target_link_libraries(vrtread_abi_probe_official PRIVATE OpenXR::headers)
vrtread_test_output(vrtread_abi_probe_official)

add_test(NAME abi_matches_official_headers
  COMMAND "${CMAKE_COMMAND}"
          -DMINIMAL=$<TARGET_FILE:vrtread_abi_probe_minimal>
          -DOFFICIAL=$<TARGET_FILE:vrtread_abi_probe_official>
          -P "${CMAKE_CURRENT_SOURCE_DIR}/compare_abi.cmake")
