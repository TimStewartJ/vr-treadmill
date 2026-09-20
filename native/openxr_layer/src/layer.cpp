#include "openxr_minimal.h"
#include "vrtread_shared_memory.h"

#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cstring>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

PFN_xrGetInstanceProcAddr g_next_get_instance_proc_addr = nullptr;
PFN_xrCreateApiLayerInstance g_next_create_api_layer_instance = nullptr;
PFN_xrGetActionStateVector2f g_next_get_action_state_vector2f = nullptr;
PFN_xrGetActionStateFloat g_next_get_action_state_float = nullptr;
PFN_xrCreateActionSet g_next_create_action_set = nullptr;
PFN_xrDestroyActionSet g_next_destroy_action_set = nullptr;
PFN_xrCreateAction g_next_create_action = nullptr;
PFN_xrDestroyAction g_next_destroy_action = nullptr;
PFN_xrSuggestInteractionProfileBindings g_next_suggest_interaction_profile_bindings = nullptr;
PFN_xrStringToPath g_next_string_to_path = nullptr;
PFN_xrPathToString g_next_path_to_string = nullptr;
PFN_xrSyncActions g_next_sync_actions = nullptr;
PFN_xrDestroySession g_next_destroy_session = nullptr;
PFN_xrDestroyInstance g_next_destroy_instance = nullptr;

struct PathKey {
    XrInstance instance;
    XrPath path;

    bool operator==(const PathKey& other) const {
        return instance == other.instance && path == other.path;
    }
};

struct PathKeyHash {
    size_t operator()(const PathKey& key) const {
        const auto instance_value = reinterpret_cast<uintptr_t>(key.instance);
        return std::hash<uintptr_t>{}(instance_value) ^ (std::hash<XrPath>{}(key.path) << 1U);
    }
};

struct ActionInfo {
    XrInstance instance = nullptr;
    XrActionSet action_set = nullptr;
    std::string name;
    XrActionType type = XR_ACTION_TYPE_BOOLEAN_INPUT;
    bool bound_left_vector = false;
    bool bound_left_y = false;
};

struct SessionInfo {
    uint64_t last_sync_ms = 0;
};

struct SharedSnapshot {
    float x = 0.0F;
    float y = 0.0F;
    uint32_t filter_mode = vrtread::kFilterModeBalanced;
};

std::mutex g_state_mutex;
std::unordered_map<XrActionSet, XrInstance> g_action_set_instances;
std::unordered_map<XrAction, ActionInfo> g_actions;
std::unordered_map<PathKey, std::string, PathKeyHash> g_path_strings;
std::unordered_map<XrSession, SessionInfo> g_sessions;

uint64_t now_ms() {
    return GetTickCount64();
}

std::string env_string(const char* name) {
    char buffer[512]{};
    const DWORD length = GetEnvironmentVariableA(name, buffer, static_cast<DWORD>(sizeof(buffer)));
    if (length == 0 || length >= sizeof(buffer)) {
        return {};
    }
    return buffer;
}

bool env_enabled(const char* name, bool default_value) {
    const std::string value = env_string(name);
    if (value.empty()) {
        return default_value;
    }
    return value != "0" && _stricmp(value.c_str(), "false") != 0 && _stricmp(value.c_str(), "no") != 0;
}

std::optional<bool> env_bool_override(const char* name) {
    const std::string value = env_string(name);
    if (value.empty()) {
        return std::nullopt;
    }
    return value != "0" && _stricmp(value.c_str(), "false") != 0 && _stricmp(value.c_str(), "no") != 0;
}

bool layer_disabled() {
    return env_enabled("VRTREAD_OPENXR_DISABLE", false);
}

std::string lower_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

bool contains_any(const std::string& value, const std::vector<const char*>& needles) {
    return std::any_of(needles.begin(), needles.end(), [&value](const char* needle) {
        return value.find(needle) != std::string::npos;
    });
}

bool action_name_is_menu_or_system(const std::string& action_name) {
    const std::string name = lower_copy(action_name);
    return contains_any(
        name,
        {"menu", "ui", "userinterface", "user_interface", "dashboard", "system", "pause", "inventory",
         "map", "settings", "options", "navigate", "navigation", "scroll", "select", "click", "cursor",
         "pointer", "laser", "teleport", "snap"});
}

