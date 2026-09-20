// Prints the size of every struct, the offset of every field, and the value of every constant that the
// layer takes from include/openxr_minimal.h.
//
// The build compiles this file twice, once against openxr_minimal.h and once against the official Khronos
// headers, runs both, and fails if the outputs differ (see compare_abi.cmake). That keeps the hand-written
// header honest without making the shipping DLL depend on a 700 KB vendored header or a network fetch.

#include <cstddef>
#include <cstdio>

#ifdef VRTREAD_ABI_OFFICIAL
#include <openxr/openxr.h>
#include <openxr/openxr_loader_negotiation.h>
#else
#include "openxr_minimal.h"
#endif

#define S(T) std::printf("sizeof(%s)=%zu\n", #T, sizeof(T))
#define P(T, F) std::printf("offsetof(%s,%s)=%zu\n", #T, #F, offsetof(T, F))
#define V(X) std::printf("%s=%lld\n", #X, static_cast<long long>(X))

int main() {
    S(XrApplicationInfo);
    P(XrApplicationInfo, applicationName);
    P(XrApplicationInfo, applicationVersion);
    P(XrApplicationInfo, engineName);
    P(XrApplicationInfo, engineVersion);
    P(XrApplicationInfo, apiVersion);

    S(XrInstanceCreateInfo);
    P(XrInstanceCreateInfo, type);
    P(XrInstanceCreateInfo, next);
    P(XrInstanceCreateInfo, createFlags);
    P(XrInstanceCreateInfo, applicationInfo);
    P(XrInstanceCreateInfo, enabledApiLayerCount);
    P(XrInstanceCreateInfo, enabledApiLayerNames);
    P(XrInstanceCreateInfo, enabledExtensionCount);
    P(XrInstanceCreateInfo, enabledExtensionNames);

    S(XrActionCreateInfo);
    P(XrActionCreateInfo, type);
    P(XrActionCreateInfo, next);
    P(XrActionCreateInfo, actionName);
    P(XrActionCreateInfo, actionType);
    P(XrActionCreateInfo, countSubactionPaths);
    P(XrActionCreateInfo, subactionPaths);
    P(XrActionCreateInfo, localizedActionName);

    S(XrActionSetCreateInfo);
    P(XrActionSetCreateInfo, type);
    P(XrActionSetCreateInfo, next);
    P(XrActionSetCreateInfo, actionSetName);
    P(XrActionSetCreateInfo, localizedActionSetName);
    P(XrActionSetCreateInfo, priority);

    S(XrActionSuggestedBinding);
    P(XrActionSuggestedBinding, action);
    P(XrActionSuggestedBinding, binding);

    S(XrInteractionProfileSuggestedBinding);
    P(XrInteractionProfileSuggestedBinding, interactionProfile);
    P(XrInteractionProfileSuggestedBinding, countSuggestedBindings);
    P(XrInteractionProfileSuggestedBinding, suggestedBindings);

    S(XrActiveActionSet);
    P(XrActiveActionSet, actionSet);
    P(XrActiveActionSet, subactionPath);

    S(XrActionsSyncInfo);
    P(XrActionsSyncInfo, countActiveActionSets);
    P(XrActionsSyncInfo, activeActionSets);

    S(XrActionStateGetInfo);
    P(XrActionStateGetInfo, action);
    P(XrActionStateGetInfo, subactionPath);

    S(XrActionStateBoolean);
    P(XrActionStateBoolean, currentState);
    P(XrActionStateBoolean, changedSinceLastSync);
    P(XrActionStateBoolean, lastChangeTime);
    P(XrActionStateBoolean, isActive);

    S(XrActionStateFloat);
    P(XrActionStateFloat, currentState);
    P(XrActionStateFloat, changedSinceLastSync);
    P(XrActionStateFloat, lastChangeTime);
    P(XrActionStateFloat, isActive);

    S(XrActionStateVector2f);
    P(XrActionStateVector2f, currentState);
    P(XrActionStateVector2f, changedSinceLastSync);
    P(XrActionStateVector2f, lastChangeTime);
    P(XrActionStateVector2f, isActive);

    S(XrApiLayerNextInfo);
    P(XrApiLayerNextInfo, structType);
    P(XrApiLayerNextInfo, structVersion);
    P(XrApiLayerNextInfo, structSize);
    P(XrApiLayerNextInfo, layerName);
    P(XrApiLayerNextInfo, nextGetInstanceProcAddr);
    P(XrApiLayerNextInfo, nextCreateApiLayerInstance);
    P(XrApiLayerNextInfo, next);

    S(XrApiLayerCreateInfo);
    P(XrApiLayerCreateInfo, structType);
    P(XrApiLayerCreateInfo, structVersion);
    P(XrApiLayerCreateInfo, structSize);
    P(XrApiLayerCreateInfo, loaderInstance);
    P(XrApiLayerCreateInfo, settings_file_location);
    P(XrApiLayerCreateInfo, nextInfo);

    S(XrNegotiateLoaderInfo);
    P(XrNegotiateLoaderInfo, structType);
    P(XrNegotiateLoaderInfo, structVersion);
    P(XrNegotiateLoaderInfo, structSize);
    P(XrNegotiateLoaderInfo, minInterfaceVersion);
    P(XrNegotiateLoaderInfo, maxInterfaceVersion);
    P(XrNegotiateLoaderInfo, minApiVersion);
    P(XrNegotiateLoaderInfo, maxApiVersion);

    S(XrNegotiateApiLayerRequest);
    P(XrNegotiateApiLayerRequest, structType);
    P(XrNegotiateApiLayerRequest, structVersion);
    P(XrNegotiateApiLayerRequest, structSize);
    P(XrNegotiateApiLayerRequest, layerInterfaceVersion);
    P(XrNegotiateApiLayerRequest, layerApiVersion);
    P(XrNegotiateApiLayerRequest, getInstanceProcAddr);
    P(XrNegotiateApiLayerRequest, createApiLayerInstance);

    S(XrResult);
    S(XrActionType);
    S(XrStructureType);
    S(XrLoaderInterfaceStructs);
    S(XrBool32);
    S(XrPath);
    S(XrTime);
    S(XrVersion);
    S(XrInstance);
    S(XrSession);
    S(XrAction);
    S(XrActionSet);
    S(XrVector2f);

    V(XR_SUCCESS);
    V(XR_ERROR_VALIDATION_FAILURE);
    V(XR_ERROR_RUNTIME_FAILURE);
    V(XR_ERROR_INITIALIZATION_FAILED);
    V(XR_ERROR_FUNCTION_UNSUPPORTED);
    V(XR_ERROR_HANDLE_INVALID);
    V(XR_ACTION_TYPE_BOOLEAN_INPUT);
    V(XR_ACTION_TYPE_FLOAT_INPUT);
    V(XR_ACTION_TYPE_VECTOR2F_INPUT);
    V(XR_ACTION_TYPE_POSE_INPUT);
    V(XR_ACTION_TYPE_VIBRATION_OUTPUT);
    V(XR_LOADER_INTERFACE_STRUCT_LOADER_INFO);
    V(XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST);
    V(XR_LOADER_INTERFACE_STRUCT_RUNTIME_REQUEST);
    V(XR_LOADER_INTERFACE_STRUCT_API_LAYER_CREATE_INFO);
    V(XR_LOADER_INTERFACE_STRUCT_API_LAYER_NEXT_INFO);
    V(XR_CURRENT_LOADER_API_LAYER_VERSION);
    V(XR_LOADER_INFO_STRUCT_VERSION);
    V(XR_API_LAYER_INFO_STRUCT_VERSION);
    V(XR_API_LAYER_NEXT_INFO_STRUCT_VERSION);
    V(XR_API_LAYER_CREATE_INFO_STRUCT_VERSION);
    V(XR_API_LAYER_MAX_SETTINGS_PATH_SIZE);
    V(XR_MAX_API_LAYER_NAME_SIZE);
    V(XR_MAX_APPLICATION_NAME_SIZE);
    V(XR_MAX_ENGINE_NAME_SIZE);
    V(XR_MAX_ACTION_SET_NAME_SIZE);
    V(XR_MAX_LOCALIZED_ACTION_SET_NAME_SIZE);
    V(XR_MAX_ACTION_NAME_SIZE);
    V(XR_MAX_LOCALIZED_ACTION_NAME_SIZE);
    V(XR_NULL_PATH);
    V(XR_TRUE);
    V(XR_FALSE);
    V(XR_MAKE_VERSION(1, 0, 0));
    V(XR_VERSION_MAJOR(XR_MAKE_VERSION(3, 2, 1)));
    V(XR_VERSION_MINOR(XR_MAKE_VERSION(3, 2, 1)));
    return 0;
}
