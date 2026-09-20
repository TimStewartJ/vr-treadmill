#pragma once

// Shared-memory contract between the VR Treadmill app (writer, Python) and the
// OpenXR API layer (reader, C++, inside the game process).
//
// Keep in sync with src/vrtread/outputs.py. tests/test_outputs.py and the native
// cross-process test both assert the layout.

#include <cstdint>

namespace vrtread {

// v2 uses a new object name so a stale v1 layer DLL can never misread a v2 block.
constexpr wchar_t kMappingName[] = L"Local\\VRTreadmillOpenXRState_v2";
constexpr char kMappingNameEnvVar[] = "VRTREAD_OPENXR_MAPPING";  // test isolation only

constexpr uint32_t kMagic = 0x4D545256;  // "VRTM" little-endian
constexpr uint32_t kVersion = 2;

// flags
constexpr uint32_t kFlagActive = 0x1;  // publisher is capturing

// options
constexpr uint32_t kOptionDriveInactive = 0x1;  // drive locomotion even if the left controller is asleep

// A block older than this is ignored, so a crashed or hung app can never leave the player walking.
constexpr uint64_t kStaleMs = 250;

// |y| below this is "treadmill idle": the layer does not touch the action state at all.
constexpr float kIdleThreshold = 0.0005F;

enum FilterMode : uint32_t {
    kFilterBalanced = 0,
    kFilterStrict = 1,
    kFilterCompatibility = 2,
};

enum CombineMode : uint32_t {
    kCombineMaxMagnitude = 0,  // whichever of treadmill / physical stick is pushed further wins
    kCombineReplace = 1,       // treadmill replaces the physical stick Y while walking
    kCombineAdd = 2,           // treadmill + physical stick, clamped
};

#pragma pack(push, 1)
struct SharedState {
    uint32_t magic;
    uint32_t version;
    uint32_t struct_size;
    uint32_t flags;
    uint64_t seq;           // seqlock: odd while the writer is mid-update
    uint64_t timestamp_ms;  // GetTickCount64() at publish time
    float x;                // reserved for strafe; currently always 0
    float y;                // +forward / -backward, [-1, 1]
    uint32_t filter_mode;
    uint32_t combine_mode;
    uint32_t options;
    uint32_t publisher_pid;
    uint64_t reserved;
};
#pragma pack(pop)

static_assert(sizeof(SharedState) == 64, "SharedState must match the Python struct format '<IIIIQQffIIIIQ'.");

}  // namespace vrtread
