// VR Treadmill OpenXR API layer.
//
// Loaded by the OpenXR loader into every OpenXR application. While the VR Treadmill
// app is capturing and the treadmill is moving, the layer substitutes the Y axis of
// the game's left-thumbstick locomotion action with the treadmill value.
//
// Rules this file is built around (see docs/openxr-layer.md):
//   1. Never break the host. Every entry point contains exceptions, and every failure
//      path degrades to "pass the call through untouched".
//   2. Never load-fail. A layer DLL that fails to load can make xrCreateInstance fail
//      for the whole game, so: static CRT, no imports beyond kernel32, and negotiation
//      accepts every loader that speaks layer interface version 1.
//   3. Never leave the player walking. Shared memory older than 250 ms is ignored.
//   4. Follow OpenXR semantics. Injected state only changes at xrSyncActions, repeated
//      reads inside one sync agree with each other, and changedSinceLastSync is honest.

#include "openxr_minimal.h"
#include "vrtread_policy.h"
#include "vrtread_shared_memory.h"

#include <windows.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <cwctype>
#include <iterator>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#ifndef VRTREAD_LAYER_VERSION
#define VRTREAD_LAYER_VERSION "0.0.0-dev"
#endif

namespace {

namespace policy = vrtread::policy;

constexpr char kLayerName[] = "XR_APILAYER_VRTREAD_treadmill";
constexpr uint64_t kSnapshotMaxAgeMs = 1000;  // stop injecting if the game stops calling xrSyncActions
constexpr LONGLONG kLogMaxBytes = 256 * 1024;

// ---------------------------------------------------------------------------------------------
// Environment / process helpers
// ---------------------------------------------------------------------------------------------

std::string env_string(const char* name) {
    char buffer[512]{};
    const DWORD length = GetEnvironmentVariableA(name, buffer, static_cast<DWORD>(sizeof(buffer)));
    if (length == 0 || length >= sizeof(buffer)) {
        return {};
    }
    return std::string(buffer, length);
}

bool env_flag(const char* name, bool default_value) {
    const std::string value = policy::lower_copy(env_string(name));
    if (value.empty()) {
        return default_value;
    }
    return value != "0" && value != "false" && value != "no" && value != "off";
}

std::wstring widen(const std::string& value) {
    if (value.empty()) {
        return {};
    }
    const int length = MultiByteToWideChar(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), nullptr, 0);
    if (length <= 0) {
        return {};
    }
    std::wstring out(static_cast<size_t>(length), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), out.data(), length);
    return out;
}

std::string narrow(const std::wstring& value) {
    if (value.empty()) {
        return {};
    }
    const int length =
        WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (length <= 0) {
        return {};
    }
    std::string out(static_cast<size_t>(length), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()), out.data(), length, nullptr, nullptr);
    return out;
}

std::wstring process_path() {
    wchar_t buffer[2048]{};
    const DWORD length = GetModuleFileNameW(nullptr, buffer, static_cast<DWORD>(std::size(buffer)));
    if (length == 0 || length >= std::size(buffer)) {
        return {};
    }
    return std::wstring(buffer, length);
}

std::wstring basename_of(const std::wstring& path) {
    const size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? path : path.substr(slash + 1);
}

// VR runtime / dashboard helper processes can be OpenXR clients too; they never need locomotion.
bool process_is_runtime_helper(const std::string& lowercase_basename) {
    return policy::contains_any(
        lowercase_basename,
        {"vrserver", "vrcompositor", "vrdashboard", "vrmonitor", "vrstartup", "vrwebhelper", "steamtours",
         "oculusclient", "oculusdash", "ovrserver", "ovrredir", "mixedrealityportal", "wmrhost",
         "virtualdesktop.streamer", "openxr_runtime"});
}

// ---------------------------------------------------------------------------------------------
// Diagnostics log: %LOCALAPPDATA%\VRTreadmill\logs\openxr-layer-<exe>.log
// Written only on rare events (instance/action creation, first decision per action), never per frame.
// ---------------------------------------------------------------------------------------------

class Log {
public:
    void configure() noexcept {
        try {
            std::lock_guard<std::mutex> lock(mutex_);
            if (configured_) {
                return;
            }
            configured_ = true;
            debug_output_ = env_flag("VRTREAD_OPENXR_DEBUG", false);
            file_enabled_ = env_flag("VRTREAD_OPENXR_LOG", true);
            if (!file_enabled_) {
                return;
            }

            std::wstring directory = widen(env_string("VRTREAD_OPENXR_LOG_DIR"));
            if (directory.empty()) {
                const std::wstring local_app_data = widen(env_string("LOCALAPPDATA"));
                if (local_app_data.empty()) {
                    file_enabled_ = false;
                    return;
                }
                const std::wstring app_dir = local_app_data + L"\\VRTreadmill";
                CreateDirectoryW(app_dir.c_str(), nullptr);
                directory = app_dir + L"\\logs";
            }
            CreateDirectoryW(directory.c_str(), nullptr);

            std::wstring exe = basename_of(process_path());
            for (wchar_t& ch : exe) {
                if (!(std::iswalnum(static_cast<wint_t>(ch)) || ch == L'.' || ch == L'-' || ch == L'_')) {
                    ch = L'_';
                }
            }
            if (exe.empty()) {
                exe = L"unknown";
            }
            path_ = directory + L"\\openxr-layer-" + exe + L".log";
        } catch (...) {
            file_enabled_ = false;
        }
    }

