#pragma once

// Minimal dependency-free test framework: TEST(name) { CHECK(...); REQUIRE(...); }

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

namespace testing {

struct TestCase {
    const char* name;
    std::function<void()> body;
};

inline std::vector<TestCase>& registry() {
    static std::vector<TestCase> tests;
    return tests;
}

struct Registrar {
    Registrar(const char* name, std::function<void()> body) { registry().push_back({name, std::move(body)}); }
};

struct RequireFailed : std::runtime_error {
    using std::runtime_error::runtime_error;
};

inline int& failures_in_current_test() {
    static int failures = 0;
    return failures;
}

inline void report_failure(const char* file, int line, const std::string& message) {
    ++failures_in_current_test();
    std::printf("    FAILED %s:%d\n      %s\n", file, line, message.c_str());
}

inline bool approx(double a, double b, double tolerance = 1e-5) {
    return std::fabs(a - b) <= tolerance;
}

// Runs every registered test whose name contains `filter` (empty = all), sorted by name so the order is
// the same in every build. Returns the number of failed tests.
inline int run_all(const std::string& filter) {
    std::vector<TestCase> tests = registry();
    std::sort(tests.begin(), tests.end(),
              [](const TestCase& a, const TestCase& b) { return std::string(a.name) < std::string(b.name); });
    int failed = 0;
    int ran = 0;
    for (const TestCase& test : tests) {
        if (!filter.empty() && std::string(test.name).find(filter) == std::string::npos) {
            continue;
        }
        ++ran;
        failures_in_current_test() = 0;
        std::printf("[ RUN  ] %s\n", test.name);
        std::fflush(stdout);
        try {
            test.body();
        } catch (const RequireFailed&) {
        } catch (const std::exception& error) {
            report_failure("<exception>", 0, error.what());
        } catch (...) {
            report_failure("<exception>", 0, "unknown exception");
        }
        if (failures_in_current_test() == 0) {
            std::printf("[  OK  ] %s\n", test.name);
        } else {
            std::printf("[ FAIL ] %s\n", test.name);
            ++failed;
        }
        std::fflush(stdout);
    }
    std::printf("\n%d test(s) run, %d failed\n", ran, failed);
    return failed;
}

}  // namespace testing

#define TEST(name)                                                        \
    static void test_body_##name();                                       \
    static ::testing::Registrar test_registrar_##name(#name, test_body_##name); \
    static void test_body_##name()

#define CHECK(condition)                                                          \
    do {                                                                          \
        if (!(condition)) {                                                       \
            ::testing::report_failure(__FILE__, __LINE__, "CHECK(" #condition ")"); \
        }                                                                         \
    } while (0)

#define REQUIRE(condition)                                                          \
    do {                                                                            \
        if (!(condition)) {                                                         \
            ::testing::report_failure(__FILE__, __LINE__, "REQUIRE(" #condition ")"); \
            throw ::testing::RequireFailed("require failed");                       \
        }                                                                           \
    } while (0)

#define CHECK_NEAR(actual, expected)                                                                        \
    do {                                                                                                    \
        const double check_actual = static_cast<double>(actual);                                            \
        const double check_expected = static_cast<double>(expected);                                        \
        if (!::testing::approx(check_actual, check_expected)) {                                             \
            ::testing::report_failure(__FILE__, __LINE__,                                                   \
                                      std::string("CHECK_NEAR(" #actual ", " #expected ") actual=") +       \
                                          std::to_string(check_actual) + " expected=" + std::to_string(check_expected)); \
        }                                                                                                   \
    } while (0)

#define CHECK_XR(expression)                                                                                   \
    do {                                                                                                       \
        const XrResult check_result = (expression);                                                            \
        if (check_result != XR_SUCCESS) {                                                                      \
            ::testing::report_failure(__FILE__, __LINE__,                                                      \
                                      std::string("CHECK_XR(" #expression ") = ") + std::to_string(check_result)); \
        }                                                                                                      \
    } while (0)

#define REQUIRE_XR(expression)                                                                                   \
    do {                                                                                                         \
        const XrResult require_result = (expression);                                                            \
        if (require_result != XR_SUCCESS) {                                                                      \
            ::testing::report_failure(__FILE__, __LINE__,                                                        \
                                      std::string("REQUIRE_XR(" #expression ") = ") + std::to_string(require_result)); \
            throw ::testing::RequireFailed("require failed");                                                    \
        }                                                                                                        \
    } while (0)
