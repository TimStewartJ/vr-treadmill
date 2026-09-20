// Tests that talk to the layer DLL directly, the way a loader does, without the Khronos loader in between:
// negotiation edge cases, xrGetInstanceProcAddr behaviour, and two simultaneous instances (which the
// Khronos loader never produces, but which the layer's per-instance dispatch must survive).

#include "harness.h"

#include <openxr/openxr_loader_negotiation.h>

using namespace harness;

namespace {

struct LayerDll {
    LayerDll() {
        module = LoadLibraryA(layer_dll_path().c_str());
        if (module == nullptr) {
            throw std::runtime_error("could not load the layer DLL directly");
        }
        negotiate = reinterpret_cast<PFN_xrNegotiateLoaderApiLayerInterface>(
            GetProcAddress(module, "xrNegotiateLoaderApiLayerInterface"));
        if (negotiate == nullptr) {
            throw std::runtime_error("layer DLL does not export xrNegotiateLoaderApiLayerInterface");
        }
    }
    ~LayerDll() { FreeLibrary(module); }
    LayerDll(const LayerDll&) = delete;
    LayerDll& operator=(const LayerDll&) = delete;

    HMODULE module = nullptr;
    PFN_xrNegotiateLoaderApiLayerInterface negotiate = nullptr;
};

XrNegotiateLoaderInfo good_loader_info() {
    // Exactly what every Khronos loader from 1.0.0 to 1.1.x sends.
    XrNegotiateLoaderInfo info{};
    info.structType = XR_LOADER_INTERFACE_STRUCT_LOADER_INFO;
    info.structVersion = XR_LOADER_INFO_STRUCT_VERSION;
    info.structSize = sizeof(XrNegotiateLoaderInfo);
    info.minInterfaceVersion = 1;
    info.maxInterfaceVersion = XR_CURRENT_LOADER_API_LAYER_VERSION;
    info.minApiVersion = XR_MAKE_VERSION(1, 0, 0);
    info.maxApiVersion = XR_MAKE_VERSION(1, 0x3ff, 0xfff);
    return info;
}

XrNegotiateApiLayerRequest empty_request() {
    XrNegotiateApiLayerRequest request{};
    request.structType = XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST;
    request.structVersion = XR_API_LAYER_INFO_STRUCT_VERSION;
    request.structSize = sizeof(XrNegotiateApiLayerRequest);
    return request;
}

// --- a minimal "loader": mock runtime at the bottom, the layer on top -------------------------------------

PFN_xrGetInstanceProcAddr g_runtime_gipa = nullptr;

XrResult XRAPI_CALL terminator_create_api_layer_instance(const XrInstanceCreateInfo* info, const XrApiLayerCreateInfo*,
                                                         XrInstance* instance) {
    PFN_xrVoidFunction create = nullptr;
    const XrResult found = g_runtime_gipa(XR_NULL_HANDLE, "xrCreateInstance", &create);
    if (XR_FAILED(found)) {
        return found;
    }
    return reinterpret_cast<XrResult(XRAPI_PTR*)(const XrInstanceCreateInfo*, XrInstance*)>(create)(info, instance);
}

void load_runtime_gipa() {
    if (g_runtime_gipa != nullptr) {
        return;
    }
    const HMODULE runtime = LoadLibraryA(VRTREAD_TEST_RUNTIME_DLL);
    REQUIRE(runtime != nullptr);
    using NegotiateRuntime = XrResult(XRAPI_PTR*)(const XrNegotiateLoaderInfo*, XrNegotiateRuntimeRequest*);
    const auto negotiate = reinterpret_cast<NegotiateRuntime>(GetProcAddress(runtime, "xrNegotiateLoaderRuntimeInterface"));
    REQUIRE(negotiate != nullptr);
    XrNegotiateLoaderInfo info = good_loader_info();
    info.maxInterfaceVersion = XR_CURRENT_LOADER_RUNTIME_VERSION;
    XrNegotiateRuntimeRequest request{};
    request.structType = XR_LOADER_INTERFACE_STRUCT_RUNTIME_REQUEST;
    request.structVersion = XR_RUNTIME_INFO_STRUCT_VERSION;
    request.structSize = sizeof(XrNegotiateRuntimeRequest);
    REQUIRE_XR(negotiate(&info, &request));
    g_runtime_gipa = request.getInstanceProcAddr;
}

// An OpenXR client wired straight to the layer's dispatch, one per instance.
struct DirectGame {
    DirectGame(const XrNegotiateApiLayerRequest& layer, const char* name) : layer_gipa(layer.getInstanceProcAddr) {
        XrApiLayerNextInfo next{};
        next.structType = XR_LOADER_INTERFACE_STRUCT_API_LAYER_NEXT_INFO;
        next.structVersion = XR_API_LAYER_NEXT_INFO_STRUCT_VERSION;
        next.structSize = sizeof(XrApiLayerNextInfo);
        strncpy_s(next.layerName, kLayerName, _TRUNCATE);
        next.nextGetInstanceProcAddr = g_runtime_gipa;
        next.nextCreateApiLayerInstance = terminator_create_api_layer_instance;
        next.next = nullptr;

        XrApiLayerCreateInfo layer_info{};
        layer_info.structType = XR_LOADER_INTERFACE_STRUCT_API_LAYER_CREATE_INFO;
        layer_info.structVersion = XR_API_LAYER_CREATE_INFO_STRUCT_VERSION;
        layer_info.structSize = sizeof(XrApiLayerCreateInfo);
        layer_info.nextInfo = &next;

        XrInstanceCreateInfo info{XR_TYPE_INSTANCE_CREATE_INFO};
        strncpy_s(info.applicationInfo.applicationName, name, _TRUNCATE);
        info.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
        REQUIRE_XR(layer.createApiLayerInstance(&info, &layer_info, &instance));

        resolve("xrDestroyInstance", destroy_instance);
        resolve("xrCreateSession", create_session);
        resolve("xrDestroySession", destroy_session);
        resolve("xrStringToPath", string_to_path);
        resolve("xrCreateActionSet", create_action_set);
        resolve("xrCreateAction", create_action);
        resolve("xrSuggestInteractionProfileBindings", suggest);
        resolve("xrAttachSessionActionSets", attach);
        resolve("xrSyncActions", sync_actions);
        resolve("xrGetActionStateVector2f", get_vector2f);

        XrSessionCreateInfo session_info{XR_TYPE_SESSION_CREATE_INFO};
        session_info.systemId = 1;
        REQUIRE_XR(create_session(instance, &session_info, &session));

        XrActionSetCreateInfo set_info{XR_TYPE_ACTION_SET_CREATE_INFO};
        strncpy_s(set_info.actionSetName, "gameplay", _TRUNCATE);
        strncpy_s(set_info.localizedActionSetName, "Gameplay", _TRUNCATE);
        REQUIRE_XR(create_action_set(instance, &set_info, &set));

        XrActionCreateInfo action_info{XR_TYPE_ACTION_CREATE_INFO};
        strncpy_s(action_info.actionName, "move", _TRUNCATE);
        strncpy_s(action_info.localizedActionName, "Move", _TRUNCATE);
        action_info.actionType = XR_ACTION_TYPE_VECTOR2F_INPUT;
        REQUIRE_XR(create_action(set, &action_info, &move));

        XrPath profile = XR_NULL_PATH;
        XrPath stick = XR_NULL_PATH;
        REQUIRE_XR(string_to_path(instance, kTouchProfile, &profile));
        REQUIRE_XR(string_to_path(instance, "/user/hand/left/input/thumbstick", &stick));
        const XrActionSuggestedBinding binding{move, stick};
        XrInteractionProfileSuggestedBinding suggested{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
        suggested.interactionProfile = profile;
        suggested.countSuggestedBindings = 1;
        suggested.suggestedBindings = &binding;
        REQUIRE_XR(suggest(instance, &suggested));

        XrSessionActionSetsAttachInfo attach_info{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};
        attach_info.countActionSets = 1;
        attach_info.actionSets = &set;
        REQUIRE_XR(attach(session, &attach_info));
    }

    ~DirectGame() { shutdown(); }

    void shutdown() {
        if (instance != XR_NULL_HANDLE) {
            destroy_session(session);
            destroy_instance(instance);
            instance = XR_NULL_HANDLE;
        }
    }

    float move_y() {
        const XrActiveActionSet active{set, XR_NULL_PATH};
        XrActionsSyncInfo sync_info{XR_TYPE_ACTIONS_SYNC_INFO};
        sync_info.countActiveActionSets = 1;
        sync_info.activeActionSets = &active;
        REQUIRE_XR(sync_actions(session, &sync_info));
        XrActionStateGetInfo get_info{XR_TYPE_ACTION_STATE_GET_INFO};
        get_info.action = move;
        XrActionStateVector2f state{XR_TYPE_ACTION_STATE_VECTOR2F};
        REQUIRE_XR(get_vector2f(session, &get_info, &state));
        return state.currentState.y;
    }

    template <typename Fn>
    void resolve(const char* name, Fn& out) {
        PFN_xrVoidFunction raw = nullptr;
        REQUIRE_XR(layer_gipa(instance, name, &raw));
        REQUIRE(raw != nullptr);
        out = reinterpret_cast<Fn>(raw);
    }

    PFN_xrGetInstanceProcAddr layer_gipa = nullptr;
    XrInstance instance = XR_NULL_HANDLE;
    XrSession session = XR_NULL_HANDLE;
    XrActionSet set = XR_NULL_HANDLE;
    XrAction move = XR_NULL_HANDLE;

    XrResult(XRAPI_PTR* destroy_instance)(XrInstance) = nullptr;
    XrResult(XRAPI_PTR* create_session)(XrInstance, const XrSessionCreateInfo*, XrSession*) = nullptr;
    XrResult(XRAPI_PTR* destroy_session)(XrSession) = nullptr;
    XrResult(XRAPI_PTR* string_to_path)(XrInstance, const char*, XrPath*) = nullptr;
    XrResult(XRAPI_PTR* create_action_set)(XrInstance, const XrActionSetCreateInfo*, XrActionSet*) = nullptr;
    XrResult(XRAPI_PTR* create_action)(XrActionSet, const XrActionCreateInfo*, XrAction*) = nullptr;
    XrResult(XRAPI_PTR* suggest)(XrInstance, const XrInteractionProfileSuggestedBinding*) = nullptr;
    XrResult(XRAPI_PTR* attach)(XrSession, const XrSessionActionSetsAttachInfo*) = nullptr;
    XrResult(XRAPI_PTR* sync_actions)(XrSession, const XrActionsSyncInfo*) = nullptr;
    XrResult(XRAPI_PTR* get_vector2f)(XrSession, const XrActionStateGetInfo*, XrActionStateVector2f*) = nullptr;
};

}  // namespace

TEST(negotiation_accepts_every_khronos_loader) {
    LayerDll dll;
    XrNegotiateLoaderInfo info = good_loader_info();
    XrNegotiateApiLayerRequest request = empty_request();
    REQUIRE_XR(dll.negotiate(&info, kLayerName, &request));
    CHECK(request.layerInterfaceVersion == 1U);
    CHECK(XR_VERSION_MAJOR(request.layerApiVersion) == 1);
    CHECK(request.getInstanceProcAddr != nullptr);
    CHECK(request.createApiLayerInstance != nullptr);

    // A future loader that also speaks newer interface versions still gets version 1.
    info.maxInterfaceVersion = 5;
    request = empty_request();
    CHECK_XR(dll.negotiate(&info, kLayerName, &request));
    CHECK(request.layerInterfaceVersion == 1U);

    // A loader restricted to OpenXR 1.0 only.
    info = good_loader_info();
    info.maxApiVersion = XR_MAKE_VERSION(1, 0, 0xfff);
    request = empty_request();
    CHECK_XR(dll.negotiate(&info, kLayerName, &request));
}

TEST(negotiation_rejects_what_it_cannot_serve) {
    LayerDll dll;
    XrNegotiateLoaderInfo info = good_loader_info();
    XrNegotiateApiLayerRequest request = empty_request();

    CHECK(dll.negotiate(nullptr, kLayerName, &request) == XR_ERROR_INITIALIZATION_FAILED);
    CHECK(dll.negotiate(&info, nullptr, &request) == XR_ERROR_INITIALIZATION_FAILED);
    CHECK(dll.negotiate(&info, kLayerName, nullptr) == XR_ERROR_INITIALIZATION_FAILED);
    CHECK(dll.negotiate(&info, "XR_APILAYER_SOMEONE_else", &request) == XR_ERROR_INITIALIZATION_FAILED);

    info = good_loader_info();
    info.structSize = sizeof(XrNegotiateLoaderInfo) - 4;
    CHECK(dll.negotiate(&info, kLayerName, &request) == XR_ERROR_INITIALIZATION_FAILED);

    info = good_loader_info();
    info.structType = XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST;
    CHECK(dll.negotiate(&info, kLayerName, &request) == XR_ERROR_INITIALIZATION_FAILED);

    info = good_loader_info();
    info.minInterfaceVersion = 2;  // a loader that no longer speaks interface 1
    info.maxInterfaceVersion = 3;
    CHECK(dll.negotiate(&info, kLayerName, &request) == XR_ERROR_INITIALIZATION_FAILED);

    info = good_loader_info();
    info.minApiVersion = XR_MAKE_VERSION(2, 0, 0);  // OpenXR 2.x only
    info.maxApiVersion = XR_MAKE_VERSION(2, 0x3ff, 0xfff);
    CHECK(dll.negotiate(&info, kLayerName, &request) == XR_ERROR_INITIALIZATION_FAILED);

    info = good_loader_info();
    XrNegotiateApiLayerRequest bad_request = empty_request();
    bad_request.structSize = 8;
    CHECK(dll.negotiate(&info, kLayerName, &bad_request) == XR_ERROR_INITIALIZATION_FAILED);
    CHECK(bad_request.getInstanceProcAddr == nullptr);  // nothing written on failure
}

TEST(negotiation_create_instance_validates_the_chain) {
    LayerDll dll;
    XrNegotiateLoaderInfo info = good_loader_info();
    XrNegotiateApiLayerRequest request = empty_request();
    REQUIRE_XR(dll.negotiate(&info, kLayerName, &request));

    XrInstanceCreateInfo create_info{XR_TYPE_INSTANCE_CREATE_INFO};
    XrInstance instance = XR_NULL_HANDLE;
    CHECK(request.createApiLayerInstance(&create_info, nullptr, &instance) == XR_ERROR_INITIALIZATION_FAILED);

    XrApiLayerCreateInfo no_next{};
    no_next.structType = XR_LOADER_INTERFACE_STRUCT_API_LAYER_CREATE_INFO;
    no_next.structVersion = XR_API_LAYER_CREATE_INFO_STRUCT_VERSION;
    no_next.structSize = sizeof(XrApiLayerCreateInfo);
    CHECK(request.createApiLayerInstance(&create_info, &no_next, &instance) == XR_ERROR_INITIALIZATION_FAILED);
    CHECK(instance == XR_NULL_HANDLE);
}

TEST(negotiation_get_instance_proc_addr) {
    reset_world();
    load_runtime_gipa();
    LayerDll dll;
    XrNegotiateLoaderInfo info = good_loader_info();
    XrNegotiateApiLayerRequest request = empty_request();
    REQUIRE_XR(dll.negotiate(&info, kLayerName, &request));

    PFN_xrVoidFunction function = reinterpret_cast<PFN_xrVoidFunction>(0x1);
    CHECK(request.getInstanceProcAddr(XR_NULL_HANDLE, "xrCreateSession", &function) == XR_ERROR_FUNCTION_UNSUPPORTED);
    CHECK(function == nullptr);
    CHECK(request.getInstanceProcAddr(reinterpret_cast<XrInstance>(0x1234), "xrCreateSession", &function) ==
          XR_ERROR_HANDLE_INVALID);
    CHECK(request.getInstanceProcAddr(XR_NULL_HANDLE, nullptr, &function) == XR_ERROR_VALIDATION_FAILURE);
    CHECK(request.getInstanceProcAddr(XR_NULL_HANDLE, "xrCreateSession", nullptr) == XR_ERROR_VALIDATION_FAILURE);

    DirectGame game(request, "GipaGame");

    // Functions the layer has no business with are handed out unwrapped: zero overhead, zero risk.
    PFN_xrVoidFunction from_layer = nullptr;
    PFN_xrVoidFunction from_runtime = nullptr;
    CHECK_XR(request.getInstanceProcAddr(game.instance, "xrGetSystem", &from_layer));
    CHECK_XR(g_runtime_gipa(game.instance, "xrGetSystem", &from_runtime));
    CHECK(from_layer == from_runtime);
    CHECK_XR(request.getInstanceProcAddr(game.instance, "xrAttachSessionActionSets", &from_layer));
    CHECK_XR(g_runtime_gipa(game.instance, "xrAttachSessionActionSets", &from_runtime));
    CHECK(from_layer == from_runtime);

    // The hooked ones are wrapped.
    CHECK_XR(request.getInstanceProcAddr(game.instance, "xrGetActionStateVector2f", &from_layer));
    CHECK_XR(g_runtime_gipa(game.instance, "xrGetActionStateVector2f", &from_runtime));
    CHECK(from_layer != from_runtime);

    // Unknown names get the runtime's answer.
    CHECK(request.getInstanceProcAddr(game.instance, "xrNoSuchFunction", &from_layer) == XR_ERROR_FUNCTION_UNSUPPORTED);
    CHECK(from_layer == nullptr);
}

TEST(negotiation_two_simultaneous_instances) {
    reset_world();
    load_runtime_gipa();
    LayerDll dll;
    XrNegotiateLoaderInfo info = good_loader_info();
    XrNegotiateApiLayerRequest request = empty_request();
    REQUIRE_XR(dll.negotiate(&info, kLayerName, &request));

    publisher().publish(0.55F);
    DirectGame first(request, "FirstInstance");
    DirectGame second(request, "SecondInstance");
    CHECK(MockRuntime::get().live_instances() == 2U);
    CHECK_NEAR(first.move_y(), 0.55F);
    CHECK_NEAR(second.move_y(), 0.55F);

    publisher().publish(0.25F);
    CHECK_NEAR(second.move_y(), 0.25F);
    CHECK_NEAR(first.move_y(), 0.25F);

    const XrInstance first_handle = first.instance;
    first.shutdown();  // destroying one instance must not disturb the other
    CHECK(MockRuntime::get().live_instances() == 1U);
    publisher().publish(0.75F);
    CHECK_NEAR(second.move_y(), 0.75F);

    PFN_xrVoidFunction function = nullptr;
    CHECK(request.getInstanceProcAddr(first_handle, "xrCreateSession", &function) == XR_ERROR_HANDLE_INVALID);

    second.shutdown();
    CHECK(MockRuntime::get().live_instances() == 0U);
}

TEST(negotiation_pinned_dll_survives_many_instance_cycles) {
    // Another module holding the DLL (or a loader that never unloads layers) keeps the layer's state alive
    // across instances; make sure nothing from a destroyed instance leaks into the next one.
    reset_world();
    LayerDll pin;
    for (int cycle = 0; cycle < 5; ++cycle) {
        Game game("CycleGame", "Custom");
        REQUIRE_XR(game.create_result);
        const XrActionSet set = game.create_set("gameplay");
        const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
        const XrAction turn = game.create_action(set, "turn", XR_ACTION_TYPE_VECTOR2F_INPUT);
        game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}, {turn, "/user/hand/right/input/thumbstick"}});
        game.attach({set});
        const float y = 0.1F * static_cast<float>(cycle + 1);
        publisher().publish(y);
        REQUIRE_XR(game.sync({set}));
        CHECK_NEAR(game.vec2(move).currentState.y, y);
        CHECK_NEAR(game.vec2(turn).currentState.y, 0.0F);
    }
    CHECK(MockRuntime::get().live_instances() == 0U);
}
