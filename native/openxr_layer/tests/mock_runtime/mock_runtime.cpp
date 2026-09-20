// Mock OpenXR runtime for testing the VR Treadmill API layer through the real Khronos loader.
//
// It implements just enough of the OpenXR 1.0 input system, faithfully enough, to stand in for
// SteamVR with a pair of Touch controllers:
//   - paths, action sets (with priority), actions (with subaction paths), suggested bindings
//   - xrAttachSessionActionSets / xrSyncActions latch device state; reads are stable until the next sync
//   - per-subaction reads, and XR_NULL_PATH reads that combine every bound source
//     (vector2f: longest vector wins, float: largest magnitude wins, boolean: OR)
//   - isActive is false when the controller is off, the set was not synced, the session is unfocused,
//     or a higher-priority active set is bound to the same physical input
//   - changedSinceLastSync / lastChangeTime
//
// Tests drive the simulated hardware through the exported mockrt_* control functions.

#include <openxr/openxr.h>
#include <openxr/openxr_loader_negotiation.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#define MOCKRT_API extern "C" __declspec(dllexport)

namespace {

struct Controller {
    bool connected = true;
    float thumbstick_x = 0.0F;
    float thumbstick_y = 0.0F;
    float trackpad_x = 0.0F;
    float trackpad_y = 0.0F;
    float trigger = 0.0F;
    float squeeze = 0.0F;
    std::map<std::string, bool> buttons;  // keyed by "<identifier>/<component>", e.g. "a/click"
};

struct Devices {
    Controller hands[2];  // 0 = left, 1 = right
};

struct Instance;
struct ActionSet;

struct Action {
    ActionSet* set = nullptr;
    std::string name;
    XrActionType type = XR_ACTION_TYPE_BOOLEAN_INPUT;
    std::vector<XrPath> subaction_paths;
};

struct ActionSet {
    Instance* instance = nullptr;
    std::string name;
    uint32_t priority = 0;
    bool attached = false;
    std::vector<std::unique_ptr<Action>> actions;
};

struct Binding {
    Action* action = nullptr;
    std::string path;  // full binding path string
};

struct ReadKey {
    Action* action;
    XrPath subaction;
    bool operator<(const ReadKey& other) const {
        return action != other.action ? action < other.action : subaction < other.subaction;
    }
};

struct ReadHistory {
    uint64_t generation = 0;
    float x = 0.0F;
    float y = 0.0F;
    bool active = false;
    bool changed = false;
    XrTime last_change_time = 0;
};

struct ActiveSet {
    ActionSet* set;
    XrPath subaction;
};

struct Session {
    Instance* instance = nullptr;
    std::vector<ActionSet*> attached;
    std::vector<ActiveSet> active;
    Devices latched;
    bool latched_focused = false;
    uint64_t generation = 0;
    XrTime sync_time = 0;
    std::map<ReadKey, ReadHistory> history;
};

struct Instance {
    std::vector<std::string> paths;  // XrPath = index + 1
    std::unordered_map<std::string, XrPath> path_lookup;
    std::vector<std::unique_ptr<ActionSet>> action_sets;
    std::vector<std::unique_ptr<Session>> sessions;
    std::map<std::string, std::vector<Binding>> suggested;  // by interaction profile string
};

struct Runtime {
    std::mutex mutex;
    std::vector<std::unique_ptr<Instance>> instances;
    Devices devices;
    bool focused = true;
    std::string profile = "/interaction_profiles/oculus/touch_controller";
    XrTime now = 1'000'000'000;
    std::map<std::string, uint64_t> call_counts;
};

Runtime& rt() {
    static Runtime* runtime = new Runtime();
    return *runtime;
}

void count_call(const char* name) {
    ++rt().call_counts[name];
}

Instance* as_instance(XrInstance handle) {
    auto* candidate = reinterpret_cast<Instance*>(handle);
    for (const auto& instance : rt().instances) {
        if (instance.get() == candidate) {
            return candidate;
        }
    }
    return nullptr;
}

Session* as_session(XrSession handle) {
    auto* candidate = reinterpret_cast<Session*>(handle);
    for (const auto& instance : rt().instances) {
        for (const auto& session : instance->sessions) {
            if (session.get() == candidate) {
                return candidate;
            }
        }
    }
    return nullptr;
}

ActionSet* as_action_set(XrActionSet handle) {
    auto* candidate = reinterpret_cast<ActionSet*>(handle);
    for (const auto& instance : rt().instances) {
        for (const auto& set : instance->action_sets) {
            if (set.get() == candidate) {
                return candidate;
            }
        }
    }
    return nullptr;
}

Action* as_action(XrAction handle) {
    auto* candidate = reinterpret_cast<Action*>(handle);
    for (const auto& instance : rt().instances) {
        for (const auto& set : instance->action_sets) {
            for (const auto& action : set->actions) {
                if (action.get() == candidate) {
                    return candidate;
                }
            }
        }
    }
    return nullptr;
}

XrPath intern_path(Instance& instance, const std::string& value) {
    const auto it = instance.path_lookup.find(value);
    if (it != instance.path_lookup.end()) {
        return it->second;
    }
    instance.paths.push_back(value);
    const XrPath path = static_cast<XrPath>(instance.paths.size());
    instance.path_lookup.emplace(value, path);
    return path;
}

const std::string* path_string(const Instance& instance, XrPath path) {
    if (path == XR_NULL_PATH || path > instance.paths.size()) {
        return nullptr;
    }
    return &instance.paths[static_cast<size_t>(path - 1)];
}

// Splits "/user/hand/left/input/thumbstick/y" into hand index, "thumbstick", "y".
struct ParsedBinding {
    int hand = -1;
    std::string identifier;
    std::string component;
};

ParsedBinding parse_binding(const std::string& path) {
    ParsedBinding parsed;
    const std::string left = "/user/hand/left/input/";
    const std::string right = "/user/hand/right/input/";
    std::string rest;
    if (path.compare(0, left.size(), left) == 0) {
        parsed.hand = 0;
        rest = path.substr(left.size());
    } else if (path.compare(0, right.size(), right) == 0) {
        parsed.hand = 1;
        rest = path.substr(right.size());
    } else {
        return parsed;
    }
    const size_t slash = rest.find('/');
    parsed.identifier = rest.substr(0, slash);
    parsed.component = slash == std::string::npos ? std::string{} : rest.substr(slash + 1);
    return parsed;
}

struct SourceValue {
    float x = 0.0F;
    float y = 0.0F;  // scalar sources put their value in both x and y so either action type can read it
    bool pressed = false;
};

bool read_source(const Devices& devices, const ParsedBinding& binding, SourceValue& out) {
    if (binding.hand < 0) {
        return false;
    }
    const Controller& controller = devices.hands[binding.hand];
    if (!controller.connected) {
        return false;
    }
    const auto button = [&](const std::string& key) {
        const auto it = controller.buttons.find(key);
        out.pressed = it != controller.buttons.end() && it->second;
        out.x = out.y = out.pressed ? 1.0F : 0.0F;
        return true;
    };
    const auto axis2 = [&](float x, float y) {
        if (binding.component.empty()) {
            out.x = x;
            out.y = y;
            return true;
        }
        if (binding.component == "x") {
            out.x = out.y = x;
            return true;
        }
        if (binding.component == "y") {
            out.x = out.y = y;
            return true;
        }
        return button(binding.identifier + "/" + binding.component);
    };
    if (binding.identifier == "thumbstick") {
        return axis2(controller.thumbstick_x, controller.thumbstick_y);
    }
    if (binding.identifier == "trackpad") {
        return axis2(controller.trackpad_x, controller.trackpad_y);
    }
    if (binding.identifier == "trigger" && (binding.component.empty() || binding.component == "value")) {
        out.x = out.y = controller.trigger;
        out.pressed = controller.trigger > 0.5F;
        return true;
    }
    if (binding.identifier == "squeeze" && (binding.component.empty() || binding.component == "value")) {
        out.x = out.y = controller.squeeze;
        out.pressed = controller.squeeze > 0.5F;
        return true;
    }
    return button(binding.identifier + "/" + (binding.component.empty() ? std::string("click") : binding.component));
}

const ActiveSet* find_active(const Session& session, const ActionSet* set, int hand, const Instance& instance) {
    for (const ActiveSet& active : session.active) {
        if (active.set != set) {
            continue;
        }
        if (active.subaction == XR_NULL_PATH) {
            return &active;
        }
        const std::string* restricted = path_string(instance, active.subaction);
        if (restricted != nullptr &&
            ((hand == 0 && *restricted == "/user/hand/left") || (hand == 1 && *restricted == "/user/hand/right"))) {
            return &active;
        }
    }
    return nullptr;
}

// True if an active set with a higher priority has any action bound to the same physical input.
bool suppressed_by_priority(const Session& session, const Instance& instance, const Action& action,
                            const ParsedBinding& binding) {
    const auto profile_it = instance.suggested.find(rt().profile);
    if (profile_it == instance.suggested.end()) {
        return false;
    }
    for (const Binding& other : profile_it->second) {
        if (other.action->set == action.set || other.action->set->priority <= action.set->priority) {
            continue;
        }
        const ParsedBinding other_parsed = parse_binding(other.path);
        if (other_parsed.hand != binding.hand || other_parsed.identifier != binding.identifier) {
            continue;
        }
        if (find_active(session, other.action->set, other_parsed.hand, instance) != nullptr) {
            return true;
        }
    }
    return false;
}

struct Evaluated {
    bool active = false;
    float x = 0.0F;
    float y = 0.0F;
    bool pressed = false;
};

Evaluated evaluate(const Session& session, const Action& action, XrPath subaction) {
    Evaluated result;
    const Instance& instance = *session.instance;
    if (!session.latched_focused || !action.set->attached) {
        return result;
    }
    const auto profile_it = instance.suggested.find(rt().profile);
    if (profile_it == instance.suggested.end()) {
        return result;
    }
    int wanted_hand = -1;
    if (subaction != XR_NULL_PATH) {
        const std::string* subaction_string = path_string(instance, subaction);
        if (subaction_string == nullptr) {
            return result;
        }
        if (*subaction_string == "/user/hand/left") {
            wanted_hand = 0;
        } else if (*subaction_string == "/user/hand/right") {
            wanted_hand = 1;
        } else {
            return result;  // head / gamepad / ...: nothing simulated there
        }
    }

    float best_length = -1.0F;
    for (const Binding& binding : profile_it->second) {
        if (binding.action != &action) {
            continue;
        }
        const ParsedBinding parsed = parse_binding(binding.path);
        if (parsed.hand < 0 || (wanted_hand >= 0 && parsed.hand != wanted_hand)) {
            continue;
        }
        if (find_active(session, action.set, parsed.hand, instance) == nullptr) {
            continue;
        }
        if (suppressed_by_priority(session, instance, action, parsed)) {
            continue;
        }
        SourceValue value;
        if (!read_source(session.latched, parsed, value)) {
            continue;
        }
        result.active = true;
        result.pressed = result.pressed || value.pressed;
        const float length = action.type == XR_ACTION_TYPE_VECTOR2F_INPUT ? std::sqrt(value.x * value.x + value.y * value.y)
                                                                         : std::fabs(value.y);
        if (length > best_length) {
            best_length = length;
            result.x = value.x;
            result.y = value.y;
        }
    }
    return result;
}

XrResult validate_read(XrSession session_handle, const XrActionStateGetInfo* info, XrActionType expected, Session*& session,
                       Action*& action) {
    session = as_session(session_handle);
    if (session == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (info == nullptr || info->type != XR_TYPE_ACTION_STATE_GET_INFO) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    action = as_action(info->action);
    if (action == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (action->type != expected) {
        return XR_ERROR_ACTION_TYPE_MISMATCH;
    }
    if (!action->set->attached) {
        return XR_ERROR_ACTIONSET_NOT_ATTACHED;
    }
    if (info->subactionPath != XR_NULL_PATH &&
        std::find(action->subaction_paths.begin(), action->subaction_paths.end(), info->subactionPath) ==
            action->subaction_paths.end()) {
        return XR_ERROR_PATH_UNSUPPORTED;
    }
    return XR_SUCCESS;
}

ReadHistory& update_history(Session& session, Action& action, XrPath subaction, const Evaluated& value) {
    ReadHistory& history = session.history[ReadKey{&action, subaction}];
    if (history.generation != session.generation) {
        const bool first = history.generation == 0;
        history.changed = !first && value.active && (history.x != value.x || history.y != value.y);
        if (history.changed || (first && value.active && (value.x != 0.0F || value.y != 0.0F))) {
            history.last_change_time = session.sync_time;
        }
        history.generation = session.generation;
        history.x = value.x;
        history.y = value.y;
        history.active = value.active;
    }
    return history;
}

// ---------------------------------------------------------------------------------------------
// OpenXR entry points
// ---------------------------------------------------------------------------------------------

XrResult XRAPI_CALL mock_xrEnumerateInstanceExtensionProperties(const char*, uint32_t capacity, uint32_t* count,
                                                                XrExtensionProperties* properties) {
    if (count == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    *count = 1;
    if (capacity == 0) {
        return XR_SUCCESS;
    }
    if (properties == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    properties[0].type = XR_TYPE_EXTENSION_PROPERTIES;
    strncpy_s(properties[0].extensionName, "XR_MND_headless", _TRUNCATE);
    properties[0].extensionVersion = 2;
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrCreateInstance(const XrInstanceCreateInfo* info, XrInstance* instance) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrCreateInstance");
    if (info == nullptr || instance == nullptr || info->type != XR_TYPE_INSTANCE_CREATE_INFO) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    if (XR_VERSION_MAJOR(info->applicationInfo.apiVersion) != 1) {
        return XR_ERROR_API_VERSION_UNSUPPORTED;
    }
    rt().instances.push_back(std::make_unique<Instance>());
    *instance = reinterpret_cast<XrInstance>(rt().instances.back().get());
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrDestroyInstance(XrInstance handle) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrDestroyInstance");
    Instance* instance = as_instance(handle);
    if (instance == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    auto& all = rt().instances;
    all.erase(std::remove_if(all.begin(), all.end(), [&](const auto& item) { return item.get() == instance; }), all.end());
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrGetInstanceProperties(XrInstance handle, XrInstanceProperties* properties) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    if (as_instance(handle) == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (properties == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    properties->runtimeVersion = XR_MAKE_VERSION(1, 0, 0);
    strncpy_s(properties->runtimeName, "VRTreadmill Mock Runtime", _TRUNCATE);
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrPollEvent(XrInstance handle, XrEventDataBuffer*) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    return as_instance(handle) == nullptr ? XR_ERROR_HANDLE_INVALID : XR_EVENT_UNAVAILABLE;
}

XrResult XRAPI_CALL mock_xrGetSystem(XrInstance handle, const XrSystemGetInfo* info, XrSystemId* system_id) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    if (as_instance(handle) == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (info == nullptr || system_id == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    *system_id = 1;
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrCreateSession(XrInstance handle, const XrSessionCreateInfo* info, XrSession* session) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrCreateSession");
    Instance* instance = as_instance(handle);
    if (instance == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (info == nullptr || session == nullptr || info->type != XR_TYPE_SESSION_CREATE_INFO) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    instance->sessions.push_back(std::make_unique<Session>());
    instance->sessions.back()->instance = instance;
    *session = reinterpret_cast<XrSession>(instance->sessions.back().get());
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrDestroySession(XrSession handle) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrDestroySession");
    Session* session = as_session(handle);
    if (session == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    for (ActionSet* set : session->attached) {
        set->attached = false;
    }
    auto& sessions = session->instance->sessions;
    sessions.erase(std::remove_if(sessions.begin(), sessions.end(), [&](const auto& item) { return item.get() == session; }),
                   sessions.end());
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrStringToPath(XrInstance handle, const char* value, XrPath* path) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    Instance* instance = as_instance(handle);
    if (instance == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (value == nullptr || path == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    if (value[0] != '/') {
        return XR_ERROR_PATH_FORMAT_INVALID;
    }
    *path = intern_path(*instance, value);
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrPathToString(XrInstance handle, XrPath path, uint32_t capacity, uint32_t* count, char* buffer) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    Instance* instance = as_instance(handle);
    if (instance == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (count == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    const std::string* value = path_string(*instance, path);
    if (value == nullptr) {
        return XR_ERROR_PATH_INVALID;
    }
    *count = static_cast<uint32_t>(value->size() + 1);
    if (capacity == 0) {
        return XR_SUCCESS;
    }
    if (capacity < *count || buffer == nullptr) {
        return XR_ERROR_SIZE_INSUFFICIENT;
    }
    std::memcpy(buffer, value->c_str(), value->size() + 1);
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrCreateActionSet(XrInstance handle, const XrActionSetCreateInfo* info, XrActionSet* action_set) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrCreateActionSet");
    Instance* instance = as_instance(handle);
    if (instance == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (info == nullptr || action_set == nullptr || info->type != XR_TYPE_ACTION_SET_CREATE_INFO) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    auto set = std::make_unique<ActionSet>();
    set->instance = instance;
    set->name = info->actionSetName;
    set->priority = info->priority;
    *action_set = reinterpret_cast<XrActionSet>(set.get());
    instance->action_sets.push_back(std::move(set));
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrDestroyActionSet(XrActionSet handle) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrDestroyActionSet");
    ActionSet* set = as_action_set(handle);
    if (set == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    Instance* instance = set->instance;
    for (auto& profile : instance->suggested) {
        auto& bindings = profile.second;
        bindings.erase(std::remove_if(bindings.begin(), bindings.end(), [&](const Binding& b) { return b.action->set == set; }),
                       bindings.end());
    }
    for (auto& session : instance->sessions) {
        auto& attached = session->attached;
        attached.erase(std::remove(attached.begin(), attached.end(), set), attached.end());
        auto& active = session->active;
        active.erase(std::remove_if(active.begin(), active.end(), [&](const ActiveSet& a) { return a.set == set; }), active.end());
        for (auto it = session->history.begin(); it != session->history.end();) {
            it = it->first.action->set == set ? session->history.erase(it) : std::next(it);
        }
    }
    auto& sets = instance->action_sets;
    sets.erase(std::remove_if(sets.begin(), sets.end(), [&](const auto& item) { return item.get() == set; }), sets.end());
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrCreateAction(XrActionSet handle, const XrActionCreateInfo* info, XrAction* action) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrCreateAction");
    ActionSet* set = as_action_set(handle);
    if (set == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (info == nullptr || action == nullptr || info->type != XR_TYPE_ACTION_CREATE_INFO) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    if (set->attached) {
        return XR_ERROR_ACTIONSETS_ALREADY_ATTACHED;
    }
    auto created = std::make_unique<Action>();
    created->set = set;
    created->name = info->actionName;
    created->type = info->actionType;
    for (uint32_t index = 0; index < info->countSubactionPaths; ++index) {
        created->subaction_paths.push_back(info->subactionPaths[index]);
    }
    *action = reinterpret_cast<XrAction>(created.get());
    set->actions.push_back(std::move(created));
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrDestroyAction(XrAction handle) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrDestroyAction");
    Action* action = as_action(handle);
    if (action == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    Instance* instance = action->set->instance;
    for (auto& profile : instance->suggested) {
        auto& bindings = profile.second;
        bindings.erase(std::remove_if(bindings.begin(), bindings.end(), [&](const Binding& b) { return b.action == action; }),
                       bindings.end());
    }
    for (auto& session : instance->sessions) {
        for (auto it = session->history.begin(); it != session->history.end();) {
            it = it->first.action == action ? session->history.erase(it) : std::next(it);
        }
    }
    auto& actions = action->set->actions;
    actions.erase(std::remove_if(actions.begin(), actions.end(), [&](const auto& item) { return item.get() == action; }),
                  actions.end());
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrSuggestInteractionProfileBindings(XrInstance handle,
                                                             const XrInteractionProfileSuggestedBinding* suggested) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrSuggestInteractionProfileBindings");
    Instance* instance = as_instance(handle);
    if (instance == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (suggested == nullptr || suggested->type != XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    const std::string* profile = path_string(*instance, suggested->interactionProfile);
    if (profile == nullptr) {
        return XR_ERROR_PATH_INVALID;
    }
    std::vector<Binding> bindings;
    for (uint32_t index = 0; index < suggested->countSuggestedBindings; ++index) {
        Action* action = as_action(suggested->suggestedBindings[index].action);
        const std::string* path = path_string(*instance, suggested->suggestedBindings[index].binding);
        if (action == nullptr) {
            return XR_ERROR_HANDLE_INVALID;
        }
        if (path == nullptr) {
            return XR_ERROR_PATH_INVALID;
        }
        if (action->set->attached) {
            return XR_ERROR_ACTIONSETS_ALREADY_ATTACHED;
        }
        bindings.push_back(Binding{action, *path});
    }
    instance->suggested[*profile] = std::move(bindings);  // a second call for the same profile replaces the first
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrAttachSessionActionSets(XrSession handle, const XrSessionActionSetsAttachInfo* info) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrAttachSessionActionSets");
    Session* session = as_session(handle);
    if (session == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (info == nullptr || info->type != XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO || info->countActionSets == 0) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    if (!session->attached.empty()) {
        return XR_ERROR_ACTIONSETS_ALREADY_ATTACHED;
    }
    for (uint32_t index = 0; index < info->countActionSets; ++index) {
        ActionSet* set = as_action_set(info->actionSets[index]);
        if (set == nullptr) {
            return XR_ERROR_HANDLE_INVALID;
        }
        set->attached = true;
        session->attached.push_back(set);
    }
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrSyncActions(XrSession handle, const XrActionsSyncInfo* info) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrSyncActions");
    Session* session = as_session(handle);
    if (session == nullptr) {
        return XR_ERROR_HANDLE_INVALID;
    }
    if (info == nullptr || info->type != XR_TYPE_ACTIONS_SYNC_INFO) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    std::vector<ActiveSet> active;
    for (uint32_t index = 0; index < info->countActiveActionSets; ++index) {
        ActionSet* set = as_action_set(info->activeActionSets[index].actionSet);
        if (set == nullptr) {
            return XR_ERROR_HANDLE_INVALID;
        }
        if (std::find(session->attached.begin(), session->attached.end(), set) == session->attached.end()) {
            return XR_ERROR_ACTIONSET_NOT_ATTACHED;
        }
        active.push_back(ActiveSet{set, info->activeActionSets[index].subactionPath});
    }
    ++session->generation;
    rt().now += 11'111'111;  // ~90 Hz
    session->sync_time = rt().now;
    session->latched_focused = rt().focused;
    session->latched = rt().devices;
    session->active = rt().focused ? std::move(active) : std::vector<ActiveSet>{};
    return rt().focused ? XR_SUCCESS : XR_SESSION_NOT_FOCUSED;
}

XrResult XRAPI_CALL mock_xrGetActionStateBoolean(XrSession handle, const XrActionStateGetInfo* info, XrActionStateBoolean* state) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrGetActionStateBoolean");
    Session* session = nullptr;
    Action* action = nullptr;
    const XrResult check = validate_read(handle, info, XR_ACTION_TYPE_BOOLEAN_INPUT, session, action);
    if (XR_FAILED(check)) {
        return check;
    }
    if (state == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    Evaluated value = evaluate(*session, *action, info->subactionPath);
    value.x = value.y = value.pressed ? 1.0F : 0.0F;
    const ReadHistory& history = update_history(*session, *action, info->subactionPath, value);
    state->currentState = value.active && value.pressed ? XR_TRUE : XR_FALSE;
    state->isActive = value.active ? XR_TRUE : XR_FALSE;
    state->changedSinceLastSync = value.active && history.changed ? XR_TRUE : XR_FALSE;
    state->lastChangeTime = value.active ? history.last_change_time : 0;
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrGetActionStateFloat(XrSession handle, const XrActionStateGetInfo* info, XrActionStateFloat* state) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrGetActionStateFloat");
    Session* session = nullptr;
    Action* action = nullptr;
    const XrResult check = validate_read(handle, info, XR_ACTION_TYPE_FLOAT_INPUT, session, action);
    if (XR_FAILED(check)) {
        return check;
    }
    if (state == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    Evaluated value = evaluate(*session, *action, info->subactionPath);
    value.x = value.y;
    const ReadHistory& history = update_history(*session, *action, info->subactionPath, value);
    state->currentState = value.active ? value.y : 0.0F;
    state->isActive = value.active ? XR_TRUE : XR_FALSE;
    state->changedSinceLastSync = value.active && history.changed ? XR_TRUE : XR_FALSE;
    state->lastChangeTime = value.active ? history.last_change_time : 0;
    return XR_SUCCESS;
}

XrResult XRAPI_CALL mock_xrGetActionStateVector2f(XrSession handle, const XrActionStateGetInfo* info,
                                                  XrActionStateVector2f* state) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    count_call("xrGetActionStateVector2f");
    Session* session = nullptr;
    Action* action = nullptr;
    const XrResult check = validate_read(handle, info, XR_ACTION_TYPE_VECTOR2F_INPUT, session, action);
    if (XR_FAILED(check)) {
        return check;
    }
    if (state == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    const Evaluated value = evaluate(*session, *action, info->subactionPath);
    const ReadHistory& history = update_history(*session, *action, info->subactionPath, value);
    state->currentState.x = value.active ? value.x : 0.0F;
    state->currentState.y = value.active ? value.y : 0.0F;
    state->isActive = value.active ? XR_TRUE : XR_FALSE;
    state->changedSinceLastSync = value.active && history.changed ? XR_TRUE : XR_FALSE;
    state->lastChangeTime = value.active ? history.last_change_time : 0;
    return XR_SUCCESS;
}

struct Entry {
    const char* name;
    PFN_xrVoidFunction function;
};

XrResult XRAPI_CALL mock_xrGetInstanceProcAddr(XrInstance instance, const char* name, PFN_xrVoidFunction* function);

#define MOCK_ENTRY(fn) {#fn, reinterpret_cast<PFN_xrVoidFunction>(mock_##fn)}
const Entry kEntries[] = {
    MOCK_ENTRY(xrGetInstanceProcAddr),
    MOCK_ENTRY(xrEnumerateInstanceExtensionProperties),
    MOCK_ENTRY(xrCreateInstance),
    MOCK_ENTRY(xrDestroyInstance),
    MOCK_ENTRY(xrGetInstanceProperties),
    MOCK_ENTRY(xrPollEvent),
    MOCK_ENTRY(xrGetSystem),
    MOCK_ENTRY(xrCreateSession),
    MOCK_ENTRY(xrDestroySession),
    MOCK_ENTRY(xrStringToPath),
    MOCK_ENTRY(xrPathToString),
    MOCK_ENTRY(xrCreateActionSet),
    MOCK_ENTRY(xrDestroyActionSet),
    MOCK_ENTRY(xrCreateAction),
    MOCK_ENTRY(xrDestroyAction),
    MOCK_ENTRY(xrSuggestInteractionProfileBindings),
    MOCK_ENTRY(xrAttachSessionActionSets),
    MOCK_ENTRY(xrSyncActions),
    MOCK_ENTRY(xrGetActionStateBoolean),
    MOCK_ENTRY(xrGetActionStateFloat),
    MOCK_ENTRY(xrGetActionStateVector2f),
};
#undef MOCK_ENTRY

XrResult XRAPI_CALL mock_xrGetInstanceProcAddr(XrInstance, const char* name, PFN_xrVoidFunction* function) {
    if (name == nullptr || function == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    for (const Entry& entry : kEntries) {
        if (std::strcmp(entry.name, name) == 0) {
            *function = entry.function;
            return XR_SUCCESS;
        }
    }
    *function = nullptr;
    return XR_ERROR_FUNCTION_UNSUPPORTED;
}

int clamp_hand(int hand) {
    return hand == 1 ? 1 : 0;
}

}  // namespace

MOCKRT_API XrResult XRAPI_CALL xrNegotiateLoaderRuntimeInterface(const XrNegotiateLoaderInfo* loader_info,
                                                                 XrNegotiateRuntimeRequest* request) {
    if (loader_info == nullptr || request == nullptr || loader_info->structType != XR_LOADER_INTERFACE_STRUCT_LOADER_INFO ||
        request->structType != XR_LOADER_INTERFACE_STRUCT_RUNTIME_REQUEST) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    request->runtimeInterfaceVersion = XR_CURRENT_LOADER_RUNTIME_VERSION;
    request->runtimeApiVersion = XR_CURRENT_API_VERSION;
    request->getInstanceProcAddr = mock_xrGetInstanceProcAddr;
    return XR_SUCCESS;
}

// ---------------------------------------------------------------------------------------------
// Test control surface
// ---------------------------------------------------------------------------------------------

MOCKRT_API void mockrt_reset() {
    std::lock_guard<std::mutex> lock(rt().mutex);
    rt().devices = Devices{};
    rt().focused = true;
    rt().profile = "/interaction_profiles/oculus/touch_controller";
    rt().call_counts.clear();
}

MOCKRT_API void mockrt_set_connected(int hand, int connected) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    rt().devices.hands[clamp_hand(hand)].connected = connected != 0;
}

MOCKRT_API void mockrt_set_thumbstick(int hand, float x, float y) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    rt().devices.hands[clamp_hand(hand)].thumbstick_x = x;
    rt().devices.hands[clamp_hand(hand)].thumbstick_y = y;
}

MOCKRT_API void mockrt_set_trackpad(int hand, float x, float y) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    rt().devices.hands[clamp_hand(hand)].trackpad_x = x;
    rt().devices.hands[clamp_hand(hand)].trackpad_y = y;
}

MOCKRT_API void mockrt_set_trigger(int hand, float value) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    rt().devices.hands[clamp_hand(hand)].trigger = value;
}

MOCKRT_API void mockrt_set_button(int hand, const char* identifier_and_component, int pressed) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    rt().devices.hands[clamp_hand(hand)].buttons[identifier_and_component] = pressed != 0;
}

MOCKRT_API void mockrt_set_focused(int focused) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    rt().focused = focused != 0;
}

MOCKRT_API void mockrt_set_profile(const char* profile) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    rt().profile = profile;
}

MOCKRT_API uint64_t mockrt_call_count(const char* function_name) {
    std::lock_guard<std::mutex> lock(rt().mutex);
    const auto it = rt().call_counts.find(function_name);
    return it == rt().call_counts.end() ? 0 : it->second;
}

MOCKRT_API uint32_t mockrt_live_instances() {
    std::lock_guard<std::mutex> lock(rt().mutex);
    return static_cast<uint32_t>(rt().instances.size());
}