    void write(const char* format, ...) noexcept {
        try {
            char message[1024]{};
            va_list args;
            va_start(args, format);
            std::vsnprintf(message, sizeof(message), format, args);
            va_end(args);

            std::lock_guard<std::mutex> lock(mutex_);
            if (debug_output_) {
                OutputDebugStringA("[VRTreadmill OpenXR] ");
                OutputDebugStringA(message);
                OutputDebugStringA("\n");
            }
            if (!file_enabled_ || path_.empty()) {
                return;
            }

            SYSTEMTIME now{};
            GetLocalTime(&now);
            char line[1200]{};
            int length = std::snprintf(line, sizeof(line), "%04u-%02u-%02u %02u:%02u:%02u.%03u [%lu] %s\r\n", now.wYear,
                                       now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond, now.wMilliseconds,
                                       GetCurrentProcessId(), message);
            if (length <= 0) {
                return;
            }
            if (length >= static_cast<int>(sizeof(line))) {
                length = static_cast<int>(sizeof(line)) - 1;
            }
            append(line, static_cast<DWORD>(length));
        } catch (...) {
        }
    }

private:
    void append(const char* data, DWORD size) noexcept {
        constexpr DWORD kShare = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
        HANDLE file = CreateFileW(path_.c_str(), FILE_APPEND_DATA, kShare, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) {
            return;
        }
        LARGE_INTEGER file_size{};
        if (GetFileSizeEx(file, &file_size) && file_size.QuadPart > kLogMaxBytes) {
            CloseHandle(file);
            file = CreateFileW(path_.c_str(), GENERIC_WRITE, kShare, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
            if (file == INVALID_HANDLE_VALUE) {
                return;
            }
        }
        DWORD written = 0;
        WriteFile(file, data, size, &written, nullptr);
        CloseHandle(file);
    }

    std::mutex mutex_;
    std::wstring path_;
    bool configured_ = false;
    bool file_enabled_ = false;
    bool debug_output_ = false;
};

// ---------------------------------------------------------------------------------------------
// Shared memory reader (seqlock). Only ever called with State::mutex held.
// ---------------------------------------------------------------------------------------------

struct Snapshot {
    bool present = false;  // a live VR Treadmill publisher exists (valid, active, fresh block)
    bool engaged = false;  // present and the treadmill is actually moving
    float x = 0.0F;
    float y = 0.0F;
    uint32_t filter_mode = vrtread::kFilterBalanced;
    uint32_t combine_mode = vrtread::kCombineMaxMagnitude;
    uint32_t options = 0;
    uint64_t taken_ms = 0;
};

class SharedStateReader {
public:
    SharedStateReader() = default;
    SharedStateReader(const SharedStateReader&) = delete;
    SharedStateReader& operator=(const SharedStateReader&) = delete;

    ~SharedStateReader() {
        if (view_ != nullptr) {
            UnmapViewOfFile(view_);
        }
        if (handle_ != nullptr) {
            CloseHandle(handle_);
        }
    }

    Snapshot read(uint64_t now_ms) noexcept {
        Snapshot result;
        result.taken_ms = now_ms;
        if (!ensure_mapping(now_ms)) {
            return result;
        }

        vrtread::SharedState block{};
        bool consistent = false;
        // Steady state: a few quick tries, then fall back to the previous block. The very first read has no
        // previous block, so without patience a game session that starts while the app is mid-update would
        // not see the treadmill on its first frame. Bounded spin (~100 us worst case), first read only.
        const int max_attempts = have_last_good_ ? 4 : 4000;
        for (int attempt = 0; attempt < max_attempts && !consistent; ++attempt) {
            // `volatile` forces two real loads of seq around the copy; without it the optimizer may fold them.
            const volatile uint64_t* seq = &static_cast<const vrtread::SharedState*>(view_)->seq;
            const uint64_t before = *seq;
            if ((before & 1U) != 0U) {
                YieldProcessor();
                continue;
            }
            std::memcpy(&block, view_, sizeof(block));
            const uint64_t after = *seq;
            consistent = before == after;
        }
        if (consistent) {
            last_good_ = block;
            have_last_good_ = true;
        } else if (have_last_good_) {
            // Lost the race with the writer several times in a row. Re-use the previous block instead of
            // dropping to "no treadmill" for a frame; its timestamp still has to pass the staleness check.
            block = last_good_;
        } else {
            return result;
        }

        if (block.magic != vrtread::kMagic || block.version != vrtread::kVersion ||
            block.struct_size != sizeof(vrtread::SharedState)) {
            return result;
        }
        if ((block.flags & vrtread::kFlagActive) == 0U) {
            return result;
        }
        if (block.timestamp_ms == 0 || now_ms < block.timestamp_ms || now_ms - block.timestamp_ms > vrtread::kStaleMs) {
            return result;
        }
        if (!policy::is_usable(block.x) || !policy::is_usable(block.y)) {
            return result;
        }

        result.x = policy::clamp_unit(block.x);
        result.y = policy::clamp_unit(block.y);
        result.filter_mode = block.filter_mode <= vrtread::kFilterCompatibility ? block.filter_mode : vrtread::kFilterBalanced;
        result.combine_mode = block.combine_mode <= vrtread::kCombineAdd ? block.combine_mode : vrtread::kCombineMaxMagnitude;
        result.options = block.options;
        result.present = true;
        result.engaged = std::fabs(result.y) >= vrtread::kIdleThreshold;
        return result;
    }

private:
    bool ensure_mapping(uint64_t now_ms) noexcept {
        if (view_ != nullptr) {
            return true;
        }
        if (now_ms < next_retry_ms_) {
            return false;
        }
        next_retry_ms_ = now_ms + 1000;
        try {
            if (name_.empty()) {
                name_ = widen(env_string(vrtread::kMappingNameEnvVar));
                if (name_.empty()) {
                    name_ = vrtread::kMappingName;
                }
            }
        } catch (...) {
            return false;
        }
        const HANDLE handle = OpenFileMappingW(FILE_MAP_READ, FALSE, name_.c_str());
        if (handle == nullptr) {
            return false;
        }
        void* view = MapViewOfFile(handle, FILE_MAP_READ, 0, 0, sizeof(vrtread::SharedState));
        if (view == nullptr) {
            CloseHandle(handle);
            return false;
        }
        handle_ = handle;
        view_ = view;
        return true;
    }

