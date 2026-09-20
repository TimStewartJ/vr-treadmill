#pragma once

// Minimal, ABI-exact subset of the OpenXR headers used by the VR Treadmill API layer.
//
// The shipping DLL is built against this file so that the layer build stays hermetic
// (no SDK download, no vendored 700 KB header). tests/abi_probe.cpp compiles the same
// sizeof/offsetof/constant probe against this header and against the official Khronos
// headers and fails the build if they ever differ.

#include <cstddef>
#include <cstdint>

#if defined(_WIN32)
#define XRAPI_ATTR
#define XRAPI_CALL __stdcall
#define XRAPI_PTR XRAPI_CALL
#else
#define XRAPI_ATTR
#define XRAPI_CALL
#define XRAPI_PTR
#endif

#define XR_MAY_ALIAS
#define XR_MAKE_VERSION(major, minor, patch) \
    ((((major)&0xffffULL) << 48) | (((minor)&0xffffULL) << 32) | ((patch)&0xffffffffULL))
#define XR_VERSION_MAJOR(version) (uint16_t)(((uint64_t)(version) >> 48) & 0xffffULL)
#define XR_VERSION_MINOR(version) (uint16_t)(((uint64_t)(version) >> 32) & 0xffffULL)
#define XR_API_VERSION_1_0 XR_MAKE_VERSION(1, 0, 0)
#define XR_CURRENT_LOADER_API_LAYER_VERSION 1
#define XR_LOADER_INFO_STRUCT_VERSION 1
#define XR_API_LAYER_INFO_STRUCT_VERSION 1
#define XR_API_LAYER_NEXT_INFO_STRUCT_VERSION 1
#define XR_API_LAYER_CREATE_INFO_STRUCT_VERSION 1
#define XR_API_LAYER_MAX_SETTINGS_PATH_SIZE 512
#define XR_MAX_API_LAYER_NAME_SIZE 256
#define XR_MAX_APPLICATION_NAME_SIZE 128
#define XR_MAX_ENGINE_NAME_SIZE 128
#define XR_MAX_ACTION_SET_NAME_SIZE 64
#define XR_MAX_LOCALIZED_ACTION_SET_NAME_SIZE 128
#define XR_MAX_ACTION_NAME_SIZE 64
#define XR_MAX_LOCALIZED_ACTION_NAME_SIZE 128
#define XR_NULL_PATH 0
#define XR_NULL_HANDLE nullptr

typedef uint64_t XrVersion;
typedef uint64_t XrFlags64;
typedef uint64_t XrSystemId;
typedef uint64_t XrPath;
typedef uint32_t XrBool32;
typedef int64_t XrTime;
typedef XrFlags64 XrInstanceCreateFlags;

typedef struct XrInstance_T* XrInstance;
typedef struct XrSession_T* XrSession;
typedef struct XrActionSet_T* XrActionSet;
typedef struct XrAction_T* XrAction;

typedef void(XRAPI_PTR* PFN_xrVoidFunction)(void);

typedef enum XrResult {
    XR_SUCCESS = 0,
    XR_ERROR_VALIDATION_FAILURE = -1,
    XR_ERROR_RUNTIME_FAILURE = -2,
    XR_ERROR_INITIALIZATION_FAILED = -6,
    XR_ERROR_FUNCTION_UNSUPPORTED = -7,
    XR_ERROR_HANDLE_INVALID = -12,
    XR_RESULT_MAX_ENUM = 0x7FFFFFFF
} XrResult;

#define XR_SUCCEEDED(result) ((result) >= 0)
#define XR_FAILED(result) ((result) < 0)
#define XR_TRUE 1
#define XR_FALSE 0

typedef enum XrStructureType {
    XR_TYPE_UNKNOWN = 0,
    XR_STRUCTURE_TYPE_MAX_ENUM = 0x7FFFFFFF
} XrStructureType;

typedef enum XrActionType {
    XR_ACTION_TYPE_BOOLEAN_INPUT = 1,
    XR_ACTION_TYPE_FLOAT_INPUT = 2,
    XR_ACTION_TYPE_VECTOR2F_INPUT = 3,
    XR_ACTION_TYPE_POSE_INPUT = 4,
    XR_ACTION_TYPE_VIBRATION_OUTPUT = 100,
    XR_ACTION_TYPE_MAX_ENUM = 0x7FFFFFFF
} XrActionType;