bool action_name_looks_like_locomotion(const std::string& action_name) {
    const std::string name = lower_copy(action_name);
    return contains_any(
        name,
        {"move", "movement", "locomotion", "walk", "run", "strafe", "forward", "backward", "stick",
         "thumbstick", "joystick", "axis"});
}

bool action_name_looks_like_y_axis(const std::string& action_name) {
    const std::string name = lower_copy(action_name);
    return contains_any(
        name,
        {"move", "movement", "locomotion", "walk", "run", "forward", "backward", "stick_y",
         "thumbstick_y", "joystick_y", "axis_y", "_y", " y"});
}

bool ends_with(const std::string& value, const char* suffix) {
    const std::string suffix_value = suffix;
    if (value.size() < suffix_value.size()) {
        return false;
    }
    return value.compare(value.size() - suffix_value.size(), suffix_value.size(), suffix_value) == 0;
}

enum class BindingKind {
    None,
    LeftStickVector,
    LeftStickY,
};

BindingKind classify_binding_path(const std::string& path_string) {
    const std::string path = lower_copy(path_string);
    const bool left_hand = path.find("/user/hand/left/input/") != std::string::npos;
    const bool stick = path.find("/thumbstick") != std::string::npos || path.find("/joystick") != std::string::npos;
    if (!left_hand || !stick) {
        return BindingKind::None;
    }
    if (ends_with(path, "/y")) {
        return BindingKind::LeftStickY;
    }
    if (!ends_with(path, "/x")) {
        return BindingKind::LeftStickVector;
    }
    return BindingKind::None;
}

std::string process_basename() {
    char path[MAX_PATH]{};
    const DWORD length = GetModuleFileNameA(nullptr, path, static_cast<DWORD>(sizeof(path)));
    if (length == 0 || length >= sizeof(path)) {
        return {};
    }
    std::string value(path, length);
    const size_t slash = value.find_last_of("\\/");
    if (slash != std::string::npos) {
        value = value.substr(slash + 1);
    }
    return lower_copy(value);
}

bool process_allowed() {
    if (env_enabled("VRTREAD_OPENXR_ALLOW_SYSTEM_PROCESSES", false)) {
        return true;
    }

    static const std::string name = process_basename();
    return !contains_any(
        name,
        {"vrserver", "vrcompositor", "vrdashboard", "vrmonitor", "vrstartup", "vrwebhelper", "oculusclient",
         "oculusdash", "ovrserver", "ovrredir", "mixedrealityportal", "wmrhost", "openxr_runtime"});
}

void debug_log(const char* message) {
    if (!env_enabled("VRTREAD_OPENXR_LOG", false)) {
        return;
    }
    OutputDebugStringA("[VRTread OpenXR] ");
    OutputDebugStringA(message);
    OutputDebugStringA("\n");
}

bool session_recently_synced(XrSession session) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    const auto it = g_sessions.find(session);
    if (it == g_sessions.end()) {
        return false;
    }
    const uint64_t elapsed = now_ms() - it->second.last_sync_ms;
    return elapsed <= 1000;
}

uint32_t normalize_filter_mode(uint64_t raw_mode) {
    const uint32_t mode = static_cast<uint32_t>(raw_mode & vrtread::kFilterModeMask);
    if (mode == vrtread::kFilterModeStrict || mode == vrtread::kFilterModeCompatibility) {
        return mode;
    }
    return vrtread::kFilterModeBalanced;
}

class SharedStateReader {
public:
    bool read(SharedSnapshot& snapshot_out) {
        if (!ensure_mapping()) {
            return false;
        }

        const auto* state = reinterpret_cast<const vrtread::SharedState*>(view_);
        const uint64_t first_seq = state->seq;
        if ((first_seq & 1U) != 0U) {
            return false;
        }

        vrtread::SharedState snapshot{};
        std::memcpy(&snapshot, state, sizeof(snapshot));

        const uint64_t second_seq = state->seq;
        if (first_seq != second_seq || (second_seq & 1U) != 0U) {
            return false;
        }
        if (snapshot.magic != vrtread::kMagic || snapshot.version != vrtread::kVersion ||
            snapshot.struct_size != sizeof(vrtread::SharedState)) {
            return false;
        }
        if ((snapshot.flags & vrtread::kActiveFlag) == 0U) {
            return false;
        }

        const uint64_t now = now_ms();
        if (snapshot.timestamp_ms == 0 || now < snapshot.timestamp_ms ||
            now - snapshot.timestamp_ms > vrtread::kStaleMs) {
            return false;
        }

        snapshot_out.x = snapshot.x;
        snapshot_out.y = snapshot.y;
        snapshot_out.filter_mode = normalize_filter_mode(snapshot.reserved0);
        return true;
    }

private:
    bool ensure_mapping() {
        if (view_ != nullptr) {
            return true;
        }

        const uint64_t now = now_ms();
        if (now < next_retry_ms_) {
            return false;
        }
        next_retry_ms_ = now + 1000;

        handle_ = OpenFileMappingW(FILE_MAP_READ, FALSE, vrtread::kMappingName);
        if (handle_ == nullptr) {
            return false;
        }
        view_ = MapViewOfFile(handle_, FILE_MAP_READ, 0, 0, sizeof(vrtread::SharedState));
        if (view_ == nullptr) {
            CloseHandle(handle_);
            handle_ = nullptr;
            return false;
        }
        return true;
    }