    HANDLE handle_ = nullptr;
    void* view_ = nullptr;
    uint64_t next_retry_ms_ = 0;
    std::wstring name_;
    vrtread::SharedState last_good_{};
    bool have_last_good_ = false;
};

// ---------------------------------------------------------------------------------------------
// Layer state
// ---------------------------------------------------------------------------------------------

struct NextTable {
    PFN_xrGetInstanceProcAddr get_instance_proc_addr = nullptr;
    PFN_xrDestroyInstance destroy_instance = nullptr;
    PFN_xrCreateSession create_session = nullptr;
    PFN_xrDestroySession destroy_session = nullptr;
    PFN_xrStringToPath string_to_path = nullptr;
    PFN_xrPathToString path_to_string = nullptr;
    PFN_xrCreateActionSet create_action_set = nullptr;
    PFN_xrDestroyActionSet destroy_action_set = nullptr;
    PFN_xrCreateAction create_action = nullptr;
    PFN_xrDestroyAction destroy_action = nullptr;
    PFN_xrSuggestInteractionProfileBindings suggest_bindings = nullptr;
    PFN_xrSyncActions sync_actions = nullptr;
    PFN_xrGetActionStateBoolean get_state_boolean = nullptr;
    PFN_xrGetActionStateFloat get_state_float = nullptr;
    PFN_xrGetActionStateVector2f get_state_vector2f = nullptr;

