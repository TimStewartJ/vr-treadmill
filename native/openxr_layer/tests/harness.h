#pragma once

// Test harness: process environment, mock-runtime control, a treadmill publisher, and a `Game`
// helper that drives OpenXR through the real Khronos loader the way an engine would.

#include <windows.h>

#include <openxr/openxr.h>

#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "test_framework.h"
#include "vrtread_shared_memory.h"

#ifndef VRTREAD_TEST_LAYER_DIR
#error "VRTREAD_TEST_LAYER_DIR must be defined by the build"
#endif

namespace harness {

constexpr char kLayerName[] = "XR_APILAYER_VRTREAD_treadmill";
constexpr char kTouchProfile[] = "/interaction_profiles/oculus/touch_controller";
constexpr char kViveProfile[] = "/interaction_profiles/htc/vive_controller";
constexpr char kLeft[] = "/user/hand/left";
constexpr char kRight[] = "/user/hand/right";

inline void set_env(const char* name, const char* value) {
    SetEnvironmentVariableA(name, value);
}

inline std::string layer_dll_path() {
    return std::string(VRTREAD_TEST_LAYER_DIR) + "\\vrtread_openxr_layer.dll";
}

// Points the real loader at the mock runtime and at the layer build under test, and keeps any copy of the
// layer that is *installed on this machine* out of the process:
//   - the loader prefers an implicit layer over an explicit one with the same name, so an installed copy
//     would silently replace the DLL under test;
//   - implicit layers are skipped when their manifest's disable_environment variable is set.
inline void configure_loader_environment() {
    set_env("XR_RUNTIME_JSON", VRTREAD_TEST_RUNTIME_JSON);
    set_env("XR_API_LAYER_PATH", VRTREAD_TEST_LAYER_DIR);
    set_env("XR_ENABLE_API_LAYERS", kLayerName);
    set_env("PROCESSOR_ARCHITEW6432", "AMD64");  // disable_environment of the production manifest
    set_env("VRTREAD_OPENXR_DISABLE", "1");      // disable_environment of the unreleased v1 prototype manifest
    set_env("VRTREAD_OPENXR_BYPASS", nullptr);
    set_env("VRTREAD_OPENXR_ALLOW_SYSTEM_PROCESSES", nullptr);
}

// ---------------------------------------------------------------------------------------------
// Mock runtime control
// ---------------------------------------------------------------------------------------------

struct MockRuntime {
    void (*reset)() = nullptr;
    void (*set_connected)(int hand, int connected) = nullptr;
    void (*set_thumbstick)(int hand, float x, float y) = nullptr;
    void (*set_trackpad)(int hand, float x, float y) = nullptr;
    void (*set_trigger)(int hand, float value) = nullptr;
    void (*set_button)(int hand, const char* name, int pressed) = nullptr;
    void (*set_focused)(int focused) = nullptr;
    void (*set_profile)(const char* profile) = nullptr;
    uint64_t (*call_count)(const char* function_name) = nullptr;
    uint32_t (*live_instances)() = nullptr;

    static MockRuntime& get() {
        static MockRuntime runtime = load();
        return runtime;
    }

private:
    template <typename Fn>
    static void resolve(HMODULE module, const char* name, Fn& out) {
        out = reinterpret_cast<Fn>(GetProcAddress(module, name));
        if (out == nullptr) {
            throw std::runtime_error(std::string("mock runtime is missing export ") + name);
        }
    }

    static MockRuntime load() {
        const HMODULE module = LoadLibraryA(VRTREAD_TEST_RUNTIME_DLL);
        if (module == nullptr) {
            throw std::runtime_error("could not load the mock runtime DLL: " VRTREAD_TEST_RUNTIME_DLL);
        }
        MockRuntime runtime;
        resolve(module, "mockrt_reset", runtime.reset);
        resolve(module, "mockrt_set_connected", runtime.set_connected);
        resolve(module, "mockrt_set_thumbstick", runtime.set_thumbstick);
        resolve(module, "mockrt_set_trackpad", runtime.set_trackpad);
        resolve(module, "mockrt_set_trigger", runtime.set_trigger);
        resolve(module, "mockrt_set_button", runtime.set_button);
        resolve(module, "mockrt_set_focused", runtime.set_focused);
        resolve(module, "mockrt_set_profile", runtime.set_profile);
        resolve(module, "mockrt_call_count", runtime.call_count);
        resolve(module, "mockrt_live_instances", runtime.live_instances);
        return runtime;
    }
};

constexpr int kLeftHand = 0;
constexpr int kRightHand = 1;

// ---------------------------------------------------------------------------------------------
// Treadmill publisher (stands in for the Python app)
// ---------------------------------------------------------------------------------------------

struct PublishOptions {
    uint32_t filter_mode = vrtread::kFilterBalanced;
    uint32_t combine_mode = vrtread::kCombineMaxMagnitude;
    uint32_t options = 0;
    bool active = true;
    int64_t age_ms = 0;  // positive = older than now, negative = in the future
};

class Publisher {
public:
    explicit Publisher(const std::wstring& name) {
        mapping_ = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(vrtread::SharedState), name.c_str());
        if (mapping_ == nullptr) {
            throw std::runtime_error("CreateFileMappingW failed");
        }
        view_ = static_cast<vrtread::SharedState*>(MapViewOfFile(mapping_, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(vrtread::SharedState)));
        if (view_ == nullptr) {
            throw std::runtime_error("MapViewOfFile failed");
        }
        idle();
    }