    HANDLE handle_ = nullptr;
    void* view_ = nullptr;
    uint64_t next_retry_ms_ = 0;
};

SharedStateReader g_reader;

std::string path_to_string(XrInstance instance, XrPath path) {
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        const auto it = g_path_strings.find(PathKey{instance, path});
        if (it != g_path_strings.end()) {
            return it->second;
        }
    }

    if (g_next_path_to_string == nullptr) {
        return {};
    }

    uint32_t count = 0;
    if (!XR_SUCCEEDED(g_next_path_to_string(instance, path, 0, &count, nullptr)) || count == 0) {
        return {};
    }

    std::vector<char> buffer(count);
    if (!XR_SUCCEEDED(g_next_path_to_string(instance, path, count, &count, buffer.data()))) {
        return {};
    }

    std::string value(buffer.data());
    std::lock_guard<std::mutex> lock(g_state_mutex);
    g_path_strings[PathKey{instance, path}] = value;
    return value;
}

void record_path_string(XrInstance instance, XrPath path, const char* path_string) {
    if (instance == nullptr || path == 0 || path_string == nullptr) {
        return;
    }
    std::lock_guard<std::mutex> lock(g_state_mutex);
    g_path_strings[PathKey{instance, path}] = path_string;
}

void record_suggested_bindings(XrInstance instance, const XrInteractionProfileSuggestedBinding* suggested_bindings) {
    if (suggested_bindings == nullptr || suggested_bindings->suggestedBindings == nullptr) {
        return;
    }

    for (uint32_t index = 0; index < suggested_bindings->countSuggestedBindings; ++index) {
        const XrActionSuggestedBinding& binding = suggested_bindings->suggestedBindings[index];
        const BindingKind kind = classify_binding_path(path_to_string(instance, binding.binding));
        if (kind == BindingKind::None || binding.action == nullptr) {
            continue;
        }

        std::lock_guard<std::mutex> lock(g_state_mutex);
        auto it = g_actions.find(binding.action);
        if (it == g_actions.end()) {
            continue;
        }
        if (kind == BindingKind::LeftStickVector) {
            it->second.bound_left_vector = true;
        } else if (kind == BindingKind::LeftStickY) {
            it->second.bound_left_y = true;
        }
    }
}

bool copy_action_info(XrAction action, ActionInfo& info) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    const auto it = g_actions.find(action);
    if (it == g_actions.end()) {
        return false;
    }
    info = it->second;
    return true;
}

bool allow_unbound_actions(uint32_t filter_mode) {
    if (const std::optional<bool> env_value = env_bool_override("VRTREAD_OPENXR_ALLOW_UNBOUND_ACTIONS")) {
        return *env_value;
    }
    return filter_mode == vrtread::kFilterModeCompatibility;
}

bool allow_generic_bound_sticks(uint32_t filter_mode) {
    if (const std::optional<bool> env_value = env_bool_override("VRTREAD_OPENXR_ALLOW_GENERIC_BOUND_STICKS")) {
        return *env_value;
    }
    return filter_mode == vrtread::kFilterModeBalanced || filter_mode == vrtread::kFilterModeCompatibility;
}