typedef struct XrVector2f {
    float x;
    float y;
} XrVector2f;

typedef struct XrApplicationInfo {
    char applicationName[XR_MAX_APPLICATION_NAME_SIZE];
    uint32_t applicationVersion;
    char engineName[XR_MAX_ENGINE_NAME_SIZE];
    uint32_t engineVersion;
    XrVersion apiVersion;
} XrApplicationInfo;

typedef struct XrInstanceCreateInfo {
    XrStructureType type;
    const void* next;
    XrInstanceCreateFlags createFlags;
    XrApplicationInfo applicationInfo;
    uint32_t enabledApiLayerCount;
    const char* const* enabledApiLayerNames;
    uint32_t enabledExtensionCount;
    const char* const* enabledExtensionNames;
} XrInstanceCreateInfo;

// Only ever handled by pointer; the layer never reads its fields.
typedef struct XrSessionCreateInfo XrSessionCreateInfo;

typedef struct XrActionCreateInfo {
    XrStructureType type;
    const void* next;
    char actionName[XR_MAX_ACTION_NAME_SIZE];
    XrActionType actionType;
    uint32_t countSubactionPaths;
    const XrPath* subactionPaths;
    char localizedActionName[XR_MAX_LOCALIZED_ACTION_NAME_SIZE];
} XrActionCreateInfo;

typedef struct XrActionSetCreateInfo {
    XrStructureType type;
    const void* next;
    char actionSetName[XR_MAX_ACTION_SET_NAME_SIZE];
    char localizedActionSetName[XR_MAX_LOCALIZED_ACTION_SET_NAME_SIZE];
    uint32_t priority;
} XrActionSetCreateInfo;

typedef struct XrActionSuggestedBinding {
    XrAction action;
    XrPath binding;
} XrActionSuggestedBinding;

typedef struct XrInteractionProfileSuggestedBinding {
    XrStructureType type;
    const void* next;
    XrPath interactionProfile;
    uint32_t countSuggestedBindings;
    const XrActionSuggestedBinding* suggestedBindings;
} XrInteractionProfileSuggestedBinding;

typedef struct XrActiveActionSet {
    XrActionSet actionSet;
    XrPath subactionPath;
} XrActiveActionSet;

typedef struct XrActionsSyncInfo {
    XrStructureType type;
    const void* next;
    uint32_t countActiveActionSets;
    const XrActiveActionSet* activeActionSets;
} XrActionsSyncInfo;

typedef struct XrActionStateGetInfo {
    XrStructureType type;
    const void* next;
    XrAction action;
    XrPath subactionPath;
} XrActionStateGetInfo;

typedef struct XrActionStateBoolean {
    XrStructureType type;
    void* next;
    XrBool32 currentState;
    XrBool32 changedSinceLastSync;
    XrTime lastChangeTime;
    XrBool32 isActive;
} XrActionStateBoolean;

typedef struct XrActionStateFloat {
    XrStructureType type;
    void* next;
    float currentState;
    XrBool32 changedSinceLastSync;
    XrTime lastChangeTime;
    XrBool32 isActive;
} XrActionStateFloat;

typedef struct XrActionStateVector2f {
    XrStructureType type;
    void* next;
    XrVector2f currentState;
    XrBool32 changedSinceLastSync;
    XrTime lastChangeTime;
    XrBool32 isActive;
} XrActionStateVector2f;

typedef XrResult(XRAPI_PTR* PFN_xrGetInstanceProcAddr)(
    XrInstance instance,
    const char* name,
    PFN_xrVoidFunction* function);
typedef XrResult(XRAPI_PTR* PFN_xrDestroyInstance)(XrInstance instance);
typedef XrResult(XRAPI_PTR* PFN_xrCreateSession)(
    XrInstance instance,
    const XrSessionCreateInfo* createInfo,
    XrSession* session);
typedef XrResult(XRAPI_PTR* PFN_xrDestroySession)(XrSession session);
typedef XrResult(XRAPI_PTR* PFN_xrStringToPath)(
    XrInstance instance,
    const char* pathString,
    XrPath* path);
typedef XrResult(XRAPI_PTR* PFN_xrPathToString)(
    XrInstance instance,
    XrPath path,
    uint32_t bufferCapacityInput,
    uint32_t* bufferCountOutput,
    char* buffer);