    bool complete() const {
        return destroy_instance && create_session && destroy_session && string_to_path && path_to_string &&
               create_action_set && destroy_action_set && create_action && destroy_action && suggest_bindings &&
               sync_actions && get_state_boolean && get_state_float && get_state_vector2f;
    }
};

struct InstanceRecord {
    XrInstance handle = nullptr;
    NextTable next;
    bool passthrough = false;  // hooks disabled for this instance: every call is forwarded untouched
    XrPath left_hand = XR_NULL_PATH;
    XrPath right_hand = XR_NULL_PATH;
    std::unordered_map<XrPath, std::string> path_cache;
};

struct ActionRecord {
    std::shared_ptr<InstanceRecord> instance;
    XrActionSet action_set = nullptr;
    policy::ActionTraits traits;
    std::string name;
    uint16_t logged_decisions = 0;  // bit per (filter mode, hand), so each decision is logged once
};

struct QueryKey {
    XrAction action;
    XrPath subaction;
    bool operator==(const QueryKey& other) const { return action == other.action && subaction == other.subaction; }
};

struct QueryKeyHash {
    size_t operator()(const QueryKey& key) const {
        const size_t a = std::hash<uintptr_t>{}(reinterpret_cast<uintptr_t>(key.action));
        const size_t b = std::hash<uint64_t>{}(key.subaction);
        return a ^ (b + static_cast<size_t>(0x9e3779b97f4a7c15ULL) + (a << 6U) + (a >> 2U));
    }
};

struct InjectedEntry {
    uint64_t generation = 0;  // sync generation this entry was last updated in; 0 = never
    float y = 0.0F;           // Y value the game last saw for this query
    bool changed = false;     // changedSinceLastSync answer for `generation`
    bool released = false;    // handed back to the physical stick in `generation`
};

struct SessionRecord {
    std::shared_ptr<InstanceRecord> instance;
    uint64_t generation = 0;  // bumped on every successful xrSyncActions; first sync is generation 1
    Snapshot treadmill;
    std::vector<XrActionSet> active_sets;
    bool saw_active_previous = false;
    bool saw_active_current = false;
    XrTime latest_time = 0;
    std::unordered_map<QueryKey, InjectedEntry, QueryKeyHash> injected;
    uint64_t driven_queries = 0;
    bool announced_engaged = false;
};

struct ActionSetRecord {
    std::shared_ptr<InstanceRecord> instance;
    uint32_t priority = 0;
};

struct State {
    std::mutex mutex;
    std::unordered_map<XrInstance, std::shared_ptr<InstanceRecord>> instances;
    std::unordered_map<XrSession, SessionRecord> sessions;
    std::unordered_map<XrActionSet, ActionSetRecord> action_sets;
    std::unordered_map<XrAction, ActionRecord> actions;
    SharedStateReader reader;
    Log log;
};

// Never destroyed at process exit: games call into OpenXR during teardown, after the static destructors
// of this DLL would already have run. It is only freed when the loader unloads the DLL (see DllMain).
State* g_state = nullptr;
std::once_flag g_state_once;

State& state() {
    std::call_once(g_state_once, [] { g_state = new State(); });
    return *g_state;
}

// A handle we have no record of must not make the game's call fail. With a single hooked instance
// (every real application) the owner is unambiguous, so adopt the handle.
std::shared_ptr<InstanceRecord> sole_hooked_instance_locked(State& s) {
    std::shared_ptr<InstanceRecord> found;
    for (const auto& entry : s.instances) {
        if (entry.second->passthrough) {
            continue;
        }
        if (found) {
            return nullptr;
        }
        found = entry.second;
    }
    return found;
}

std::shared_ptr<InstanceRecord> find_instance(XrInstance handle) {
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    const auto it = s.instances.find(handle);
    return it == s.instances.end() ? nullptr : it->second;
}

std::shared_ptr<InstanceRecord> find_instance_for_session(XrSession session) {
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    const auto it = s.sessions.find(session);
    if (it != s.sessions.end()) {
        return it->second.instance;
    }
    std::shared_ptr<InstanceRecord> owner = sole_hooked_instance_locked(s);
    if (owner && session != nullptr) {
        s.sessions[session].instance = owner;
    }
    return owner;
}

std::shared_ptr<InstanceRecord> find_instance_for_action_set(XrActionSet action_set) {
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    const auto it = s.action_sets.find(action_set);
    if (it != s.action_sets.end()) {
        return it->second.instance;
    }
    std::shared_ptr<InstanceRecord> owner = sole_hooked_instance_locked(s);
    if (owner && action_set != nullptr) {
        s.action_sets[action_set].instance = owner;
    }
    return owner;
}

std::shared_ptr<InstanceRecord> find_instance_for_action(XrAction action) {
    State& s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    const auto it = s.actions.find(action);
    if (it != s.actions.end()) {
        return it->second.instance;
    }
    return sole_hooked_instance_locked(s);
}

// Resolves a path through the next layer/runtime. Must be called without State::mutex held.
std::string resolve_path(const std::shared_ptr<InstanceRecord>& instance, XrPath path) {
    if (path == XR_NULL_PATH) {
        return {};
    }
    {
        std::lock_guard<std::mutex> lock(state().mutex);
        const auto it = instance->path_cache.find(path);
        if (it != instance->path_cache.end()) {
            return it->second;
        }
    }
    uint32_t count = 0;
    if (XR_FAILED(instance->next.path_to_string(instance->handle, path, 0, &count, nullptr)) || count == 0 || count > 4096) {
        return {};
    }
    std::vector<char> buffer(static_cast<size_t>(count) + 1U, '\0');
    if (XR_FAILED(instance->next.path_to_string(instance->handle, path, count, &count, buffer.data()))) {
        return {};
    }
    std::string value(buffer.data());
    std::lock_guard<std::mutex> lock(state().mutex);
    instance->path_cache.emplace(path, value);
    return value;
}

policy::Hand hand_for_subaction(const InstanceRecord& instance, XrPath subaction) {
    if (subaction == XR_NULL_PATH) {
        return policy::Hand::None;
    }
    if (subaction == instance.left_hand) {
        return policy::Hand::Left;
    }
    if (subaction == instance.right_hand) {
        return policy::Hand::Right;
    }
    return policy::Hand::Other;
}

const char* hand_name(policy::Hand hand) {
    switch (hand) {
        case policy::Hand::None: return "any";
        case policy::Hand::Left: return "left";
        case policy::Hand::Right: return "right";
        case policy::Hand::Other: return "other";
    }
    return "?";
}

const char* filter_name(uint32_t filter_mode) {
    switch (filter_mode) {
        case vrtread::kFilterStrict: return "strict";
        case vrtread::kFilterCompatibility: return "compatibility";
        default: return "balanced";
    }
}

bool action_set_is_active(const SessionRecord& session, XrActionSet action_set) {
    return std::find(session.active_sets.begin(), session.active_sets.end(), action_set) != session.active_sets.end();
}

// OpenXR gives an input to the highest-priority active action set that binds it and reports lower-priority
// actions on the same input as inactive (a pause menu taking over the stick, for example). An action that
// is inactive for that reason must stay inactive, opt-in or not.
bool left_stick_claimed_by_higher_priority_set(const State& s, const SessionRecord& session, const ActionRecord& action) {
    const auto own = s.action_sets.find(action.action_set);
    const uint32_t own_priority = own == s.action_sets.end() ? 0U : own->second.priority;
    for (const auto& entry : s.actions) {
        const ActionRecord& other = entry.second;
        if (other.action_set == action.action_set || !other.traits.uses_left_stick_source) {
            continue;
        }
        if (!action_set_is_active(session, other.action_set)) {
            continue;
        }
        const auto other_set = s.action_sets.find(other.action_set);
        if (other_set != s.action_sets.end() && other_set->second.priority > own_priority) {
            return true;
        }
    }
    return false;
}

// ---------------------------------------------------------------------------------------------
// The substitution itself, shared by the float and vector2f hooks. Called with State::mutex held.
// ---------------------------------------------------------------------------------------------

struct Substitution {
    bool inject = false;       // replace Y with `y`
    bool flags_only = false;   // leave the value alone, but changedSinceLastSync / lastChangeTime need correcting
    bool force_active = false;
    float y = 0.0F;
    bool changed = false;
    XrTime change_time = 0;
};

Substitution compute_substitution(State& s, XrSession session_handle, const XrActionStateGetInfo& get_info,
                                  float physical_y, bool runtime_active, bool runtime_changed, XrTime runtime_time) {
    Substitution result;

    const auto session_it = s.sessions.find(session_handle);
    if (session_it == s.sessions.end()) {
        return result;
    }
    SessionRecord& session = session_it->second;
    if (runtime_active) {
        session.saw_active_current = true;
    }
    if (runtime_time > session.latest_time) {
        session.latest_time = runtime_time;
    }

    const QueryKey key{get_info.action, get_info.subactionPath};

    // Not injecting into this read. If we did inject earlier, the value the game sees just changed from
    // "treadmill" back to "physical stick", even though the stick itself may not have moved.
    const auto pass_through = [&]() -> Substitution {
        Substitution out;
        const auto it = session.injected.find(key);
        if (it == session.injected.end()) {
            return out;
        }
        InjectedEntry& entry = it->second;
        if (entry.generation == session.generation) {
            if (entry.released) {  // repeated read inside the sync we released in: same answer again
                out.flags_only = true;
                out.changed = entry.changed || runtime_changed;
                out.change_time = entry.changed ? session.latest_time : 0;
            }
            return out;
        }
        if (entry.released) {  // released in an earlier sync: nothing left to correct
            session.injected.erase(it);
            return out;
        }
        entry.released = true;
        entry.generation = session.generation;
        entry.changed = entry.y != physical_y;
        entry.y = physical_y;
        out.flags_only = true;
        out.changed = entry.changed || runtime_changed;
        out.change_time = entry.changed ? session.latest_time : 0;
        return out;
    };

    const auto action_it = s.actions.find(get_info.action);
    if (action_it == s.actions.end()) {
        return pass_through();
    }
    ActionRecord& action = action_it->second;
    const Snapshot& treadmill = session.treadmill;
    const policy::Hand hand = hand_for_subaction(*action.instance, get_info.subactionPath);
    const policy::Decision decision = policy::decide(action.traits, hand, treadmill.filter_mode);

    // Logged even while standing still, so the log already says what will be driven before the first step.
    if (action.traits.kind != policy::ActionKind::Other) {
        const unsigned bit_index = (treadmill.filter_mode % 3U) * 4U + static_cast<unsigned>(hand);
        const uint16_t bit = static_cast<uint16_t>(1U << bit_index);
        if ((action.logged_decisions & bit) == 0) {
            action.logged_decisions = static_cast<uint16_t>(action.logged_decisions | bit);
            s.log.write("decision action='%s' hand=%s filter=%s -> %s", action.name.c_str(), hand_name(hand),
                        filter_name(treadmill.filter_mode), policy::to_string(decision));
        }
    }

    if (decision != policy::Decision::Drive || !treadmill.engaged || GetTickCount64() - treadmill.taken_ms > kSnapshotMaxAgeMs) {
        return pass_through();
    }

    float base_y = physical_y;
    if (!runtime_active) {
        // Opt-in: keep walking when the left controller is asleep or put down. Only when the game has this
        // action's set active, some other input in the session is live (i.e. the session is focused), and
        // the stick has not been taken over by a higher-priority action set.
        const bool allowed = (treadmill.options & vrtread::kOptionDriveInactive) != 0U &&
                             action_set_is_active(session, action.action_set) &&
                             (session.saw_active_previous || session.saw_active_current) &&
                             !left_stick_claimed_by_higher_priority_set(s, session, action);
        if (!allowed) {
            return pass_through();
        }
        result.force_active = true;
        base_y = 0.0F;
    }

    const float new_y = policy::combine_axis(base_y, treadmill.y, treadmill.combine_mode);
    InjectedEntry& entry = session.injected[key];
    if (entry.generation != session.generation || entry.released) {
        // entry.y is the last value the game saw for this read (injected, or physical at release time).
        // A brand-new entry has no history, so compare against what the runtime would have delivered.
        const bool brand_new = entry.generation == 0;
        entry.changed = brand_new ? (new_y != physical_y || result.force_active) : (new_y != entry.y);
        entry.generation = session.generation;
        entry.released = false;
        entry.y = new_y;
    }

    result.inject = true;
    result.y = new_y;
    result.changed = entry.changed || runtime_changed;
    result.change_time = entry.changed ? session.latest_time : 0;
    ++session.driven_queries;
    if (!session.announced_engaged) {
        session.announced_engaged = true;
        s.log.write("engaged: driving action='%s' hand=%s treadmill_y=%.3f", action.name.c_str(), hand_name(hand),
                    static_cast<double>(treadmill.y));
    }
    return result;
}

// ---------------------------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------------------------

XrResult XRAPI_CALL hook_xrGetActionStateVector2f(XrSession session, const XrActionStateGetInfo* get_info,
                                                  XrActionStateVector2f* out) {
    const std::shared_ptr<InstanceRecord> instance = find_instance_for_session(session);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    const XrResult result = instance->next.get_state_vector2f(session, get_info, out);
    if (XR_FAILED(result) || get_info == nullptr || out == nullptr) {
        return result;
    }
    try {
        State& s = state();
        std::lock_guard<std::mutex> lock(s.mutex);
        const Substitution sub = compute_substitution(s, session, *get_info, out->currentState.y, out->isActive == XR_TRUE,
                                                      out->changedSinceLastSync == XR_TRUE, out->lastChangeTime);
        if (sub.inject) {
            float x = sub.force_active ? 0.0F : out->currentState.x;
            float y = sub.y;
            // Keep the vector inside the unit circle like a real stick, but never shrink below what the
            // runtime itself reported (some runtimes deliver slightly square ranges).
            const float physical_sq = out->currentState.x * out->currentState.x + out->currentState.y * out->currentState.y;
            if (x * x + y * y > (physical_sq > 1.0F ? physical_sq : 1.0F)) {
                policy::clamp_to_unit_circle(x, y);
            }
            out->currentState.x = x;
            out->currentState.y = y;
            if (sub.force_active) {
                out->isActive = XR_TRUE;
            }
        }
        if (sub.inject || sub.flags_only) {
            out->changedSinceLastSync = sub.changed ? XR_TRUE : XR_FALSE;
            if (sub.change_time > out->lastChangeTime) {
                out->lastChangeTime = sub.change_time;
            }
        }
    } catch (...) {
    }
    return result;
}

XrResult XRAPI_CALL hook_xrGetActionStateFloat(XrSession session, const XrActionStateGetInfo* get_info,
                                               XrActionStateFloat* out) {
    const std::shared_ptr<InstanceRecord> instance = find_instance_for_session(session);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    const XrResult result = instance->next.get_state_float(session, get_info, out);
    if (XR_FAILED(result) || get_info == nullptr || out == nullptr) {
        return result;
    }
    try {
        State& s = state();
        std::lock_guard<std::mutex> lock(s.mutex);
        const Substitution sub = compute_substitution(s, session, *get_info, out->currentState, out->isActive == XR_TRUE,
                                                      out->changedSinceLastSync == XR_TRUE, out->lastChangeTime);
        if (sub.inject) {
            out->currentState = sub.y;
            if (sub.force_active) {
                out->isActive = XR_TRUE;
            }
        }
        if (sub.inject || sub.flags_only) {
            out->changedSinceLastSync = sub.changed ? XR_TRUE : XR_FALSE;
            if (sub.change_time > out->lastChangeTime) {
                out->lastChangeTime = sub.change_time;
            }
        }
    } catch (...) {
    }
    return result;
}

// Observed only, never modified: tells us whether any controller input is live in this session,
// which gates the opt-in "drive while the left controller is asleep" behaviour.
XrResult XRAPI_CALL hook_xrGetActionStateBoolean(XrSession session, const XrActionStateGetInfo* get_info,
                                                 XrActionStateBoolean* out) {
    const std::shared_ptr<InstanceRecord> instance = find_instance_for_session(session);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    const XrResult result = instance->next.get_state_boolean(session, get_info, out);
    if (XR_FAILED(result) || out == nullptr || out->isActive != XR_TRUE) {
        return result;
    }
    try {
        State& s = state();
        std::lock_guard<std::mutex> lock(s.mutex);
        const auto it = s.sessions.find(session);
        if (it != s.sessions.end()) {
            it->second.saw_active_current = true;
            if (out->lastChangeTime > it->second.latest_time) {
                it->second.latest_time = out->lastChangeTime;
            }
        }
    } catch (...) {
    }
    return result;
}

XrResult XRAPI_CALL hook_xrSyncActions(XrSession session, const XrActionsSyncInfo* sync_info) {
    const std::shared_ptr<InstanceRecord> instance = find_instance_for_session(session);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    const XrResult result = instance->next.sync_actions(session, sync_info);
    if (XR_FAILED(result)) {
        return result;
    }
    try {
        State& s = state();
        std::lock_guard<std::mutex> lock(s.mutex);
        const auto it = s.sessions.find(session);
        if (it != s.sessions.end()) {
            SessionRecord& record = it->second;
            ++record.generation;
            record.saw_active_previous = record.saw_active_current;
            record.saw_active_current = false;
            record.active_sets.clear();
            if (result == XR_SUCCESS) {
                record.treadmill = s.reader.read(GetTickCount64());
                if (sync_info != nullptr && sync_info->activeActionSets != nullptr) {
                    for (uint32_t index = 0; index < sync_info->countActiveActionSets; ++index) {
                        record.active_sets.push_back(sync_info->activeActionSets[index].actionSet);
                    }
                }
            } else {
                // XR_SESSION_NOT_FOCUSED: the runtime reports every action inactive, and so do we.
                const uint32_t filter_mode = record.treadmill.filter_mode;
                record.treadmill = Snapshot{};
                record.treadmill.filter_mode = filter_mode;
                record.saw_active_previous = false;
            }
        }
    } catch (...) {
    }
    return result;
}

XrResult XRAPI_CALL hook_xrCreateSession(XrInstance instance_handle, const XrSessionCreateInfo* create_info,
                                         XrSession* session) {
    const std::shared_ptr<InstanceRecord> instance = find_instance(instance_handle);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    const XrResult result = instance->next.create_session(instance_handle, create_info, session);
    if (XR_SUCCEEDED(result) && session != nullptr && *session != nullptr) {
        try {
            State& s = state();
            std::lock_guard<std::mutex> lock(s.mutex);
            SessionRecord record;
            record.instance = instance;
            s.sessions[*session] = std::move(record);
            s.log.write("session created");
        } catch (...) {
        }
    }
    return result;
}

XrResult XRAPI_CALL hook_xrDestroySession(XrSession session) {
    const std::shared_ptr<InstanceRecord> instance = find_instance_for_session(session);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    try {
        State& s = state();
        std::lock_guard<std::mutex> lock(s.mutex);
        const auto it = s.sessions.find(session);
        if (it != s.sessions.end()) {
            s.log.write("session destroyed: %llu treadmill-driven reads", it->second.driven_queries);
            s.sessions.erase(it);
        }
    } catch (...) {
    }
    return instance->next.destroy_session(session);
}

XrResult XRAPI_CALL hook_xrCreateActionSet(XrInstance instance_handle, const XrActionSetCreateInfo* create_info,
                                           XrActionSet* action_set) {
    const std::shared_ptr<InstanceRecord> instance = find_instance(instance_handle);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    const XrResult result = instance->next.create_action_set(instance_handle, create_info, action_set);
    if (XR_SUCCEEDED(result) && action_set != nullptr && *action_set != nullptr) {
        try {
            State& s = state();
            std::lock_guard<std::mutex> lock(s.mutex);
            ActionSetRecord& record = s.action_sets[*action_set];
            record.instance = instance;
            record.priority = create_info != nullptr ? create_info->priority : 0U;
        } catch (...) {
        }
    }
    return result;
}

XrResult XRAPI_CALL hook_xrDestroyActionSet(XrActionSet action_set) {
    const std::shared_ptr<InstanceRecord> instance = find_instance_for_action_set(action_set);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    try {
        State& s = state();
        std::lock_guard<std::mutex> lock(s.mutex);
        s.action_sets.erase(action_set);
        for (auto it = s.actions.begin(); it != s.actions.end();) {
            it = it->second.action_set == action_set ? s.actions.erase(it) : std::next(it);
        }
    } catch (...) {
    }
    return instance->next.destroy_action_set(action_set);
}

XrResult XRAPI_CALL hook_xrCreateAction(XrActionSet action_set, const XrActionCreateInfo* create_info, XrAction* action) {
    const std::shared_ptr<InstanceRecord> instance = find_instance_for_action_set(action_set);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    const XrResult result = instance->next.create_action(action_set, create_info, action);
    if (XR_SUCCEEDED(result) && create_info != nullptr && action != nullptr && *action != nullptr) {
        try {
            ActionRecord record;
            record.instance = instance;
            record.action_set = action_set;
            record.name.assign(create_info->actionName, strnlen(create_info->actionName, XR_MAX_ACTION_NAME_SIZE));
            const std::string localized(create_info->localizedActionName,
                                        strnlen(create_info->localizedActionName, XR_MAX_LOCALIZED_ACTION_NAME_SIZE));
            switch (create_info->actionType) {
                case XR_ACTION_TYPE_FLOAT_INPUT: record.traits.kind = policy::ActionKind::Float; break;
                case XR_ACTION_TYPE_VECTOR2F_INPUT: record.traits.kind = policy::ActionKind::Vector2; break;
                default: record.traits.kind = policy::ActionKind::Other; break;
            }
            record.traits.name = policy::classify_name(record.name, localized);

            State& s = state();
            std::lock_guard<std::mutex> lock(s.mutex);
            if (record.traits.kind != policy::ActionKind::Other) {
                s.log.write("action created name='%s' localized='%s' type=%s subactions=%u", record.name.c_str(),
                            localized.c_str(), record.traits.kind == policy::ActionKind::Float ? "float" : "vector2f",
                            create_info->countSubactionPaths);
            }
            s.actions[*action] = std::move(record);
        } catch (...) {
        }
    }
    return result;
}

XrResult XRAPI_CALL hook_xrDestroyAction(XrAction action) {
    const std::shared_ptr<InstanceRecord> instance = find_instance_for_action(action);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    try {
        State& s = state();
        std::lock_guard<std::mutex> lock(s.mutex);
        s.actions.erase(action);
    } catch (...) {
    }
    return instance->next.destroy_action(action);
}

XrResult XRAPI_CALL hook_xrSuggestInteractionProfileBindings(XrInstance instance_handle,
                                                             const XrInteractionProfileSuggestedBinding* suggested) {
    const std::shared_ptr<InstanceRecord> instance = find_instance(instance_handle);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    const XrResult result = instance->next.suggest_bindings(instance_handle, suggested);
    if (XR_FAILED(result) || suggested == nullptr || suggested->suggestedBindings == nullptr) {
        return result;
    }
    try {
        const std::string profile = resolve_path(instance, suggested->interactionProfile);
        for (uint32_t index = 0; index < suggested->countSuggestedBindings; ++index) {
            const XrActionSuggestedBinding& binding = suggested->suggestedBindings[index];
            if (binding.action == nullptr) {
                continue;
            }
            const std::string path = resolve_path(instance, binding.binding);
            if (path.empty()) {
                continue;
            }
            const policy::BindingClass binding_class = policy::classify_binding_path(path);

            State& s = state();
            std::lock_guard<std::mutex> lock(s.mutex);
            const auto it = s.actions.find(binding.action);
            if (it == s.actions.end()) {
                continue;
            }
            const bool was_left = it->second.traits.left_stick;
            policy::note_binding(it->second.traits, binding_class);
            if (!was_left && it->second.traits.left_stick) {
                s.log.write("left-stick binding: action='%s' path='%s' profile='%s'", it->second.name.c_str(), path.c_str(),
                            profile.c_str());
            }
        }
    } catch (...) {
    }
    return result;
}

XrResult XRAPI_CALL hook_xrDestroyInstance(XrInstance instance_handle) {
    const std::shared_ptr<InstanceRecord> instance = find_instance(instance_handle);
    if (!instance) {
        return XR_ERROR_HANDLE_INVALID;
    }
    try {
        State& s = state();
        std::lock_guard<std::mutex> lock(s.mutex);
        for (auto it = s.sessions.begin(); it != s.sessions.end();) {
            it = it->second.instance == instance ? s.sessions.erase(it) : std::next(it);
        }
        for (auto it = s.actions.begin(); it != s.actions.end();) {
            it = it->second.instance == instance ? s.actions.erase(it) : std::next(it);
        }
        for (auto it = s.action_sets.begin(); it != s.action_sets.end();) {
            it = it->second.instance == instance ? s.action_sets.erase(it) : std::next(it);
        }
        s.instances.erase(instance_handle);
        s.log.write("instance destroyed");
    } catch (...) {
    }
    return instance->next.destroy_instance(instance_handle);
}

XrResult XRAPI_CALL layer_xrGetInstanceProcAddr(XrInstance instance_handle, const char* name, PFN_xrVoidFunction* function);

struct HookEntry {
    const char* name;
    PFN_xrVoidFunction function;
};

const HookEntry kHooks[] = {
    {"xrGetInstanceProcAddr", reinterpret_cast<PFN_xrVoidFunction>(layer_xrGetInstanceProcAddr)},
    {"xrDestroyInstance", reinterpret_cast<PFN_xrVoidFunction>(hook_xrDestroyInstance)},
    {"xrCreateSession", reinterpret_cast<PFN_xrVoidFunction>(hook_xrCreateSession)},
    {"xrDestroySession", reinterpret_cast<PFN_xrVoidFunction>(hook_xrDestroySession)},
    {"xrCreateActionSet", reinterpret_cast<PFN_xrVoidFunction>(hook_xrCreateActionSet)},
    {"xrDestroyActionSet", reinterpret_cast<PFN_xrVoidFunction>(hook_xrDestroyActionSet)},
    {"xrCreateAction", reinterpret_cast<PFN_xrVoidFunction>(hook_xrCreateAction)},
    {"xrDestroyAction", reinterpret_cast<PFN_xrVoidFunction>(hook_xrDestroyAction)},
    {"xrSuggestInteractionProfileBindings", reinterpret_cast<PFN_xrVoidFunction>(hook_xrSuggestInteractionProfileBindings)},
    {"xrSyncActions", reinterpret_cast<PFN_xrVoidFunction>(hook_xrSyncActions)},
    {"xrGetActionStateBoolean", reinterpret_cast<PFN_xrVoidFunction>(hook_xrGetActionStateBoolean)},
    {"xrGetActionStateFloat", reinterpret_cast<PFN_xrVoidFunction>(hook_xrGetActionStateFloat)},
    {"xrGetActionStateVector2f", reinterpret_cast<PFN_xrVoidFunction>(hook_xrGetActionStateVector2f)},
};

XrResult XRAPI_CALL layer_xrGetInstanceProcAddr(XrInstance instance_handle, const char* name, PFN_xrVoidFunction* function) {
    if (name == nullptr || function == nullptr) {
        return XR_ERROR_VALIDATION_FAILURE;
    }
    *function = nullptr;

    const std::shared_ptr<InstanceRecord> instance = find_instance(instance_handle);
    if (!instance) {
        // The loader resolves the few functions that are legal without an instance by itself.
        return instance_handle == nullptr ? XR_ERROR_FUNCTION_UNSUPPORTED : XR_ERROR_HANDLE_INVALID;
    }
    if (!instance->passthrough) {
        for (const HookEntry& hook : kHooks) {
            if (std::strcmp(name, hook.name) == 0) {
                *function = hook.function;
                return XR_SUCCESS;
            }
        }
    }
    return instance->next.get_instance_proc_addr(instance_handle, name, function);
}

template <typename Fn>
void load_next(const NextTable& table, XrInstance instance, const char* name, Fn& out) {
    PFN_xrVoidFunction raw = nullptr;
    if (XR_SUCCEEDED(table.get_instance_proc_addr(instance, name, &raw)) && raw != nullptr) {
        out = reinterpret_cast<Fn>(raw);
    }
}

XrResult XRAPI_CALL layer_xrCreateApiLayerInstance(const XrInstanceCreateInfo* info, const XrApiLayerCreateInfo* layer_info,
                                                   XrInstance* instance_out) {
    if (layer_info == nullptr || layer_info->nextInfo == nullptr || layer_info->nextInfo->nextGetInstanceProcAddr == nullptr ||
        layer_info->nextInfo->nextCreateApiLayerInstance == nullptr) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }

