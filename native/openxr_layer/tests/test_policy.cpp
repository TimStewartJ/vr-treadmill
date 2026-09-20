// Unit tests for the pure decision logic (no OpenXR, no Windows).

#include "test_framework.h"
#include "vrtread_policy.h"

using namespace vrtread;
using namespace vrtread::policy;

namespace {

ActionTraits make_traits(ActionKind kind, const char* name, std::initializer_list<const char*> binding_paths,
                         const char* localized = "") {
    ActionTraits traits;
    traits.kind = kind;
    traits.name = classify_name(name, localized);
    for (const char* path : binding_paths) {
        note_binding(traits, classify_binding_path(path));
    }
    return traits;
}

}  // namespace

TEST(policy_binding_paths_are_classified) {
    BindingClass b = classify_binding_path("/user/hand/left/input/thumbstick");
    CHECK(b.hand == Hand::Left && b.axis == StickAxis::Vector);

    b = classify_binding_path("/user/hand/left/input/thumbstick/y");
    CHECK(b.hand == Hand::Left && b.axis == StickAxis::Y);

    b = classify_binding_path("/user/hand/left/input/thumbstick/x");
    CHECK(b.hand == Hand::Left && b.axis == StickAxis::X);

    b = classify_binding_path("/user/hand/right/input/thumbstick");
    CHECK(b.hand == Hand::Right && b.axis == StickAxis::Vector);

    // Vive wand bindings are served by the Touch thumbstick under SteamVR.
    b = classify_binding_path("/user/hand/left/input/trackpad");
    CHECK(b.hand == Hand::Left && b.axis == StickAxis::Vector);

    // Not an axis of a 2D stick.
    CHECK(classify_binding_path("/user/hand/left/input/thumbstick/click").axis == StickAxis::None);
    CHECK(classify_binding_path("/user/hand/left/input/thumbstick/touch").axis == StickAxis::None);
    CHECK(classify_binding_path("/user/hand/left/input/thumbstick/dpad_up").axis == StickAxis::None);
    CHECK(classify_binding_path("/user/hand/left/input/trigger/value").axis == StickAxis::None);
    CHECK(classify_binding_path("/user/hand/left/input/squeeze/value").axis == StickAxis::None);
    CHECK(classify_binding_path("/user/hand/left/output/haptic").axis == StickAxis::None);

    // Must not be fooled by prefixes / other top-level users.
    CHECK(classify_binding_path("/user/gamepad/input/thumbstick_left").hand == Hand::Other);
    CHECK(classify_binding_path("/user/gamepad/input/thumbstick_left/y").axis == StickAxis::None);
    CHECK(classify_binding_path("/user/hand/left/input/thumbstick_extra").axis == StickAxis::None);
    CHECK(classify_binding_path("/user/hand/leftish/input/thumbstick").hand == Hand::Other);
    CHECK(classify_binding_path("").hand == Hand::Other);

    // Case-insensitive, although OpenXR paths are lowercase by spec.
    CHECK(classify_binding_path("/User/Hand/Left/Input/Thumbstick/Y").axis == StickAxis::Y);
}

TEST(policy_user_paths_are_classified) {
    CHECK(classify_user_path("/user/hand/left") == Hand::Left);
    CHECK(classify_user_path("/user/hand/right") == Hand::Right);
    CHECK(classify_user_path("/user/head") == Hand::Other);
    CHECK(classify_user_path("/user/gamepad") == Hand::Other);
    CHECK(classify_user_path("") == Hand::None);
}

TEST(policy_names_locomotion) {
    for (const char* name : {"move", "movement", "locomotion", "player_move", "moveforward", "walk", "smooth_locomotion",
                             "thumbstick", "left_stick", "primary2daxis", "strafe", "run", "fly", "axis1"}) {
        const NameTraits traits = classify_name(name, "");
        if (!traits.locomotion || traits.excluded) {
            testing::report_failure(__FILE__, __LINE__, std::string("expected locomotion, not excluded: ") + name);
        }
    }
    CHECK(classify_name("ia_move", "IA_Move").locomotion);
    CHECK(classify_name("a1", "Move Forward / Back").locomotion);  // only the localized name is meaningful
}

TEST(policy_names_excluded) {
    for (const char* name : {"menu_navigate", "ui_scroll", "ui", "snap_turn", "smoothturn", "turn", "rotate_player",
                             "teleport", "teleport_direction", "inventory_scroll", "weapon_wheel", "radialmenu",
                             "move_cursor", "laser_pointer", "map_pan", "camera_zoom", "look", "hud_select",
                             "system_menu", "pause"}) {
        if (!classify_name(name, "").excluded) {
            testing::report_failure(__FILE__, __LINE__, std::string("expected excluded: ") + name);
        }
    }
    CHECK(classify_name("a7", "Open Menu").excluded);
}