bool should_override_vector2_action(XrAction action, uint32_t filter_mode) {
    if (env_enabled("VRTREAD_OPENXR_OVERRIDE_ALL_VECTOR2", false)) {
        return true;
    }

    ActionInfo info{};
    if (!copy_action_info(action, info) || info.type != XR_ACTION_TYPE_VECTOR2F_INPUT) {
        return false;
    }
    if (action_name_is_menu_or_system(info.name)) {
        return false;
    }
    const bool locomotion_name = action_name_looks_like_locomotion(info.name);
    if (info.bound_left_vector) {
        return locomotion_name || allow_generic_bound_sticks(filter_mode);
    }

    return allow_unbound_actions(filter_mode) && locomotion_name;
}

bool should_override_float_action(XrAction action, uint32_t filter_mode) {
    if (!env_enabled("VRTREAD_OPENXR_OVERRIDE_FLOAT", true)) {
        return false;
    }

    ActionInfo info{};
    if (!copy_action_info(action, info) || info.type != XR_ACTION_TYPE_FLOAT_INPUT) {
        return false;
    }
    if (action_name_is_menu_or_system(info.name)) {
        return false;
    }
    const bool y_axis_name = action_name_looks_like_y_axis(info.name);
    if (info.bound_left_y) {
        return y_axis_name || allow_generic_bound_sticks(filter_mode);
    }
    return allow_unbound_actions(filter_mode) && y_axis_name;
}

bool common_override_gates_pass(XrSession session, XrBool32 runtime_is_active) {
    if (layer_disabled() || !process_allowed()) {
        return false;
    }
    if (!session_recently_synced(session)) {
        return false;
    }
    if (runtime_is_active != XR_TRUE && !env_enabled("VRTREAD_OPENXR_OVERRIDE_INACTIVE", false)) {
        debug_log("runtime action is inactive; passing through");
        return false;
    }
    return true;
}

void cleanup_instance(XrInstance instance) {
    std::lock_guard<std::mutex> lock(g_state_mutex);
    for (auto it = g_path_strings.begin(); it != g_path_strings.end();) {
        if (it->first.instance == instance) {
            it = g_path_strings.erase(it);
        } else {
            ++it;
        }
    }
    for (auto it = g_action_set_instances.begin(); it != g_action_set_instances.end();) {
        if (it->second == instance) {
            it = g_action_set_instances.erase(it);
        } else {
            ++it;
        }
    }
    for (auto it = g_actions.begin(); it != g_actions.end();) {
        if (it->second.instance == instance) {
            it = g_actions.erase(it);
        } else {
            ++it;
        }
    }
}

XrResult XRAPI_CALL layer_xrGetActionStateVector2f(
    XrSession session,
    const XrActionStateGetInfo* get_info,
    XrActionStateVector2f* state) {
    if (g_next_get_action_state_vector2f == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }

    const XrResult result = g_next_get_action_state_vector2f(session, get_info, state);
    if (!XR_SUCCEEDED(result) || state == nullptr || get_info == nullptr) {
        return result;
    }
    if (!common_override_gates_pass(session, state->isActive)) {
        return result;
    }

    SharedSnapshot treadmill{};
    if (g_reader.read(treadmill) && should_override_vector2_action(get_info->action, treadmill.filter_mode)) {
        state->currentState.y = treadmill.y;
        if (env_enabled("VRTREAD_OPENXR_OVERRIDE_X", false)) {
            state->currentState.x = treadmill.x;
        }
        state->changedSinceLastSync = XR_TRUE;
    }

    return result;
}

XrResult XRAPI_CALL layer_xrGetActionStateFloat(
    XrSession session,
    const XrActionStateGetInfo* get_info,
    XrActionStateFloat* state) {
    if (g_next_get_action_state_float == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }

    const XrResult result = g_next_get_action_state_float(session, get_info, state);
    if (!XR_SUCCEEDED(result) || state == nullptr || get_info == nullptr) {
        return result;
    }
    if (!common_override_gates_pass(session, state->isActive)) {
        return result;
    }

    SharedSnapshot treadmill{};
    if (g_reader.read(treadmill) && should_override_float_action(get_info->action, treadmill.filter_mode)) {
        state->currentState = treadmill.y;
        state->changedSinceLastSync = XR_TRUE;
    }

    return result;
}

XrResult XRAPI_CALL layer_xrCreateActionSet(
    XrInstance instance,
    const XrActionSetCreateInfo* create_info,
    XrActionSet* action_set) {
    if (g_next_create_action_set == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }

    const XrResult result = g_next_create_action_set(instance, create_info, action_set);
    if (XR_SUCCEEDED(result) && action_set != nullptr && *action_set != nullptr) {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        g_action_set_instances[*action_set] = instance;
    }
    return result;
}

