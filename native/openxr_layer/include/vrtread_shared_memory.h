#pragma once

#include <cstdint>

namespace vrtread {

constexpr wchar_t kMappingName[] = L"Local\\VRTreadmillOpenXRState_v1";
constexpr uint32_t kMagic = 0x4D545256;
constexpr uint32_t kVersion = 1;
constexpr uint32_t kActiveFlag = 0x1;
constexpr uint64_t kStaleMs = 250;
constexpr uint64_t kFilterModeMask = 0xFF;
constexpr uint32_t kFilterModeBalanced = 0;
constexpr uint32_t kFilterModeStrict = 1;
constexpr uint32_t kFilterModeCompatibility = 2;

#pragma pack(push, 1)
struct SharedState {
    uint32_t magic;
    uint32_t version;
    uint32_t struct_size;
    uint32_t flags;
    uint64_t seq;
    uint64_t timestamp_ms;
    float x;
    float y;
    uint64_t reserved0;  // Low 8 bits carry OpenXR filter mode; upper bits must be zero.
    uint64_t reserved1;
};
#pragma pack(pop)

static_assert(sizeof(SharedState) == 56, "SharedState must match Python struct format.");

}  // namespace vrtread
