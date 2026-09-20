// A genuine 32-bit OpenXR client. It gives the loader nothing but a runtime: API layers are whatever the real
// registry says, exactly as for a 32-bit game. Prints one JSON object describing what happened.

#include <windows.h>

#include <openxr/openxr.h>

#include <cstdio>
#include <cstring>

int main() {
    SetEnvironmentVariableA("XR_RUNTIME_JSON", VRTREAD_RUNTIME32_JSON);
    SetEnvironmentVariableA("XR_API_LAYER_PATH", nullptr);
    SetEnvironmentVariableA("XR_ENABLE_API_LAYERS", nullptr);

    char wow64[64]{};
    GetEnvironmentVariableA("PROCESSOR_ARCHITEW6432", wow64, sizeof(wow64));

    XrInstanceCreateInfo info{XR_TYPE_INSTANCE_CREATE_INFO};
    strncpy_s(info.applicationInfo.applicationName, "VRTreadmill32BitGame", _TRUNCATE);
    strncpy_s(info.applicationInfo.engineName, "Probe", _TRUNCATE);
    info.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);
    XrInstance instance = XR_NULL_HANDLE;
    const XrResult result = xrCreateInstance(&info, &instance);
    const bool layer_loaded = GetModuleHandleA("vrtread_openxr_layer.dll") != nullptr;

    std::printf("{\"pointer_bits\":%u,\"processor_architew6432\":\"%s\",\"create_result\":%d,\"layer_loaded\":%s}\n",
                static_cast<unsigned>(sizeof(void*) * 8), wow64, static_cast<int>(result), layer_loaded ? "true" : "false");
    if (instance != XR_NULL_HANDLE) {
        xrDestroyInstance(instance);
    }
    return 0;
}