XrResult XRAPI_CALL layer_xrDestroyActionSet(XrActionSet action_set) {
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        g_action_set_instances.erase(action_set);
        for (auto it = g_actions.begin(); it != g_actions.end();) {
            if (it->second.action_set == action_set) {
                it = g_actions.erase(it);
            } else {
                ++it;
            }
        }
    }
    if (g_next_destroy_action_set == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }
    return g_next_destroy_action_set(action_set);
}

XrResult XRAPI_CALL layer_xrCreateAction(
    XrActionSet action_set,
    const XrActionCreateInfo* create_info,
    XrAction* action) {
    if (g_next_create_action == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }

    const XrResult result = g_next_create_action(action_set, create_info, action);
    if (XR_SUCCEEDED(result) && create_info != nullptr && action != nullptr && *action != nullptr) {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        ActionInfo info{};
        const auto action_set_it = g_action_set_instances.find(action_set);
        if (action_set_it != g_action_set_instances.end()) {
            info.instance = action_set_it->second;
        }
        info.action_set = action_set;
        info.name = create_info->actionName;
        info.type = create_info->actionType;
        g_actions[*action] = info;
    }
    return result;
}

XrResult XRAPI_CALL layer_xrDestroyAction(XrAction action) {
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        g_actions.erase(action);
    }
    if (g_next_destroy_action == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }
    return g_next_destroy_action(action);
}

XrResult XRAPI_CALL layer_xrSuggestInteractionProfileBindings(
    XrInstance instance,
    const XrInteractionProfileSuggestedBinding* suggested_bindings) {
    if (g_next_suggest_interaction_profile_bindings == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }

    const XrResult result = g_next_suggest_interaction_profile_bindings(instance, suggested_bindings);
    if (XR_SUCCEEDED(result)) {
        record_suggested_bindings(instance, suggested_bindings);
    }
    return result;
}

XrResult XRAPI_CALL layer_xrStringToPath(XrInstance instance, const char* path_string, XrPath* path) {
    if (g_next_string_to_path == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }

    const XrResult result = g_next_string_to_path(instance, path_string, path);
    if (XR_SUCCEEDED(result) && path != nullptr) {
        record_path_string(instance, *path, path_string);
    }
    return result;
}

XrResult XRAPI_CALL layer_xrSyncActions(XrSession session, const XrActionsSyncInfo* sync_info) {
    if (g_next_sync_actions == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }

    const XrResult result = g_next_sync_actions(session, sync_info);
    if (XR_SUCCEEDED(result)) {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        g_sessions[session].last_sync_ms = now_ms();
    }
    return result;
}

XrResult XRAPI_CALL layer_xrDestroySession(XrSession session) {
    {
        std::lock_guard<std::mutex> lock(g_state_mutex);
        g_sessions.erase(session);
    }
    if (g_next_destroy_session == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }
    return g_next_destroy_session(session);
}

XrResult XRAPI_CALL layer_xrDestroyInstance(XrInstance instance) {
    cleanup_instance(instance);
    if (g_next_destroy_instance == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }
    return g_next_destroy_instance(instance);
}

XrResult XRAPI_CALL layer_xrGetInstanceProcAddr(
    XrInstance instance,
    const char* name,
    PFN_xrVoidFunction* function) {
    if (function == nullptr || name == nullptr) {
        return XR_ERROR_RUNTIME_FAILURE;
    }
    if (std::strcmp(name, "xrGetInstanceProcAddr") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrGetInstanceProcAddr);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrGetActionStateVector2f") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrGetActionStateVector2f);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrGetActionStateFloat") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrGetActionStateFloat);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrCreateActionSet") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrCreateActionSet);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrDestroyActionSet") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrDestroyActionSet);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrCreateAction") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrCreateAction);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrDestroyAction") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrDestroyAction);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrSuggestInteractionProfileBindings") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrSuggestInteractionProfileBindings);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrStringToPath") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrStringToPath);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrSyncActions") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrSyncActions);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrDestroySession") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrDestroySession);
        return XR_SUCCESS;
    }
    if (std::strcmp(name, "xrDestroyInstance") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(layer_xrDestroyInstance);
        return XR_SUCCESS;
    }
    if (g_next_get_instance_proc_addr == nullptr) {
        return XR_ERROR_FUNCTION_UNSUPPORTED;
    }
    return g_next_get_instance_proc_addr(instance, name, function);
}