    const XrApiLayerNextInfo* const next_info = layer_info->nextInfo;
    XrApiLayerCreateInfo next_layer_info = *layer_info;
    next_layer_info.nextInfo = next_info->next;
    const XrResult result = next_info->nextCreateApiLayerInstance(info, &next_layer_info, instance_out);
    if (XR_FAILED(result) || instance_out == nullptr || *instance_out == nullptr) {
        return result;
    }

    try {
        State& s = state();
        s.log.configure();

        auto record = std::make_shared<InstanceRecord>();
        record->handle = *instance_out;
        NextTable& next = record->next;
        next.get_instance_proc_addr = next_info->nextGetInstanceProcAddr;
        load_next(next, record->handle, "xrDestroyInstance", next.destroy_instance);
        load_next(next, record->handle, "xrCreateSession", next.create_session);
        load_next(next, record->handle, "xrDestroySession", next.destroy_session);
        load_next(next, record->handle, "xrStringToPath", next.string_to_path);
        load_next(next, record->handle, "xrPathToString", next.path_to_string);
        load_next(next, record->handle, "xrCreateActionSet", next.create_action_set);
        load_next(next, record->handle, "xrDestroyActionSet", next.destroy_action_set);
        load_next(next, record->handle, "xrCreateAction", next.create_action);
        load_next(next, record->handle, "xrDestroyAction", next.destroy_action);
        load_next(next, record->handle, "xrSuggestInteractionProfileBindings", next.suggest_bindings);
        load_next(next, record->handle, "xrSyncActions", next.sync_actions);
        load_next(next, record->handle, "xrGetActionStateBoolean", next.get_state_boolean);
        load_next(next, record->handle, "xrGetActionStateFloat", next.get_state_float);
        load_next(next, record->handle, "xrGetActionStateVector2f", next.get_state_vector2f);

        const std::string exe = policy::lower_copy(narrow(basename_of(process_path())));
        const char* passthrough_reason = nullptr;
        if (!next.complete()) {
            passthrough_reason = "next layer/runtime is missing a required function";
        } else if (env_flag("VRTREAD_OPENXR_BYPASS", false)) {
            passthrough_reason = "VRTREAD_OPENXR_BYPASS is set";
        } else if (process_is_runtime_helper(exe) && !env_flag("VRTREAD_OPENXR_ALLOW_SYSTEM_PROCESSES", false)) {
            passthrough_reason = "VR runtime helper process";
        } else if (XR_FAILED(next.string_to_path(record->handle, "/user/hand/left", &record->left_hand)) ||
                   XR_FAILED(next.string_to_path(record->handle, "/user/hand/right", &record->right_hand))) {
            passthrough_reason = "could not resolve hand paths";
        }
        record->passthrough = passthrough_reason != nullptr;

        const char* app_name = info != nullptr ? info->applicationInfo.applicationName : "";
        const char* engine_name = info != nullptr ? info->applicationInfo.engineName : "";
        const XrVersion api = info != nullptr ? info->applicationInfo.apiVersion : 0;
        s.log.write("layer %s attached: exe='%s' app='%.127s' engine='%.127s' api=%u.%u mode=%s%s%s", VRTREAD_LAYER_VERSION,
                    exe.c_str(), app_name, engine_name, static_cast<unsigned>(XR_VERSION_MAJOR(api)),
                    static_cast<unsigned>(XR_VERSION_MINOR(api)), record->passthrough ? "passthrough" : "active",
                    record->passthrough ? " reason=" : "", record->passthrough ? passthrough_reason : "");

        std::lock_guard<std::mutex> lock(s.mutex);
        s.instances[record->handle] = std::move(record);
    } catch (...) {
        // Could not register the instance (out of memory). Our xrGetInstanceProcAddr cannot serve an
        // unregistered instance, so fail instance creation cleanly rather than leaving a half-wired one.
        PFN_xrVoidFunction destroy = nullptr;
        if (XR_SUCCEEDED(next_info->nextGetInstanceProcAddr(*instance_out, "xrDestroyInstance", &destroy)) && destroy != nullptr) {
            reinterpret_cast<PFN_xrDestroyInstance>(destroy)(*instance_out);
        }
        *instance_out = nullptr;
        return XR_ERROR_RUNTIME_FAILURE;
    }
    return result;
}

}  // namespace

