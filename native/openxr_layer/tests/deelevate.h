#pragma once

// The Khronos loader ignores XR_RUNTIME_JSON / XR_API_LAYER_PATH / XR_ENABLE_API_LAYERS and the per-user (HKCU)
// layer registrations in elevated processes. Games are not elevated, but CI runners are. So that the test
// clients see what a game sees, they relaunch themselves with a medium-integrity copy of their own token.

#include <windows.h>

#include <sddl.h>

#include <cstdio>
#include <string>
#include <vector>

namespace deelevate {

inline bool is_high_integrity(HANDLE token) {
    DWORD size = 0;
    GetTokenInformation(token, TokenIntegrityLevel, nullptr, 0, &size);
    if (size == 0) {
        return false;
    }
    std::vector<BYTE> buffer(size);
    if (!GetTokenInformation(token, TokenIntegrityLevel, buffer.data(), size, &size)) {
        return false;
    }
    const auto* label = reinterpret_cast<const TOKEN_MANDATORY_LABEL*>(buffer.data());
    const DWORD count = *GetSidSubAuthorityCount(label->Label.Sid);
    const DWORD rid = *GetSidSubAuthority(label->Label.Sid, count - 1);
    return rid >= SECURITY_MANDATORY_HIGH_RID;
}

// Returns true and sets `exit_code` if the work was done by a medium-integrity child process.
// Returns false if this process should simply carry on (not elevated, or the relaunch was not possible).
inline bool run_at_medium_integrity(int& exit_code) {
    constexpr char kGuard[] = "VRTREAD_TEST_DEELEVATED";
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ADJUST_DEFAULT | TOKEN_ASSIGN_PRIMARY, &token)) {
        return false;
    }
    const bool elevated = is_high_integrity(token);
    char guard[8]{};
    const bool already_relaunched = GetEnvironmentVariableA(kGuard, guard, sizeof(guard)) > 0;
    if (!elevated || already_relaunched) {
        if (elevated) {
            std::fprintf(stderr, "warning: still elevated after relaunch; the OpenXR loader will ignore the test environment\n");
        }
        CloseHandle(token);
        return false;
    }

    HANDLE medium = nullptr;
    PSID medium_sid = nullptr;
    bool launched = false;
    if (DuplicateTokenEx(token, MAXIMUM_ALLOWED, nullptr, SecurityImpersonation, TokenPrimary, &medium) &&
        ConvertStringSidToSidW(L"S-1-16-8192", &medium_sid)) {  // Mandatory Label\Medium
        TOKEN_MANDATORY_LABEL label{};
        label.Label.Attributes = SE_GROUP_INTEGRITY;
        label.Label.Sid = medium_sid;
        if (SetTokenInformation(medium, TokenIntegrityLevel, &label, sizeof(label) + GetLengthSid(medium_sid))) {
            SetEnvironmentVariableA(kGuard, "1");
            std::wstring command_line = GetCommandLineW();
            STARTUPINFOW startup{};
            startup.cb = sizeof(startup);
            startup.dwFlags = STARTF_USESTDHANDLES;
            startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
            startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
            startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
            PROCESS_INFORMATION process{};
            std::fflush(stdout);
            std::fflush(stderr);
            if (CreateProcessAsUserW(medium, nullptr, command_line.data(), nullptr, nullptr, TRUE, 0, nullptr, nullptr, &startup,
                                     &process)) {
                WaitForSingleObject(process.hProcess, INFINITE);
                DWORD code = 1;
                GetExitCodeProcess(process.hProcess, &code);
                exit_code = static_cast<int>(code);
                CloseHandle(process.hThread);
                CloseHandle(process.hProcess);
                launched = true;
            } else {
                std::fprintf(stderr, "warning: could not relaunch at medium integrity (error %lu)\n", GetLastError());
            }
        }
    }
    if (medium_sid != nullptr) {
        LocalFree(medium_sid);
    }
    if (medium != nullptr) {
        CloseHandle(medium);
    }
    CloseHandle(token);
    return launched;
}

}  // namespace deelevate