TEST(policy_names_no_false_exclusions) {
    // Short ambiguous words only match as whole tokens.
    for (const char* name : {"move", "quit_moving", "build_move", "guide_move", "remap_move", "bitmap_axis", "fluid_move",
                             "thumbstick", "primary", "movement_axis", "suit_thrust", "equip_move"}) {
        if (classify_name(name, "").excluded) {
            testing::report_failure(__FILE__, __LINE__, std::string("must not be excluded: ") + name);
        }
    }
}

TEST(policy_names_y_axis_hint) {
    CHECK(classify_name("moveforward", "").y_axis_hint);
    CHECK(classify_name("move_y", "").y_axis_hint);
    CHECK(classify_name("motioncontroller_left_thumbstick_y", "").y_axis_hint);
    CHECK(classify_name("a", "Thumbstick Y").y_axis_hint);
    CHECK(classify_name("vertical", "").y_axis_hint);
    CHECK(!classify_name("moveright", "").y_axis_hint);
    CHECK(!classify_name("move_x", "").y_axis_hint);
    CHECK(!classify_name("yaw", "").y_axis_hint);  // 'y' must be a whole token
}

TEST(policy_unity_generic_thumbstick_per_hand) {
    // Unity: one "thumbstick" action bound on both hands, read once per hand.
    const ActionTraits traits = make_traits(ActionKind::Vector2, "thumbstick",
                                            {"/user/hand/left/input/thumbstick", "/user/hand/right/input/thumbstick"});
    for (const uint32_t mode : {kFilterBalanced, kFilterStrict, kFilterCompatibility}) {
        CHECK(decide(traits, Hand::Left, mode) == Decision::Drive);
        CHECK(decide(traits, Hand::Right, mode) == Decision::SkipRightHand);
        CHECK(decide(traits, Hand::Other, mode) == Decision::SkipOtherSubaction);
    }
}

TEST(policy_godot_generic_name) {
    const ActionTraits traits = make_traits(ActionKind::Vector2, "primary",
                                            {"/user/hand/left/input/thumbstick", "/user/hand/right/input/thumbstick"});
    CHECK(decide(traits, Hand::Left, kFilterBalanced) == Decision::Drive);
    CHECK(decide(traits, Hand::Left, kFilterStrict) == Decision::SkipStrictName);
    CHECK(decide(traits, Hand::Right, kFilterBalanced) == Decision::SkipRightHand);
    // Combined read of a generically named action on both sticks: could be anything.
    CHECK(decide(traits, Hand::None, kFilterBalanced) == Decision::SkipAmbiguousHands);
    CHECK(decide(traits, Hand::None, kFilterCompatibility) == Decision::Drive);
}

TEST(policy_native_move_left_only_null_subaction) {
    const ActionTraits traits = make_traits(ActionKind::Vector2, "move", {"/user/hand/left/input/thumbstick"});
    for (const uint32_t mode : {kFilterBalanced, kFilterStrict, kFilterCompatibility}) {
        CHECK(decide(traits, Hand::None, mode) == Decision::Drive);
        CHECK(decide(traits, Hand::Left, mode) == Decision::Drive);
    }
}

TEST(policy_move_bound_to_both_sticks_null_subaction) {
    const ActionTraits traits = make_traits(ActionKind::Vector2, "move",
                                            {"/user/hand/left/input/thumbstick", "/user/hand/right/input/thumbstick"});
    CHECK(decide(traits, Hand::None, kFilterBalanced) == Decision::Drive);
    CHECK(decide(traits, Hand::None, kFilterStrict) == Decision::Drive);
}

TEST(policy_unreal_float_axes) {
    const ActionTraits forward = make_traits(ActionKind::Float, "moveforward", {"/user/hand/left/input/thumbstick/y"});
    const ActionTraits sideways = make_traits(ActionKind::Float, "moveright", {"/user/hand/left/input/thumbstick/x"});
    const ActionTraits turn = make_traits(ActionKind::Float, "turn", {"/user/hand/right/input/thumbstick/x"});
    const ActionTraits trigger = make_traits(ActionKind::Float, "fire", {"/user/hand/left/input/trigger/value"});
    for (const uint32_t mode : {kFilterBalanced, kFilterStrict, kFilterCompatibility}) {
        CHECK(decide(forward, Hand::None, mode) == Decision::Drive);
        CHECK(decide(sideways, Hand::None, mode) == Decision::SkipNotLeftStick);
        CHECK(decide(turn, Hand::None, mode) == Decision::SkipExcludedName);
        CHECK(decide(trigger, Hand::None, mode) == Decision::SkipNotLeftStick);
    }
}