typedef XrResult(XRAPI_PTR* PFN_xrCreateActionSet)(
    XrInstance instance,
    const XrActionSetCreateInfo* createInfo,
    XrActionSet* actionSet);
typedef XrResult(XRAPI_PTR* PFN_xrDestroyActionSet)(XrActionSet actionSet);
typedef XrResult(XRAPI_PTR* PFN_xrCreateAction)(
    XrActionSet actionSet,
    const XrActionCreateInfo* createInfo,
    XrAction* action);
typedef XrResult(XRAPI_PTR* PFN_xrDestroyAction)(XrAction action);
typedef XrResult(XRAPI_PTR* PFN_xrSuggestInteractionProfileBindings)(
    XrInstance instance,
    const XrInteractionProfileSuggestedBinding* suggestedBindings);
typedef XrResult(XRAPI_PTR* PFN_xrSyncActions)(XrSession session, const XrActionsSyncInfo* syncInfo);
typedef XrResult(XRAPI_PTR* PFN_xrGetActionStateBoolean)(
    XrSession session,
    const XrActionStateGetInfo* getInfo,
    XrActionStateBoolean* state);
typedef XrResult(XRAPI_PTR* PFN_xrGetActionStateFloat)(
    XrSession session,
    const XrActionStateGetInfo* getInfo,
    XrActionStateFloat* state);
typedef XrResult(XRAPI_PTR* PFN_xrGetActionStateVector2f)(
    XrSession session,
    const XrActionStateGetInfo* getInfo,
    XrActionStateVector2f* state);

typedef enum XrLoaderInterfaceStructs {
    XR_LOADER_INTERFACE_STRUCT_UNINTIALIZED = 0,
    XR_LOADER_INTERFACE_STRUCT_LOADER_INFO = 1,
    XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST = 2,
    XR_LOADER_INTERFACE_STRUCT_RUNTIME_REQUEST = 3,
    XR_LOADER_INTERFACE_STRUCT_API_LAYER_CREATE_INFO = 4,
    XR_LOADER_INTERFACE_STRUCT_API_LAYER_NEXT_INFO = 5,
    XR_LOADER_INTERFACE_STRUCTS_MAX_ENUM = 0x7FFFFFFF
} XrLoaderInterfaceStructs;

typedef struct XrApiLayerCreateInfo XrApiLayerCreateInfo;
typedef XrResult(XRAPI_PTR* PFN_xrCreateApiLayerInstance)(
    const XrInstanceCreateInfo* info,
    const XrApiLayerCreateInfo* apiLayerInfo,
    XrInstance* instance);

typedef struct XrApiLayerNextInfo {
    XrLoaderInterfaceStructs structType;
    uint32_t structVersion;
    size_t structSize;
    char layerName[XR_MAX_API_LAYER_NAME_SIZE];
    PFN_xrGetInstanceProcAddr nextGetInstanceProcAddr;
    PFN_xrCreateApiLayerInstance nextCreateApiLayerInstance;
    struct XrApiLayerNextInfo* next;
} XrApiLayerNextInfo;

typedef struct XrApiLayerCreateInfo {
    XrLoaderInterfaceStructs structType;
    uint32_t structVersion;
    size_t structSize;
    void* XR_MAY_ALIAS loaderInstance;
    char settings_file_location[XR_API_LAYER_MAX_SETTINGS_PATH_SIZE];
    XrApiLayerNextInfo* nextInfo;
} XrApiLayerCreateInfo;

typedef struct XrNegotiateLoaderInfo {
    XrLoaderInterfaceStructs structType;
    uint32_t structVersion;
    size_t structSize;
    uint32_t minInterfaceVersion;
    uint32_t maxInterfaceVersion;
    XrVersion minApiVersion;
    XrVersion maxApiVersion;
} XrNegotiateLoaderInfo;

typedef struct XrNegotiateApiLayerRequest {
    XrLoaderInterfaceStructs structType;
    uint32_t structVersion;
    size_t structSize;
    uint32_t layerInterfaceVersion;
    XrVersion layerApiVersion;
    PFN_xrGetInstanceProcAddr getInstanceProcAddr;
    PFN_xrCreateApiLayerInstance createApiLayerInstance;
} XrNegotiateApiLayerRequest;
