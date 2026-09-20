#pragma once

// Pure decision logic for the VR Treadmill OpenXR layer.
//
// Nothing in here touches Windows or OpenXR, so it is unit-tested directly
// (tests/test_policy.cpp) as well as through the real loader.
//
// The question this file answers: "the game is reading action A for hand H;
// is that the player's left-thumbstick locomotion input?"  Engines differ a lot:
//
//   Unity    one generic action "thumbstick" with subaction paths for BOTH hands,
//            queried once per hand. Only the subaction path tells the hands apart.
//   Unreal   (legacy input) float actions such as "moveforward" bound to
//            .../thumbstick/y, usually queried with XR_NULL_PATH.
//   Unreal   (enhanced input) / Godot / native: vector2 actions bound to
//            .../thumbstick, with or without subaction paths.
//
// So the binding the game suggested is the primary evidence, the subaction path
// selects the hand, and the action name is only a tie-breaker / safety filter.

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <string>
#include <string_view>

#include "vrtread_shared_memory.h"

namespace vrtread::policy {

enum class Hand : uint8_t { None, Left, Right, Other };
enum class StickAxis : uint8_t { None, Vector, X, Y };
enum class ActionKind : uint8_t { Other, Float, Vector2 };

struct BindingClass {
    Hand hand = Hand::None;
    StickAxis axis = StickAxis::None;
    // Any component of a left-hand 2D stick (x, y, click, touch, dpad_*): all of them are the same
    // physical input source as far as action-set priority is concerned.
    bool on_left_stick_source = false;
};

struct NameTraits {
    bool locomotion = false;  // name suggests player movement
    bool excluded = false;    // name suggests menu / turn / teleport / UI: never drive
    bool y_axis_hint = false;  // float action name suggests a forward/back axis
};

struct ActionTraits {
    ActionKind kind = ActionKind::Other;
    NameTraits name;
    bool left_stick = false;   // bound to a left-hand 2D stick (matching axis kind) in some profile
    bool right_stick = false;  // same, right hand
    bool any_binding = false;  // at least one suggested binding was seen for this action
    bool uses_left_stick_source = false;  // bound to any component of the left stick (any action type)
};

enum class Decision : uint8_t {
    Drive,
    SkipKind,              // not a float / vector2 input
    SkipExcludedName,      // looks like menu / turn / teleport / UI
    SkipRightHand,         // queried for the right hand
    SkipOtherSubaction,    // queried for head / gamepad / treadmill / ...
    SkipNotLeftStick,      // has bindings, none of them is the left stick
    SkipAmbiguousHands,    // null subaction, bound to both sticks, name is not locomotion
    SkipStrictName,        // strict filter and the name does not say locomotion
    SkipNoBindings,        // no bindings seen and compatibility rules not met
};

inline const char* to_string(Decision decision) {
    switch (decision) {
        case Decision::Drive: return "drive";
        case Decision::SkipKind: return "skip:not-an-axis";
        case Decision::SkipExcludedName: return "skip:menu-or-turn-name";
        case Decision::SkipRightHand: return "skip:right-hand";
        case Decision::SkipOtherSubaction: return "skip:other-subaction";
        case Decision::SkipNotLeftStick: return "skip:not-left-stick";
        case Decision::SkipAmbiguousHands: return "skip:both-hands-ambiguous";
        case Decision::SkipStrictName: return "skip:strict-name";
        case Decision::SkipNoBindings: return "skip:no-bindings";
    }
    return "skip:unknown";
}

inline std::string lower_copy(std::string_view value) {
    std::string out(value);
    std::transform(out.begin(), out.end(), out.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return out;
}

inline bool starts_with(std::string_view value, std::string_view prefix) {
    return value.size() >= prefix.size() && value.compare(0, prefix.size(), prefix) == 0;
}

inline bool contains_any(std::string_view haystack, std::initializer_list<std::string_view> needles) {
    for (const std::string_view needle : needles) {
        if (haystack.find(needle) != std::string_view::npos) {
            return true;
        }
    }
    return false;
}

// True if any maximal run of [a-z0-9] in `value` equals one of `tokens`.
inline bool has_token(std::string_view value, std::initializer_list<std::string_view> tokens) {
    size_t index = 0;
    while (index < value.size()) {
        while (index < value.size() && !std::isalnum(static_cast<unsigned char>(value[index]))) {
            ++index;
        }
        const size_t start = index;
        while (index < value.size() && std::isalnum(static_cast<unsigned char>(value[index]))) {
            ++index;
        }
        const std::string_view token = value.substr(start, index - start);
        for (const std::string_view candidate : tokens) {
            if (!token.empty() && token == candidate) {
                return true;
            }
        }
    }
    return false;
}

inline Hand classify_user_path(std::string_view path_string) {
    const std::string path = lower_copy(path_string);
    if (path == "/user/hand/left") {
        return Hand::Left;
    }
    if (path == "/user/hand/right") {
        return Hand::Right;
    }
    return path.empty() ? Hand::None : Hand::Other;
}

// "/user/hand/left/input/thumbstick"   -> {Left, Vector}
// "/user/hand/left/input/thumbstick/y" -> {Left, Y}
// "/user/hand/left/input/trackpad"     -> {Left, Vector}   (SteamVR maps Vive trackpad bindings onto Touch sticks)
// "/user/hand/left/input/thumbstick/click", ".../trigger/value", "/user/gamepad/..." -> axis None
inline BindingClass classify_binding_path(std::string_view path_string) {
    const std::string path = lower_copy(path_string);
    BindingClass result;

    constexpr std::string_view kLeft = "/user/hand/left/";
    constexpr std::string_view kRight = "/user/hand/right/";
    size_t rest_offset = 0;
    if (starts_with(path, kLeft)) {
        result.hand = Hand::Left;
        rest_offset = kLeft.size();
    } else if (starts_with(path, kRight)) {
        result.hand = Hand::Right;
        rest_offset = kRight.size();
    } else {
        result.hand = Hand::Other;
        return result;
    }

    const std::string_view rest = std::string_view(path).substr(rest_offset);
    constexpr std::string_view kInput = "input/";
    if (!starts_with(rest, kInput)) {
        return result;
    }
    const std::string_view source = rest.substr(kInput.size());
    const size_t slash = source.find('/');
    const std::string_view identifier = source.substr(0, slash);
    const std::string_view component = slash == std::string_view::npos ? std::string_view{} : source.substr(slash + 1);

    if (identifier != "thumbstick" && identifier != "trackpad" && identifier != "joystick") {
        return result;
    }
    result.on_left_stick_source = result.hand == Hand::Left;
    if (component.empty()) {
        result.axis = StickAxis::Vector;
    } else if (component == "x") {
        result.axis = StickAxis::X;
    } else if (component == "y") {
        result.axis = StickAxis::Y;
    }
    return result;
}

inline NameTraits classify_name(std::string_view action_name, std::string_view localized_name) {
    std::string combined = lower_copy(action_name);
    combined.push_back(' ');
    combined += lower_copy(localized_name);

    NameTraits traits;
    // Long, unambiguous words match as substrings ("snap_turn", "TeleportAim").
    // Short, ambiguous words only match as whole tokens ("ui", not "quit" or "build").
    traits.excluded =
        contains_any(combined,
                     {"menu", "teleport", "turn", "rotat", "snap", "scroll", "inventory", "cursor", "pointer", "laser",
                      "select", "navigat", "dashboard", "pause", "zoom", "camera", "look", "radial", "wheel",
                      "settings", "option", "system"}) ||
        has_token(combined, {"ui", "gui", "hud", "map"});

    traits.locomotion = contains_any(
        combined, {"move", "locomot", "walk", "run", "sprint", "strafe", "forward", "backward", "stick", "thumb",
                   "axis", "direction", "drive", "throttle", "fly", "swim", "travel", "motion"});

    traits.y_axis_hint = contains_any(combined, {"forward", "backward", "vertical"}) || has_token(combined, {"y"});
    return traits;
}

inline void note_binding(ActionTraits& traits, BindingClass binding) {
    traits.any_binding = true;
    traits.uses_left_stick_source = traits.uses_left_stick_source || binding.on_left_stick_source;
    const bool axis_matches = (traits.kind == ActionKind::Vector2 && binding.axis == StickAxis::Vector) ||
                              (traits.kind == ActionKind::Float && binding.axis == StickAxis::Y);
    if (!axis_matches) {
        return;
    }
    if (binding.hand == Hand::Left) {
        traits.left_stick = true;
    } else if (binding.hand == Hand::Right) {
        traits.right_stick = true;
    }
}

// `query_hand` is Hand::None when the game passed XR_NULL_PATH (combined state of every bound source).
inline Decision decide(const ActionTraits& traits, Hand query_hand, uint32_t filter_mode) {
    if (traits.kind == ActionKind::Other) {
        return Decision::SkipKind;
    }
    if (traits.name.excluded) {
        return Decision::SkipExcludedName;
    }
    if (query_hand == Hand::Right) {
        return Decision::SkipRightHand;
    }
    if (query_hand == Hand::Other) {
        return Decision::SkipOtherSubaction;
    }

    const bool strict = filter_mode == kFilterStrict;
    const bool compatibility = filter_mode == kFilterCompatibility;

    if (!traits.any_binding) {
        // The game never suggested bindings for this action (or only for profiles we did not see).
        const bool named = traits.name.locomotion && (traits.kind == ActionKind::Vector2 || traits.name.y_axis_hint);
        return compatibility && named ? Decision::Drive : Decision::SkipNoBindings;
    }
    if (!traits.left_stick) {
        return Decision::SkipNotLeftStick;
    }
    if (query_hand == Hand::None && traits.right_stick && !traits.name.locomotion) {
        // Combined read of an action on both sticks with a generic name: cannot tell what it is for.
        return compatibility ? Decision::Drive : Decision::SkipAmbiguousHands;
    }
    if (strict && !traits.name.locomotion) {
        return Decision::SkipStrictName;
    }
    return Decision::Drive;
}

inline bool is_usable(float value) {
    return std::isfinite(value);
}

inline float clamp_unit(float value) {
    return value < -1.0F ? -1.0F : (value > 1.0F ? 1.0F : value);
}

inline float combine_axis(float physical, float treadmill, uint32_t combine_mode) {
    switch (combine_mode) {
        case kCombineReplace:
            return clamp_unit(treadmill);
        case kCombineAdd:
            return clamp_unit(physical + treadmill);
        case kCombineMaxMagnitude:
        default:
            return std::fabs(treadmill) >= std::fabs(physical) ? clamp_unit(treadmill) : physical;
    }
}

// Real thumbsticks report a vector inside the unit circle; keep that true after injecting Y.
inline void clamp_to_unit_circle(float& x, float& y) {
    const float length_squared = x * x + y * y;
    if (length_squared > 1.0F) {
        const float scale = 1.0F / std::sqrt(length_squared);
        x *= scale;
        y *= scale;
    }
}

}  // namespace vrtread::policy
