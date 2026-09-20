// Test runner for the VR Treadmill OpenXR layer.
//
//   vrtread_layer_tests.exe [--filter <substring>]
//       Runs the native test suite. The exe publishes treadmill state itself.
//
//   vrtread_layer_tests.exe --probe --mapping <name> [--frames N] [--interval-ms M] [--log-dir DIR]
//       Cross-language mode, driven by tests/test_native_e2e.py. Something else (the real Python app code)
//       publishes to <name>; this process plays a Unity-style and an Unreal-style game through the real
//       loader + layer + mock runtime and prints what each game read, one JSON object per frame.

#include "harness.h"

#include <chrono>
#include <cstdlib>
#include <thread>

using namespace harness;

namespace {

std::wstring widen(const std::string& value) {
    return std::wstring(value.begin(), value.end());
}

std::string make_temp_dir(const char* prefix) {
    char base[MAX_PATH]{};
    GetTempPathA(MAX_PATH, base);
    const std::string dir = std::string(base) + prefix + std::to_string(GetCurrentProcessId());
    CreateDirectoryA(dir.c_str(), nullptr);
    return dir;
}

int run_probe(int frames, int interval_ms) {
    configure_loader_environment();
    MockRuntime::get().reset();
    MockRuntime::get().set_thumbstick(kLeftHand, 0.25F, 0.0F);
    MockRuntime::get().set_thumbstick(kRightHand, 0.5F, 0.125F);

    Game game("VRTreadmillProbe", "Unity");
    if (game.create_result != XR_SUCCESS) {
        std::printf("{\"error\":\"xrCreateInstance failed\",\"result\":%d}\n", static_cast<int>(game.create_result));
        return 2;
    }
    const XrActionSet unity_set = game.create_set("oculustouchcontroller");
    const XrAction thumbstick = game.create_action(unity_set, "thumbstick", XR_ACTION_TYPE_VECTOR2F_INPUT, game.both_hands());
    const XrActionSet unreal_set = game.create_set("ue4");
    const XrAction forward = game.create_action(unreal_set, "moveforward", XR_ACTION_TYPE_FLOAT_INPUT);
    game.suggest(kTouchProfile, {
                                    {thumbstick, "/user/hand/left/input/thumbstick"},
                                    {thumbstick, "/user/hand/right/input/thumbstick"},
                                    {forward, "/user/hand/left/input/thumbstick/y"},
                                });
    game.attach({unity_set, unreal_set});

    for (int frame = 0; frame < frames; ++frame) {
        if (game.sync({unity_set, unreal_set}) != XR_SUCCESS) {
            std::printf("{\"error\":\"xrSyncActions failed\"}\n");
            return 3;
        }
        const XrActionStateVector2f left = game.vec2(thumbstick, game.left);
        const XrActionStateVector2f right = game.vec2(thumbstick, game.right);
        const XrActionStateFloat axis = game.scalar(forward);
        std::printf("{\"frame\":%d,\"left_x\":%.6f,\"left_y\":%.6f,\"right_x\":%.6f,\"right_y\":%.6f,\"forward\":%.6f}\n", frame,
                    static_cast<double>(left.currentState.x), static_cast<double>(left.currentState.y),
                    static_cast<double>(right.currentState.x), static_cast<double>(right.currentState.y),
                    static_cast<double>(axis.currentState));
        std::fflush(stdout);
        std::this_thread::sleep_for(std::chrono::milliseconds(interval_ms));
    }
    return testing::failures_in_current_test() == 0 ? 0 : 4;
}

}  // namespace

int main(int argc, char** argv) {
    std::string filter;
    std::string mapping;
    std::string log_dir;
    bool probe = false;
    int frames = 30;
    int interval_ms = 10;
    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        const auto value = [&]() -> std::string { return index + 1 < argc ? argv[++index] : std::string{}; };
        if (arg == "--filter") {
            filter = value();
        } else if (arg == "--probe") {
            probe = true;
        } else if (arg == "--mapping") {
            mapping = value();
        } else if (arg == "--frames") {
            frames = std::atoi(value().c_str());
        } else if (arg == "--interval-ms") {
            interval_ms = std::atoi(value().c_str());
        } else if (arg == "--log-dir") {
            log_dir = value();
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", arg.c_str());
            return 64;
        }
    }

    try {
        if (log_dir.empty()) {
            log_dir = make_temp_dir("vrtread-layer-tests-");
        }
        fixtures().log_dir = log_dir;
        set_env("VRTREAD_OPENXR_LOG_DIR", log_dir.c_str());
        set_env("VRTREAD_OPENXR_LOG", "1");

        if (mapping.empty()) {
            mapping = "Local\\VRTreadmillLayerTest_" + std::to_string(GetCurrentProcessId());
        }
        fixtures().mapping_name = widen(mapping);
        set_env(vrtread::kMappingNameEnvVar, mapping.c_str());

        if (probe) {
            return run_probe(frames, interval_ms);
        }

        std::printf("layer under test : %s\n", layer_dll_path().c_str());
        std::printf("mock runtime     : %s\n", VRTREAD_TEST_RUNTIME_JSON);
        std::printf("layer log dir    : %s\n\n", log_dir.c_str());
        return testing::run_all(filter) == 0 ? 0 : 1;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "fatal: %s\n", error.what());
        return 70;
    }
}