TEST(policy_float_bound_to_whole_stick_is_not_an_axis) {
    // A float action on the stick's parent path has no defined axis; do not guess.
    const ActionTraits traits = make_traits(ActionKind::Float, "move", {"/user/hand/left/input/thumbstick"});
    CHECK(decide(traits, Hand::None, kFilterBalanced) == Decision::SkipNotLeftStick);
}

TEST(policy_vector2_bound_to_single_axis_is_not_driven) {
    const ActionTraits traits = make_traits(ActionKind::Vector2, "move", {"/user/hand/left/input/thumbstick/y"});
    CHECK(decide(traits, Hand::None, kFilterBalanced) == Decision::SkipNotLeftStick);
}

TEST(policy_excluded_names_are_never_driven) {
    const ActionTraits menu = make_traits(ActionKind::Vector2, "menu_navigate", {"/user/hand/left/input/thumbstick"});
    const ActionTraits teleport = make_traits(ActionKind::Vector2, "teleport_aim", {"/user/hand/left/input/thumbstick"});
    for (const uint32_t mode : {kFilterBalanced, kFilterStrict, kFilterCompatibility}) {
        CHECK(decide(menu, Hand::None, mode) == Decision::SkipExcludedName);
        CHECK(decide(menu, Hand::Left, mode) == Decision::SkipExcludedName);
        CHECK(decide(teleport, Hand::Left, mode) == Decision::SkipExcludedName);
    }
}

TEST(policy_no_bindings_needs_compatibility_and_a_name) {
    const ActionTraits move = make_traits(ActionKind::Vector2, "move", {});
    const ActionTraits generic = make_traits(ActionKind::Vector2, "primary", {});
    const ActionTraits forward = make_traits(ActionKind::Float, "moveforward", {});
    const ActionTraits sideways = make_traits(ActionKind::Float, "moveright", {});
    CHECK(decide(move, Hand::None, kFilterBalanced) == Decision::SkipNoBindings);
    CHECK(decide(move, Hand::None, kFilterStrict) == Decision::SkipNoBindings);
    CHECK(decide(move, Hand::None, kFilterCompatibility) == Decision::Drive);
    CHECK(decide(generic, Hand::None, kFilterCompatibility) == Decision::SkipNoBindings);
    CHECK(decide(forward, Hand::None, kFilterCompatibility) == Decision::Drive);
    CHECK(decide(sideways, Hand::None, kFilterCompatibility) == Decision::SkipNoBindings);
    // Even in compatibility mode the right hand is never driven.
    CHECK(decide(move, Hand::Right, kFilterCompatibility) == Decision::SkipRightHand);
}

TEST(policy_non_axis_actions_are_skipped) {
    ActionTraits traits;
    traits.kind = ActionKind::Other;
    CHECK(decide(traits, Hand::Left, kFilterCompatibility) == Decision::SkipKind);
}

TEST(policy_right_only_stick_is_not_driven) {
    const ActionTraits traits = make_traits(ActionKind::Vector2, "move", {"/user/hand/right/input/thumbstick"});
    CHECK(decide(traits, Hand::None, kFilterBalanced) == Decision::SkipNotLeftStick);
    CHECK(decide(traits, Hand::None, kFilterCompatibility) == Decision::SkipNotLeftStick);
}

TEST(policy_combine_modes) {
    CHECK_NEAR(combine_axis(0.0F, 0.6F, kCombineMaxMagnitude), 0.6F);
    CHECK_NEAR(combine_axis(0.9F, 0.6F, kCombineMaxMagnitude), 0.9F);
    CHECK_NEAR(combine_axis(-0.9F, 0.6F, kCombineMaxMagnitude), -0.9F);  // pulling back harder than walking wins
    CHECK_NEAR(combine_axis(0.9F, 0.6F, kCombineReplace), 0.6F);
    CHECK_NEAR(combine_axis(0.9F, 0.6F, kCombineAdd), 1.0F);
    CHECK_NEAR(combine_axis(-0.2F, 0.6F, kCombineAdd), 0.4F);
    CHECK_NEAR(combine_axis(0.0F, 7.0F, kCombineReplace), 1.0F);
    CHECK_NEAR(combine_axis(0.0F, -7.0F, kCombineMaxMagnitude), -1.0F);
    CHECK_NEAR(combine_axis(0.0F, 0.6F, 999U), 0.6F);  // unknown mode behaves like max-magnitude
}

TEST(policy_unit_circle_clamp) {
    float x = 1.0F;
    float y = 1.0F;
    clamp_to_unit_circle(x, y);
    CHECK_NEAR(x * x + y * y, 1.0F);
    CHECK_NEAR(x, y);

    x = 0.3F;
    y = 0.4F;
    clamp_to_unit_circle(x, y);
    CHECK_NEAR(x, 0.3F);
    CHECK_NEAR(y, 0.4F);
}