    ~Publisher() {
        if (view_ != nullptr) {
            UnmapViewOfFile(view_);
        }
        if (mapping_ != nullptr) {
            CloseHandle(mapping_);
        }
    }

    Publisher(const Publisher&) = delete;
    Publisher& operator=(const Publisher&) = delete;

    vrtread::SharedState make_block(float y, const PublishOptions& options = {}) const {
        vrtread::SharedState block{};
        block.magic = vrtread::kMagic;
        block.version = vrtread::kVersion;
        block.struct_size = sizeof(vrtread::SharedState);
        block.flags = options.active ? vrtread::kFlagActive : 0U;
        block.timestamp_ms = static_cast<uint64_t>(static_cast<int64_t>(GetTickCount64()) - options.age_ms);
        block.x = 0.0F;
        block.y = y;
        block.filter_mode = options.filter_mode;
        block.combine_mode = options.combine_mode;
        block.options = options.options;
        block.publisher_pid = GetCurrentProcessId();
        return block;
    }

    void publish(float y, const PublishOptions& options = {}) { write(make_block(y, options)); }

    // Publisher running, treadmill standing still.
    void idle(const PublishOptions& options = {}) { publish(0.0F, options); }

    // Same ordering as the Python writer: odd seq, payload, even seq.
    void write(vrtread::SharedState block, bool leave_mid_write = false) {
        const uint64_t odd = seq_ + 1;
        const uint64_t even = seq_ + 2;
        view_->seq = odd;
        block.seq = odd;
        std::memcpy(view_, &block, sizeof(block));
        if (!leave_mid_write) {
            view_->seq = even;
        }
        seq_ = even;
    }

private:
    HANDLE mapping_ = nullptr;
    vrtread::SharedState* view_ = nullptr;
    uint64_t seq_ = 0;
};

// ---------------------------------------------------------------------------------------------
// Game: an OpenXR client, through the real loader
// ---------------------------------------------------------------------------------------------

struct Bind {
    XrAction action;
    const char* path;
};

class Game {
public:
    explicit Game(const char* application_name = "VRTreadmillTest", const char* engine_name = "TestEngine",
                  XrVersion api_version = XR_MAKE_VERSION(1, 0, 0)) {
        XrInstanceCreateInfo info{XR_TYPE_INSTANCE_CREATE_INFO};
        strncpy_s(info.applicationInfo.applicationName, application_name, _TRUNCATE);
        strncpy_s(info.applicationInfo.engineName, engine_name, _TRUNCATE);
        info.applicationInfo.applicationVersion = 1;
        info.applicationInfo.engineVersion = 1;
        info.applicationInfo.apiVersion = api_version;
        const char* extensions[] = {"XR_MND_headless"};
        info.enabledExtensionCount = 1;
        info.enabledExtensionNames = extensions;
        create_result = xrCreateInstance(&info, &instance);
        if (create_result != XR_SUCCESS) {
            return;
        }

        XrSystemGetInfo system_info{XR_TYPE_SYSTEM_GET_INFO};
        system_info.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
        XrSystemId system_id = XR_NULL_SYSTEM_ID;
        REQUIRE_XR(xrGetSystem(instance, &system_info, &system_id));

        XrSessionCreateInfo session_info{XR_TYPE_SESSION_CREATE_INFO};
        session_info.systemId = system_id;
        REQUIRE_XR(xrCreateSession(instance, &session_info, &session));

        left = path(kLeft);
        right = path(kRight);
    }

    ~Game() { shutdown(); }

    Game(const Game&) = delete;
    Game& operator=(const Game&) = delete;

    void shutdown() {
        if (session != XR_NULL_HANDLE) {
            xrDestroySession(session);
            session = XR_NULL_HANDLE;
        }
        if (instance != XR_NULL_HANDLE) {
            xrDestroyInstance(instance);
            instance = XR_NULL_HANDLE;
        }
    }

    XrPath path(const char* value) const {
        XrPath out = XR_NULL_PATH;
        REQUIRE_XR(xrStringToPath(instance, value, &out));
        return out;
    }

