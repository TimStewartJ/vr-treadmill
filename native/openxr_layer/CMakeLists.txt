cmake_minimum_required(VERSION 3.20)

project(vrtread_openxr_layer LANGUAGES CXX)

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "VRTreadmill OpenXR layer is x64-only.")
endif()

add_library(vrtread_openxr_layer SHARED
  src/layer.cpp
)

target_compile_features(vrtread_openxr_layer PRIVATE cxx_std_17)
target_include_directories(vrtread_openxr_layer PRIVATE include)
target_compile_definitions(vrtread_openxr_layer PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)

if(MSVC)
  target_compile_options(vrtread_openxr_layer PRIVATE /W4 /permissive-)
endif()

foreach(config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
  set_target_properties(vrtread_openxr_layer PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    LIBRARY_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    ARCHIVE_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/lib"
  )
endforeach()

set_target_properties(vrtread_openxr_layer PROPERTIES
  OUTPUT_NAME "vrtread_openxr_layer"
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
  ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/lib"
)