// The loader unloads the layer DLL after xrDestroyInstance and loads it again for the next instance, so
// release the shared-memory view on a real unload. On process termination (reserved != nullptr) other
// threads may already be gone mid-call; touch nothing.
BOOL WINAPI DllMain(HINSTANCE, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_DETACH && reserved == nullptr) {
        delete g_state;
        g_state = nullptr;
    }
    return TRUE;
}

extern "C" __declspec(dllexport) XrResult XRAPI_CALL xrNegotiateLoaderApiLayerInterface(
    const XrNegotiateLoaderInfo* loader_info, const char* layer_name, XrNegotiateApiLayerRequest* request) {
    if (loader_info == nullptr || layer_name == nullptr || request == nullptr) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    if (std::strcmp(layer_name, kLayerName) != 0) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    if (loader_info->structType != XR_LOADER_INTERFACE_STRUCT_LOADER_INFO ||
        loader_info->structVersion != XR_LOADER_INFO_STRUCT_VERSION ||
        loader_info->structSize != sizeof(XrNegotiateLoaderInfo)) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    if (request->structType != XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST ||
        request->structVersion != XR_API_LAYER_INFO_STRUCT_VERSION ||
        request->structSize != sizeof(XrNegotiateApiLayerRequest)) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    if (loader_info->minInterfaceVersion > XR_CURRENT_LOADER_API_LAYER_VERSION ||
        loader_info->maxInterfaceVersion < XR_CURRENT_LOADER_API_LAYER_VERSION) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    // Only OpenXR 1.0 core input entry points are used, which every 1.x loader and runtime provides.
    if (XR_VERSION_MAJOR(loader_info->minApiVersion) > 1 || XR_VERSION_MAJOR(loader_info->maxApiVersion) < 1) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }

    request->layerInterfaceVersion = XR_CURRENT_LOADER_API_LAYER_VERSION;
    request->layerApiVersion = XR_API_VERSION_1_0;
    request->getInstanceProcAddr = layer_xrGetInstanceProcAddr;
    request->createApiLayerInstance = layer_xrCreateApiLayerInstance;
    return XR_SUCCESS;
}