    XrActionSet create_set(const char* name, uint32_t priority = 0) const {
        XrActionSetCreateInfo info{XR_TYPE_ACTION_SET_CREATE_INFO};
        strncpy_s(info.actionSetName, name, _TRUNCATE);
        strncpy_s(info.localizedActionSetName, name, _TRUNCATE);
        info.priority = priority;
        XrActionSet set = XR_NULL_HANDLE;
        REQUIRE_XR(xrCreateActionSet(instance, &info, &set));
        return set;
    }

    XrAction create_action(XrActionSet set, const char* name, XrActionType type, const std::vector<XrPath>& subactions = {},
                           const char* localized = nullptr) const {
        XrActionCreateInfo info{XR_TYPE_ACTION_CREATE_INFO};
        strncpy_s(info.actionName, name, _TRUNCATE);
        strncpy_s(info.localizedActionName, localized != nullptr ? localized : name, _TRUNCATE);
        info.actionType = type;
        info.countSubactionPaths = static_cast<uint32_t>(subactions.size());
        info.subactionPaths = subactions.empty() ? nullptr : subactions.data();
        XrAction action = XR_NULL_HANDLE;
        REQUIRE_XR(xrCreateAction(set, &info, &action));
        return action;
    }

    std::vector<XrPath> both_hands() const { return {left, right}; }

    void suggest(const char* profile, const std::vector<Bind>& bindings) const {
        std::vector<XrActionSuggestedBinding> suggested;
        for (const Bind& bind : bindings) {
            suggested.push_back(XrActionSuggestedBinding{bind.action, path(bind.path)});
        }
        XrInteractionProfileSuggestedBinding info{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
        info.interactionProfile = path(profile);
        info.countSuggestedBindings = static_cast<uint32_t>(suggested.size());
        info.suggestedBindings = suggested.data();
        REQUIRE_XR(xrSuggestInteractionProfileBindings(instance, &info));
    }

    void attach(const std::vector<XrActionSet>& sets) const {
        XrSessionActionSetsAttachInfo info{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};
        info.countActionSets = static_cast<uint32_t>(sets.size());
        info.actionSets = sets.data();
        REQUIRE_XR(xrAttachSessionActionSets(session, &info));
    }

    XrResult sync(const std::vector<XrActionSet>& sets) const {
        std::vector<XrActiveActionSet> active;
        for (const XrActionSet set : sets) {
            active.push_back(XrActiveActionSet{set, XR_NULL_PATH});
        }
        XrActionsSyncInfo info{XR_TYPE_ACTIONS_SYNC_INFO};
        info.countActiveActionSets = static_cast<uint32_t>(active.size());
        info.activeActionSets = active.data();
        return xrSyncActions(session, &info);
    }

    XrActionStateVector2f vec2(XrAction action, XrPath subaction = XR_NULL_PATH) const {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
        info.action = action;
        info.subactionPath = subaction;
        XrActionStateVector2f state{XR_TYPE_ACTION_STATE_VECTOR2F};
        REQUIRE_XR(xrGetActionStateVector2f(session, &info, &state));
        return state;
    }

    XrActionStateFloat scalar(XrAction action, XrPath subaction = XR_NULL_PATH) const {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
        info.action = action;
        info.subactionPath = subaction;
        XrActionStateFloat state{XR_TYPE_ACTION_STATE_FLOAT};
        REQUIRE_XR(xrGetActionStateFloat(session, &info, &state));
        return state;
    }

    XrActionStateBoolean boolean(XrAction action, XrPath subaction = XR_NULL_PATH) const {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
        info.action = action;
        info.subactionPath = subaction;
        XrActionStateBoolean state{XR_TYPE_ACTION_STATE_BOOLEAN};
        REQUIRE_XR(xrGetActionStateBoolean(session, &info, &state));
        return state;
    }

    XrResult create_result = XR_ERROR_RUNTIME_FAILURE;
    XrInstance instance = XR_NULL_HANDLE;
    XrSession session = XR_NULL_HANDLE;
    XrPath left = XR_NULL_PATH;
    XrPath right = XR_NULL_PATH;
};

// Process-wide fixtures, configured by main().
struct Fixtures {
    Publisher* publisher = nullptr;  // created on first use, so the first test can run without any mapping
    std::wstring mapping_name;
    std::string log_dir;
};

inline Fixtures& fixtures() {
    static Fixtures instance;
    return instance;
}

inline Publisher& publisher() {
    Fixtures& all = fixtures();
    if (all.publisher == nullptr) {
        all.publisher = new Publisher(all.mapping_name);
    }
    return *all.publisher;
}

// Every scenario starts from: mock hardware reset, publisher running but standing still.
inline void reset_world() {
    configure_loader_environment();
    MockRuntime::get().reset();
    if (fixtures().publisher != nullptr) {
        fixtures().publisher->idle();
    }
}

}  // namespace harness
