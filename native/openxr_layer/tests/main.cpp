// Test runner for the VR Treadmill OpenXR layer.
//
//   vrtread_layer_tests.exe [--filter <substring>]
//       Runs the native test suite. The exe publishes treadmill state itself.
//
//   vrtread_layer_tests.exe --probe --mapping <name> [--frames N] [--interval-ms M] [--log-dir DIR] [--implicit]
//       Cross-language mode, driven by tests/test_native_e2e.py. Something else (the real Python app code)
//       publishes to <name>; this process plays a Unity-style and an Unreal-style game through the real
//       loader + layer + mock runtime and prints what each game read, one JSON object per frame.
//       With --implicit the layer is NOT handed to the loader: like a real game, the loader has to discover
//       it through the registry. The first output line reports which layer DLL (if any) got loaded.

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

std::string json_escape(const std::string& value) {
    std::string out;
    for (const char ch : value) {
        if (ch == '\\' || ch == '"') {
            out.push_back('\\');
        }
        out.push_back(ch);
    }
    return out;
}

// Instance-level check against a REAL runtime (no headset needed, no session is created):
//   vrtread_layer_tests.exe --real-runtime <runtime.json | active>
// Loads the layer build under test explicitly, creates an instance on the given runtime, and exercises every
// instance-level call the layer hooks. The layer log then shows whether it attached in "active" mode, i.e.
// whether the real runtime provided every function the layer needs.
int run_real_runtime_check(const std::string& runtime_json) {
    configure_loader_environment();
    set_env("XR_RUNTIME_JSON", runtime_json == "active" ? nullptr : runtime_json.c_str());

    XrInstanceCreateInfo info{XR_TYPE_INSTANCE_CREATE_INFO};
    strncpy_s(info.applicationInfo.applicationName, "VRTreadmillRuntimeCheck", _TRUNCATE);
    strncpy_s(info.applicationInfo.engineName, "Probe", _TRUNCATE);
    info.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
    XrInstance instance = XR_NULL_HANDLE;
    const XrResult created = xrCreateInstance(&info, &instance);
    const bool layer_loaded = GetModuleHandleA("vrtread_openxr_layer.dll") != nullptr;
    std::printf("{\"create_instance\":%d,\"layer_loaded_during_create\":%s}\n", static_cast<int>(created), layer_loaded ? "true" : "false");
    if (created != XR_SUCCESS) {
        return 0;  // a runtime that is not available is a valid outcome of this check
    }

    XrInstanceProperties properties{XR_TYPE_INSTANCE_PROPERTIES};
    xrGetInstanceProperties(instance, &properties);
    XrSystemGetInfo system_info{XR_TYPE_SYSTEM_GET_INFO};
    system_info.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
    XrSystemId system_id = XR_NULL_SYSTEM_ID;
    const XrResult system = xrGetSystem(instance, &system_info, &system_id);

    XrActionSetCreateInfo set_info{XR_TYPE_ACTION_SET_CREATE_INFO};
    strncpy_s(set_info.actionSetName, "gameplay", _TRUNCATE);
    strncpy_s(set_info.localizedActionSetName, "Gameplay", _TRUNCATE);
    XrActionSet set = XR_NULL_HANDLE;
    const XrResult set_result = xrCreateActionSet(instance, &set_info, &set);

    XrPath hands[2] = {XR_NULL_PATH, XR_NULL_PATH};
    xrStringToPath(instance, kLeft, &hands[0]);
    xrStringToPath(instance, kRight, &hands[1]);
    XrActionCreateInfo action_info{XR_TYPE_ACTION_CREATE_INFO};
    strncpy_s(action_info.actionName, "thumbstick", _TRUNCATE);
    strncpy_s(action_info.localizedActionName, "Thumbstick", _TRUNCATE);
    action_info.actionType = XR_ACTION_TYPE_VECTOR2F_INPUT;
    action_info.countSubactionPaths = 2;
    action_info.subactionPaths = hands;
    XrAction action = XR_NULL_HANDLE;
    const XrResult action_result = set != XR_NULL_HANDLE ? xrCreateAction(set, &action_info, &action) : XR_ERROR_HANDLE_INVALID;

    XrResult suggest_result = XR_ERROR_HANDLE_INVALID;
    if (action != XR_NULL_HANDLE) {
        XrPath profile = XR_NULL_PATH;
        XrPath left_stick = XR_NULL_PATH;
        XrPath right_stick = XR_NULL_PATH;
        xrStringToPath(instance, kTouchProfile, &profile);
        xrStringToPath(instance, "/user/hand/left/input/thumbstick", &left_stick);
        xrStringToPath(instance, "/user/hand/right/input/thumbstick", &right_stick);
        const XrActionSuggestedBinding bindings[] = {{action, left_stick}, {action, right_stick}};
        XrInteractionProfileSuggestedBinding suggested{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
        suggested.interactionProfile = profile;
        suggested.countSuggestedBindings = 2;
        suggested.suggestedBindings = bindings;
        suggest_result = xrSuggestInteractionProfileBindings(instance, &suggested);
    }

    std::printf("{\"runtime\":\"%s\",\"get_system\":%d,\"create_action_set\":%d,\"create_action\":%d,\"suggest_bindings\":%d}\n",
                json_escape(properties.runtimeName).c_str(), static_cast<int>(system), static_cast<int>(set_result),
                static_cast<int>(action_result), static_cast<int>(suggest_result));
    const XrResult destroyed = xrDestroyInstance(instance);
    std::printf("{\"destroy_instance\":%d}\n", static_cast<int>(destroyed));
    return 0;
}

int run_probe(int frames, int interval_ms, bool implicit, bool simulate_wow64) {
    if (simulate_wow64) {
        // What Windows does for every 32-bit process. It has to be set from inside: Windows strips this
        // variable from the environment block of any 64-bit process at creation, so a parent cannot pass it.
        set_env("PROCESSOR_ARCHITEW6432", "AMD64");
    }
    if (implicit) {
        // Only the runtime is ours. Layer discovery is left entirely to the loader and the registry.
        set_env("XR_RUNTIME_JSON", VRTREAD_TEST_RUNTIME_JSON);
        set_env("XR_API_LAYER_PATH", nullptr);
        set_env("XR_ENABLE_API_LAYERS", nullptr);
    } else {
        configure_loader_environment();
    }
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

    char loaded_layer[MAX_PATH]{};
    if (const HMODULE module = GetModuleHandleA("vrtread_openxr_layer.dll")) {
        GetModuleFileNameA(module, loaded_layer, MAX_PATH);
    }
    std::printf("{\"layer_dll\":\"%s\"}\n", json_escape(loaded_layer).c_str());

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
    std::string real_runtime;
    bool probe = false;
    bool implicit = false;
    bool simulate_wow64 = false;
    int frames = 30;
    int interval_ms = 10;
    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        const auto value = [&]() -> std::string { return index + 1 < argc ? argv[++index] : std::string{}; };
        if (arg == "--filter") {
            filter = value();
        } else if (arg == "--probe") {
            probe = true;
        } else if (arg == "--implicit") {
            implicit = true;
        } else if (arg == "--simulate-wow64") {
            simulate_wow64 = true;
        } else if (arg == "--real-runtime") {
            real_runtime = value();
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

        if (!real_runtime.empty()) {
            std::printf("{\"log_dir\":\"%s\"}\n", json_escape(log_dir).c_str());
            return run_real_runtime_check(real_runtime);
        }
        if (probe) {
            return run_probe(frames, interval_ms, implicit, simulate_wow64);
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