void load_next_functions(XrInstance instance) {
    g_next_get_action_state_vector2f = nullptr;
    g_next_get_action_state_float = nullptr;
    g_next_create_action_set = nullptr;
    g_next_destroy_action_set = nullptr;
    g_next_create_action = nullptr;
    g_next_destroy_action = nullptr;
    g_next_suggest_interaction_profile_bindings = nullptr;
    g_next_string_to_path = nullptr;
    g_next_path_to_string = nullptr;
    g_next_sync_actions = nullptr;
    g_next_destroy_session = nullptr;
    g_next_destroy_instance = nullptr;

    g_next_get_instance_proc_addr(instance, "xrGetActionStateVector2f",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_get_action_state_vector2f));
    g_next_get_instance_proc_addr(instance, "xrGetActionStateFloat",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_get_action_state_float));
    g_next_get_instance_proc_addr(instance, "xrCreateActionSet",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_create_action_set));
    g_next_get_instance_proc_addr(instance, "xrDestroyActionSet",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_destroy_action_set));
    g_next_get_instance_proc_addr(instance, "xrCreateAction",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_create_action));
    g_next_get_instance_proc_addr(instance, "xrDestroyAction",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_destroy_action));
    g_next_get_instance_proc_addr(instance, "xrSuggestInteractionProfileBindings",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_suggest_interaction_profile_bindings));
    g_next_get_instance_proc_addr(instance, "xrStringToPath",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_string_to_path));
    g_next_get_instance_proc_addr(instance, "xrPathToString",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_path_to_string));
    g_next_get_instance_proc_addr(instance, "xrSyncActions",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_sync_actions));
    g_next_get_instance_proc_addr(instance, "xrDestroySession",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_destroy_session));
    g_next_get_instance_proc_addr(instance, "xrDestroyInstance",
                                  reinterpret_cast<PFN_xrVoidFunction*>(&g_next_destroy_instance));
}

XrResult XRAPI_CALL layer_xrCreateApiLayerInstance(
    const XrInstanceCreateInfo* info,
    const XrApiLayerCreateInfo* api_layer_info,
    XrInstance* instance) {
    if (api_layer_info == nullptr || api_layer_info->nextInfo == nullptr ||
        api_layer_info->nextInfo->nextGetInstanceProcAddr == nullptr ||
        api_layer_info->nextInfo->nextCreateApiLayerInstance == nullptr) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }

    XrApiLayerNextInfo* next = api_layer_info->nextInfo;
    g_next_get_instance_proc_addr = next->nextGetInstanceProcAddr;
    g_next_create_api_layer_instance = next->nextCreateApiLayerInstance;

    XrApiLayerCreateInfo next_layer_info = *api_layer_info;
    next_layer_info.nextInfo = next->next;
    const XrResult result = g_next_create_api_layer_instance(info, &next_layer_info, instance);
    if (!XR_SUCCEEDED(result) || instance == nullptr || *instance == nullptr) {
        return result;
    }

    load_next_functions(*instance);
    return result;
}

}  // namespace

extern "C" __declspec(dllexport) XrResult XRAPI_CALL xrNegotiateLoaderApiLayerInterface(
    const XrNegotiateLoaderInfo* loader_info,
    const char* layer_name,
    XrNegotiateApiLayerRequest* api_layer_request) {
    if (loader_info == nullptr || layer_name == nullptr || api_layer_request == nullptr) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    if (std::strcmp(layer_name, "XR_APILAYER_VRTREAD_treadmill") != 0) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    if (loader_info->maxInterfaceVersion < XR_CURRENT_LOADER_API_LAYER_VERSION) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }

    api_layer_request->structType = XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST;
    api_layer_request->structVersion = XR_API_LAYER_INFO_STRUCT_VERSION;
    api_layer_request->structSize = sizeof(XrNegotiateApiLayerRequest);
    api_layer_request->layerInterfaceVersion = XR_CURRENT_LOADER_API_LAYER_VERSION;
    api_layer_request->layerApiVersion = XR_API_VERSION_1_0;
    api_layer_request->getInstanceProcAddr = layer_xrGetInstanceProcAddr;
    api_layer_request->createApiLayerInstance = layer_xrCreateApiLayerInstance;
    return XR_SUCCESS;
}
