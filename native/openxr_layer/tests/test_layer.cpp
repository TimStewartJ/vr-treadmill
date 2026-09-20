// End-to-end scenarios: test exe (the "game") -> real Khronos loader -> layer DLL under test -> mock runtime.

#include "harness.h"

#include <atomic>
#include <chrono>
#include <fstream>
#include <sstream>
#include <thread>

using namespace harness;

namespace {

std::string read_layer_log() {
    char exe_path[MAX_PATH]{};
    GetModuleFileNameA(nullptr, exe_path, MAX_PATH);
    std::string exe(exe_path);
    exe = exe.substr(exe.find_last_of("\\/") + 1);
    std::ifstream file(fixtures().log_dir + "\\openxr-layer-" + exe + ".log", std::ios::binary);
    std::stringstream buffer;
    buffer << file.rdbuf();
    return buffer.str();
}

// The Unity OpenXR plugin exposes every control of a controller layout as one action with both hands as
// subaction paths, and reads it once per hand.
struct UnityRig {
    explicit UnityRig(const Game& game) {
        set = game.create_set("oculustouchcontroller");
        thumbstick = game.create_action(set, "thumbstick", XR_ACTION_TYPE_VECTOR2F_INPUT, game.both_hands(), "Thumbstick");
        trigger = game.create_action(set, "trigger", XR_ACTION_TYPE_FLOAT_INPUT, game.both_hands(), "Trigger");
        primary = game.create_action(set, "primarybutton", XR_ACTION_TYPE_BOOLEAN_INPUT, game.both_hands(), "Primary Button");
        game.suggest(kTouchProfile, {
                                        {thumbstick, "/user/hand/left/input/thumbstick"},
                                        {thumbstick, "/user/hand/right/input/thumbstick"},
                                        {trigger, "/user/hand/left/input/trigger/value"},
                                        {trigger, "/user/hand/right/input/trigger/value"},
                                        {primary, "/user/hand/left/input/x/click"},
                                        {primary, "/user/hand/right/input/a/click"},
                                    });
        game.attach({set});
    }
    XrActionSet set = XR_NULL_HANDLE;
    XrAction thumbstick = XR_NULL_HANDLE;
    XrAction trigger = XR_NULL_HANDLE;
    XrAction primary = XR_NULL_HANDLE;
};

}  // namespace

// Runs first (tests are sorted by name): the treadmill app is not running, so its mapping does not exist.
TEST(layer_00_no_publisher_then_app_starts_mid_game) {
    reset_world();
    REQUIRE(fixtures().publisher == nullptr);
    MockRuntime::get().set_thumbstick(kLeftHand, 0.25F, -0.5F);
    MockRuntime::get().set_thumbstick(kRightHand, 0.75F, 0.125F);

    Game game("NoPublisher", "Unity");
    REQUIRE_XR(game.create_result);
    const UnityRig rig(game);
    REQUIRE_XR(game.sync({rig.set}));

    const XrActionStateVector2f left = game.vec2(rig.thumbstick, game.left);
    const XrActionStateVector2f right = game.vec2(rig.thumbstick, game.right);
    CHECK_NEAR(left.currentState.x, 0.25F);
    CHECK_NEAR(left.currentState.y, -0.5F);
    CHECK_NEAR(right.currentState.x, 0.75F);
    CHECK_NEAR(right.currentState.y, 0.125F);
    CHECK(left.isActive == XR_TRUE);

    // The player starts VR Treadmill while the game is already running. The layer looks for the app
    // once a second, so it has to pick the treadmill up without restarting the game.
    MockRuntime::get().set_thumbstick(kLeftHand, 0.25F, 0.0F);
    publisher().publish(0.6F);
    bool driven = false;
    for (int attempt = 0; attempt < 40 && !driven; ++attempt) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        publisher().publish(0.6F);
        REQUIRE_XR(game.sync({rig.set}));
        driven = testing::approx(game.vec2(rig.thumbstick, game.left).currentState.y, 0.6);
    }
    CHECK(driven);
    CHECK_NEAR(game.vec2(rig.thumbstick, game.right).currentState.y, 0.125F);
}

TEST(layer_01_loader_loaded_the_dll_under_test) {
    reset_world();
    Game game("WhichDll", "Unity", XR_MAKE_VERSION(1, 1, 0));  // an OpenXR 1.1 application
    REQUIRE_XR(game.create_result);

    const HMODULE module = GetModuleHandleA("vrtread_openxr_layer.dll");
    REQUIRE(module != nullptr);
    char loaded[MAX_PATH]{};
    GetModuleFileNameA(module, loaded, MAX_PATH);
    char loaded_full[MAX_PATH]{};
    char expected_full[MAX_PATH]{};
    GetFullPathNameA(loaded, MAX_PATH, loaded_full, nullptr);
    GetFullPathNameA(layer_dll_path().c_str(), MAX_PATH, expected_full, nullptr);
    if (_stricmp(loaded_full, expected_full) != 0) {
        testing::report_failure(__FILE__, __LINE__, std::string("wrong layer DLL loaded: ") + loaded_full + " (expected " +
                                                        expected_full + ")");
    }

    const std::string log = read_layer_log();
    CHECK(log.find("attached: exe=") != std::string::npos);
    CHECK(log.find("app='WhichDll'") != std::string::npos);
    CHECK(log.find("engine='Unity'") != std::string::npos);
    CHECK(log.find("api=1.1") != std::string::npos);
    CHECK(log.find("mode=active") != std::string::npos);
}

TEST(layer_unity_only_the_left_hand_is_driven) {
    reset_world();
    MockRuntime::get().set_thumbstick(kLeftHand, 0.25F, 0.0F);
    MockRuntime::get().set_thumbstick(kRightHand, 0.5F, 0.125F);

    Game game("UnityGame", "Unity");
    REQUIRE_XR(game.create_result);
    const UnityRig rig(game);

    publisher().publish(0.8F);
    REQUIRE_XR(game.sync({rig.set}));

    const XrActionStateVector2f left = game.vec2(rig.thumbstick, game.left);
    CHECK_NEAR(left.currentState.y, 0.8F);
    CHECK_NEAR(left.currentState.x, 0.25F);  // strafe input is preserved
    CHECK(left.isActive == XR_TRUE);

    // The prototype's bug: this read also received the treadmill value.
    const XrActionStateVector2f right = game.vec2(rig.thumbstick, game.right);
    CHECK_NEAR(right.currentState.x, 0.5F);
    CHECK_NEAR(right.currentState.y, 0.125F);

    // Other action types bound on the left hand are untouched.
    MockRuntime::get().set_trigger(kLeftHand, 0.4F);
    REQUIRE_XR(game.sync({rig.set}));
    CHECK_NEAR(game.scalar(rig.trigger, game.left).currentState, 0.4F);
    CHECK(game.boolean(rig.primary, game.left).currentState == XR_FALSE);
}

TEST(layer_unreal_legacy_float_axes) {
    reset_world();
    MockRuntime::get().set_thumbstick(kLeftHand, 0.3F, 0.0F);
    MockRuntime::get().set_thumbstick(kRightHand, -0.6F, 0.2F);

    Game game("UnrealGame", "UnrealEngine4");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("ue4");
    const XrAction forward = game.create_action(set, "moveforward", XR_ACTION_TYPE_FLOAT_INPUT, {}, "MoveForward");
    const XrAction sideways = game.create_action(set, "moveright", XR_ACTION_TYPE_FLOAT_INPUT, {}, "MoveRight");
    const XrAction turn = game.create_action(set, "turn", XR_ACTION_TYPE_FLOAT_INPUT, {}, "Turn");
    const XrAction look_up = game.create_action(set, "rightsticky", XR_ACTION_TYPE_FLOAT_INPUT, {}, "RightStickY");
    game.suggest(kTouchProfile, {
                                    {forward, "/user/hand/left/input/thumbstick/y"},
                                    {sideways, "/user/hand/left/input/thumbstick/x"},
                                    {turn, "/user/hand/right/input/thumbstick/x"},
                                    {look_up, "/user/hand/right/input/thumbstick/y"},
                                });
    game.attach({set});

    publisher().publish(0.65F);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.scalar(forward).currentState, 0.65F);
    CHECK_NEAR(game.scalar(sideways).currentState, 0.3F);
    CHECK_NEAR(game.scalar(turn).currentState, -0.6F);
    CHECK_NEAR(game.scalar(look_up).currentState, 0.2F);  // right-hand Y axis must never be driven
}

TEST(layer_native_vector2_move_without_subactions) {
    reset_world();
    MockRuntime::get().set_thumbstick(kRightHand, 1.0F, 0.0F);

    Game game("NativeGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    const XrAction turn = game.create_action(set, "snap_turn", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}, {turn, "/user/hand/right/input/thumbstick"}});
    game.attach({set});

    publisher().publish(-0.4F);  // walking backwards
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, -0.4F);
    CHECK_NEAR(game.vec2(turn).currentState.x, 1.0F);
    CHECK_NEAR(game.vec2(turn).currentState.y, 0.0F);
}

TEST(layer_filter_modes) {
    reset_world();
    Game game("GodotGame", "Godot");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("godot");
    const XrAction primary = game.create_action(set, "primary", XR_ACTION_TYPE_VECTOR2F_INPUT, game.both_hands());
    const XrAction unbound_move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    const XrAction placeholder = game.create_action(set, "grab", XR_ACTION_TYPE_FLOAT_INPUT);
    game.suggest(kTouchProfile, {
                                    {primary, "/user/hand/left/input/thumbstick"},
                                    {primary, "/user/hand/right/input/thumbstick"},
                                    {placeholder, "/user/hand/left/input/squeeze/value"},
                                });
    game.attach({set});

    PublishOptions options;
    options.filter_mode = vrtread::kFilterBalanced;
    publisher().publish(0.5F, options);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(primary, game.left).currentState.y, 0.5F);
    CHECK_NEAR(game.vec2(primary, game.right).currentState.y, 0.0F);
    CHECK_NEAR(game.vec2(primary).currentState.y, 0.0F);  // combined read, generic name: ambiguous

    options.filter_mode = vrtread::kFilterStrict;
    publisher().publish(0.5F, options);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(primary, game.left).currentState.y, 0.0F);  // "primary" does not say locomotion

    options.filter_mode = vrtread::kFilterCompatibility;
    publisher().publish(0.5F, options);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(primary, game.left).currentState.y, 0.5F);
    CHECK_NEAR(game.vec2(primary, game.right).currentState.y, 0.0F);
    // "move" has no bindings, so the runtime reports it inactive and the layer leaves it alone by default.
    CHECK(game.vec2(unbound_move).isActive == XR_FALSE);
    CHECK_NEAR(game.vec2(unbound_move).currentState.y, 0.0F);
}

TEST(layer_menu_and_teleport_actions_are_never_driven) {
    reset_world();
    Game game("MenuGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    const XrAction menu = game.create_action(set, "menu_navigate", XR_ACTION_TYPE_VECTOR2F_INPUT);
    const XrAction teleport = game.create_action(set, "teleport_aim", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {
                                    {move, "/user/hand/left/input/thumbstick"},
                                    {menu, "/user/hand/left/input/thumbstick"},
                                    {teleport, "/user/hand/left/input/thumbstick"},
                                });
    game.attach({set});

    PublishOptions options;
    options.filter_mode = vrtread::kFilterCompatibility;  // even the most permissive filter
    publisher().publish(1.0F, options);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 1.0F);
    CHECK_NEAR(game.vec2(menu).currentState.y, 0.0F);
    CHECK_NEAR(game.vec2(teleport).currentState.y, 0.0F);
}

TEST(layer_vive_trackpad_bindings_count_as_the_stick) {
    reset_world();
    MockRuntime::get().set_profile(kViveProfile);
    Game game("ViveOnlyGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kViveProfile, {{move, "/user/hand/left/input/trackpad"}});
    game.attach({set});

    publisher().publish(0.7F);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.7F);
}

TEST(layer_combine_modes) {
    reset_world();
    Game game("CombineGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});

    PublishOptions options;
    MockRuntime::get().set_thumbstick(kLeftHand, 0.0F, 0.9F);  // player also pushes the stick forward, harder
    options.combine_mode = vrtread::kCombineMaxMagnitude;
    publisher().publish(0.5F, options);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.9F);

    options.combine_mode = vrtread::kCombineReplace;
    publisher().publish(0.5F, options);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.5F);

    options.combine_mode = vrtread::kCombineAdd;
    publisher().publish(0.5F, options);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 1.0F);

    MockRuntime::get().set_thumbstick(kLeftHand, 0.0F, -1.0F);  // pulling back while walking forward slowly
    options.combine_mode = vrtread::kCombineMaxMagnitude;
    publisher().publish(0.3F, options);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, -1.0F);
}

TEST(layer_vector_stays_inside_the_unit_circle) {
    reset_world();
    MockRuntime::get().set_thumbstick(kLeftHand, 1.0F, 0.0F);  // full strafe
    Game game("CircleGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});

    publisher().publish(1.0F);
    REQUIRE_XR(game.sync({set}));
    const XrVector2f value = game.vec2(move).currentState;
    CHECK(value.x * value.x + value.y * value.y <= 1.0001F);
    CHECK_NEAR(value.x, value.y);
    CHECK(value.y > 0.7F);
}

TEST(layer_idle_treadmill_changes_nothing) {
    reset_world();
    MockRuntime::get().set_thumbstick(kLeftHand, 0.1F, 0.2F);
    Game game("IdleGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});

    publisher().idle();
    REQUIRE_XR(game.sync({set}));
    REQUIRE_XR(game.sync({set}));
    const XrActionStateVector2f state = game.vec2(move);
    CHECK_NEAR(state.currentState.x, 0.1F);
    CHECK_NEAR(state.currentState.y, 0.2F);
    CHECK(state.changedSinceLastSync == XR_FALSE);

    publisher().publish(0.0004F);  // below the idle threshold
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.2F);
}

TEST(layer_rejects_unusable_shared_memory) {
    reset_world();
    Game game("SafetyGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});

    const auto observed_y = [&]() {
        REQUIRE_XR(game.sync({set}));
        return game.vec2(move).currentState.y;
    };

    publisher().publish(0.9F);
    CHECK_NEAR(observed_y(), 0.9F);  // sanity: the good case works

    PublishOptions stale;
    stale.age_ms = 400;  // app hung or crashed 400 ms ago
    publisher().publish(0.9F, stale);
    CHECK_NEAR(observed_y(), 0.0F);

    PublishOptions future;
    future.age_ms = -5000;
    publisher().publish(0.9F, future);
    CHECK_NEAR(observed_y(), 0.0F);

    PublishOptions inactive;
    inactive.active = false;  // capture stopped
    publisher().publish(0.9F, inactive);
    CHECK_NEAR(observed_y(), 0.0F);

    vrtread::SharedState block = publisher().make_block(0.9F);
    block.magic = 0xDEADBEEF;
    publisher().write(block);
    CHECK_NEAR(observed_y(), 0.0F);

    block = publisher().make_block(0.9F);
    block.version = vrtread::kVersion + 1;
    publisher().write(block);
    CHECK_NEAR(observed_y(), 0.0F);

    block = publisher().make_block(0.9F);
    block.struct_size = 56;  // the v1 prototype layout
    publisher().write(block);
    CHECK_NEAR(observed_y(), 0.0F);

    block = publisher().make_block(std::numeric_limits<float>::quiet_NaN());
    publisher().write(block);
    CHECK_NEAR(observed_y(), 0.0F);

    block = publisher().make_block(std::numeric_limits<float>::infinity());
    publisher().write(block);
    CHECK_NEAR(observed_y(), 0.0F);

    publisher().publish(250.0F);  // out of range is clamped, not trusted
    CHECK_NEAR(observed_y(), 1.0F);

    block = publisher().make_block(0.5F);
    block.filter_mode = 77;  // unknown enum values fall back to defaults instead of disabling locomotion
    block.combine_mode = 99;
    publisher().write(block);
    CHECK_NEAR(observed_y(), 0.5F);

    publisher().publish(0.9F);
    CHECK_NEAR(observed_y(), 0.9F);  // and it recovers
}

TEST(layer_writer_caught_mid_update_holds_last_good_value) {
    reset_world();
    Game game("SeqlockGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});

    publisher().publish(0.6F);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.6F);

    // Writer stalls with seq odd. The prototype dropped to zero for a frame here (a visible stutter).
    publisher().write(publisher().make_block(0.1F), /*leave_mid_write=*/true);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.6F);

    // ...but not forever: once the held block is older than the staleness limit, stop.
    std::this_thread::sleep_for(std::chrono::milliseconds(static_cast<int>(vrtread::kStaleMs) + 80));
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.0F);

    publisher().publish(0.2F);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.2F);
}

TEST(layer_value_only_changes_at_sync) {
    reset_world();
    Game game("SyncGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});

    publisher().publish(0.3F);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.3F);
    publisher().publish(0.9F);  // app publishes at 100 Hz, in between the game's syncs
    CHECK_NEAR(game.vec2(move).currentState.y, 0.3F);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.9F);
}

TEST(layer_changed_since_last_sync_is_honest) {
    reset_world();
    Game game("ChangedGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    const XrAction forward = game.create_action(set, "moveforward", XR_ACTION_TYPE_FLOAT_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}, {forward, "/user/hand/left/input/thumbstick/y"}});
    game.attach({set});

    publisher().idle();
    REQUIRE_XR(game.sync({set}));
    CHECK(game.vec2(move).changedSinceLastSync == XR_FALSE);
    const XrTime before = game.vec2(move).lastChangeTime;

    publisher().publish(0.5F);  // starts walking
    REQUIRE_XR(game.sync({set}));
    const XrActionStateVector2f started = game.vec2(move);
    CHECK(started.changedSinceLastSync == XR_TRUE);
    CHECK(game.vec2(move).changedSinceLastSync == XR_TRUE);  // repeated read in the same sync agrees
    CHECK(game.scalar(forward).changedSinceLastSync == XR_TRUE);
    CHECK(started.lastChangeTime >= before);

    REQUIRE_XR(game.sync({set}));  // same pace
    CHECK(game.vec2(move).changedSinceLastSync == XR_FALSE);
    CHECK(game.scalar(forward).changedSinceLastSync == XR_FALSE);
    CHECK_NEAR(game.vec2(move).currentState.y, 0.5F);

    publisher().publish(0.7F);  // speeds up
    REQUIRE_XR(game.sync({set}));
    CHECK(game.vec2(move).changedSinceLastSync == XR_TRUE);

    publisher().idle();  // stops: the value the game sees drops from 0.7 to the physical 0.0
    REQUIRE_XR(game.sync({set}));
    const XrActionStateVector2f stopped = game.vec2(move);
    CHECK_NEAR(stopped.currentState.y, 0.0F);
    CHECK(stopped.changedSinceLastSync == XR_TRUE);
    CHECK(game.vec2(move).changedSinceLastSync == XR_TRUE);
    CHECK(game.scalar(forward).changedSinceLastSync == XR_TRUE);

    REQUIRE_XR(game.sync({set}));
    CHECK(game.vec2(move).changedSinceLastSync == XR_FALSE);
    CHECK(game.scalar(forward).changedSinceLastSync == XR_FALSE);
}

TEST(layer_inactive_actions_follow_the_opt_in) {
    reset_world();
    Game game("InactiveGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet gameplay = game.create_set("gameplay");
    const XrActionSet vehicle = game.create_set("vehicle");
    const XrAction move = game.create_action(gameplay, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    const XrAction fire = game.create_action(gameplay, "fire", XR_ACTION_TYPE_BOOLEAN_INPUT);
    const XrAction drive = game.create_action(vehicle, "drive", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {
                                    {move, "/user/hand/left/input/thumbstick"},
                                    {fire, "/user/hand/right/input/trigger/value"},
                                    {drive, "/user/hand/left/input/thumbstick"},
                                });
    game.attach({gameplay, vehicle});

    MockRuntime::get().set_connected(kLeftHand, 0);  // left controller put down / asleep
    const auto frame = [&](const std::vector<XrActionSet>& sets) {
        const XrResult result = game.sync(sets);
        game.boolean(fire);  // the game reads its other actions too
        return result;
    };

    // Default: an inactive action stays inactive.
    publisher().publish(0.8F);
    REQUIRE_XR(frame({gameplay}));
    REQUIRE_XR(frame({gameplay}));
    CHECK(game.vec2(move).isActive == XR_FALSE);
    CHECK_NEAR(game.vec2(move).currentState.y, 0.0F);

    // Opt-in: keep walking with the left controller down, as long as the session is live.
    PublishOptions options;
    options.options = vrtread::kOptionDriveInactive;
    publisher().publish(0.8F, options);
    REQUIRE_XR(frame({gameplay}));
    REQUIRE_XR(frame({gameplay}));
    CHECK(game.vec2(move).isActive == XR_TRUE);
    CHECK_NEAR(game.vec2(move).currentState.y, 0.8F);
    CHECK_NEAR(game.vec2(move).currentState.x, 0.0F);

    // ...but never for an action set the game has switched off.
    CHECK(game.vec2(drive).isActive == XR_FALSE);
    CHECK_NEAR(game.vec2(drive).currentState.y, 0.0F);

    // ...and never while the session is unfocused (dashboard open, headset off).
    MockRuntime::get().set_focused(0);
    CHECK(frame({gameplay}) == XR_SESSION_NOT_FOCUSED);
    CHECK(frame({gameplay}) == XR_SESSION_NOT_FOCUSED);
    CHECK(game.vec2(move).isActive == XR_FALSE);
    CHECK_NEAR(game.vec2(move).currentState.y, 0.0F);
}

TEST(layer_unfocused_session_is_not_driven) {
    reset_world();
    Game game("FocusGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});

    publisher().publish(0.8F);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.8F);

    MockRuntime::get().set_focused(0);
    CHECK(game.sync({set}) == XR_SESSION_NOT_FOCUSED);
    CHECK_NEAR(game.vec2(move).currentState.y, 0.0F);
    CHECK(game.vec2(move).isActive == XR_FALSE);

    MockRuntime::get().set_focused(1);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.8F);
}

TEST(layer_higher_priority_menu_set_suppresses_locomotion) {
    reset_world();
    Game game("PriorityGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet gameplay = game.create_set("gameplay", 0);
    const XrActionSet menu = game.create_set("pausescreen", 10);
    const XrAction move = game.create_action(gameplay, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    const XrAction navigate = game.create_action(menu, "navigate", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}, {navigate, "/user/hand/left/input/thumbstick"}});
    game.attach({gameplay, menu});

    PublishOptions options;
    options.options = vrtread::kOptionDriveInactive;  // the most aggressive setting, for the whole test
    publisher().publish(0.8F, options);

    REQUIRE_XR(game.sync({gameplay}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.8F);

    // Pause menu opens: the runtime hands the stick to the higher-priority menu set and reports "move"
    // inactive. Forcing it active here would walk the player around behind the pause menu, so the layer has
    // to recognise priority suppression on its own even with the "drive while inactive" opt-in enabled.
    REQUIRE_XR(game.sync({gameplay, menu}));
    REQUIRE_XR(game.sync({gameplay, menu}));
    CHECK(game.vec2(move).isActive == XR_FALSE);
    CHECK_NEAR(game.vec2(move).currentState.y, 0.0F);
    CHECK_NEAR(game.vec2(navigate).currentState.y, 0.0F);  // and walking never scrolls the menu

    // Menu closes again.
    REQUIRE_XR(game.sync({gameplay}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.8F);
}

TEST(layer_errors_from_the_runtime_pass_through) {
    reset_world();
    Game game("ErrorGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT, {game.left});
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});
    publisher().publish(0.8F);
    REQUIRE_XR(game.sync({set}));

    XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
    info.action = move;
    info.subactionPath = game.right;  // not declared for this action
    XrActionStateVector2f state{XR_TYPE_ACTION_STATE_VECTOR2F};
    state.currentState.y = 123.0F;
    CHECK(xrGetActionStateVector2f(game.session, &info, &state) == XR_ERROR_PATH_UNSUPPORTED);
    CHECK_NEAR(state.currentState.y, 123.0F);  // output untouched on failure

    XrActionStateFloat wrong_type{XR_TYPE_ACTION_STATE_FLOAT};
    info.subactionPath = XR_NULL_PATH;
    CHECK(xrGetActionStateFloat(game.session, &info, &wrong_type) == XR_ERROR_ACTION_TYPE_MISMATCH);
}

TEST(layer_bypass_environment_variable) {
    reset_world();
    set_env("VRTREAD_OPENXR_BYPASS", "1");
    {
        Game game("BypassGame", "Custom");
        REQUIRE_XR(game.create_result);
        const XrActionSet set = game.create_set("gameplay");
        const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
        game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
        game.attach({set});
        publisher().publish(0.8F);
        REQUIRE_XR(game.sync({set}));
        CHECK_NEAR(game.vec2(move).currentState.y, 0.0F);
    }
    set_env("VRTREAD_OPENXR_BYPASS", nullptr);
    CHECK(read_layer_log().find("mode=passthrough reason=VRTREAD_OPENXR_BYPASS is set") != std::string::npos);
}

TEST(layer_probe_instance_then_real_instance) {
    // Many engines create a throwaway instance to query extensions, destroy it, then create the real one.
    reset_world();
    {
        Game probe("Probe", "Unity");
        REQUIRE_XR(probe.create_result);
    }
    CHECK(MockRuntime::get().live_instances() == 0U);

    Game game("Real", "Unity");
    REQUIRE_XR(game.create_result);
    const UnityRig rig(game);
    publisher().publish(0.45F);
    REQUIRE_XR(game.sync({rig.set}));
    CHECK_NEAR(game.vec2(rig.thumbstick, game.left).currentState.y, 0.45F);
    CHECK_NEAR(game.vec2(rig.thumbstick, game.right).currentState.y, 0.0F);
}

TEST(layer_destroy_and_recreate_actions) {
    reset_world();
    Game game("RecreateGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet first = game.create_set("first");
    const XrAction doomed = game.create_action(first, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    CHECK_XR(xrDestroyAction(doomed));
    CHECK_XR(xrDestroyActionSet(first));

    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
    game.attach({set});
    publisher().publish(0.35F);
    REQUIRE_XR(game.sync({set}));
    CHECK_NEAR(game.vec2(move).currentState.y, 0.35F);

    game.shutdown();
    CHECK(MockRuntime::get().live_instances() == 0U);
    CHECK(MockRuntime::get().call_count("xrDestroySession") == 1U);
    CHECK(MockRuntime::get().call_count("xrDestroyInstance") == 1U);
}

TEST(layer_every_call_reaches_the_runtime_exactly_once) {
    reset_world();
    Game game("CountGame", "Custom");
    REQUIRE_XR(game.create_result);
    const XrActionSet set = game.create_set("gameplay");
    const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
    const XrAction forward = game.create_action(set, "moveforward", XR_ACTION_TYPE_FLOAT_INPUT);
    const XrAction fire = game.create_action(set, "fire", XR_ACTION_TYPE_BOOLEAN_INPUT);
    game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"},
                                 {forward, "/user/hand/left/input/thumbstick/y"},
                                 {fire, "/user/hand/right/input/trigger/value"}});
    game.attach({set});
    publisher().publish(0.5F);
    for (int frame = 0; frame < 10; ++frame) {
        REQUIRE_XR(game.sync({set}));
        game.vec2(move);
        game.scalar(forward);
        game.boolean(fire);
    }
    MockRuntime& runtime = MockRuntime::get();
    CHECK(runtime.call_count("xrCreateInstance") == 1U);
    CHECK(runtime.call_count("xrCreateSession") == 1U);
    CHECK(runtime.call_count("xrCreateActionSet") == 1U);
    CHECK(runtime.call_count("xrCreateAction") == 3U);
    CHECK(runtime.call_count("xrSuggestInteractionProfileBindings") == 1U);
    CHECK(runtime.call_count("xrAttachSessionActionSets") == 1U);
    CHECK(runtime.call_count("xrSyncActions") == 10U);
    CHECK(runtime.call_count("xrGetActionStateVector2f") == 10U);
    CHECK(runtime.call_count("xrGetActionStateFloat") == 10U);
    CHECK(runtime.call_count("xrGetActionStateBoolean") == 10U);
}

TEST(layer_log_explains_its_decisions) {
    reset_world();
    Game game("LogGame", "Unity");
    REQUIRE_XR(game.create_result);
    const UnityRig rig(game);
    publisher().idle();  // standing still: the decision is still logged
    REQUIRE_XR(game.sync({rig.set}));
    game.vec2(rig.thumbstick, game.left);
    game.vec2(rig.thumbstick, game.right);

    const std::string log = read_layer_log();
    CHECK(log.find("action created name='thumbstick' localized='Thumbstick' type=vector2f subactions=2") != std::string::npos);
    CHECK(log.find("left-stick binding: action='thumbstick' path='/user/hand/left/input/thumbstick'") != std::string::npos);
    CHECK(log.find("decision action='thumbstick' hand=left filter=balanced -> drive") != std::string::npos);
    CHECK(log.find("decision action='thumbstick' hand=right filter=balanced -> skip:right-hand") != std::string::npos);
}

TEST(layer_threads_hammering_the_input_system) {
    reset_world();
    MockRuntime::get().set_thumbstick(kLeftHand, 0.0F, 0.0F);
    Game game("ThreadGame", "Unity");
    REQUIRE_XR(game.create_result);
    const UnityRig rig(game);

    std::atomic<bool> stop{false};
    std::atomic<int> bad_values{0};
    std::atomic<uint64_t> reads{0};

    std::thread writer([&] {
        int step = 1;
        while (!stop.load()) {
            publisher().publish(static_cast<float>(step) / 10.0F);
            step = step >= 9 ? 1 : step + 1;
        }
    });
    std::thread syncer([&] {
        while (!stop.load()) {
            game.sync({rig.set});
        }
    });
    std::vector<std::thread> readers;
    for (int index = 0; index < 4; ++index) {
        readers.emplace_back([&] {
            XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
            info.action = rig.thumbstick;
            while (!stop.load()) {
                info.subactionPath = game.left;
                XrActionStateVector2f left{XR_TYPE_ACTION_STATE_VECTOR2F};
                xrGetActionStateVector2f(game.session, &info, &left);
                info.subactionPath = game.right;
                XrActionStateVector2f right{XR_TYPE_ACTION_STATE_VECTOR2F};
                xrGetActionStateVector2f(game.session, &info, &right);
                // Left is always a value the writer published (or 0 before the first sync); right is never touched.
                const float y = left.currentState.y;
                const bool valid_left = y >= 0.0F && y <= 0.9001F && std::fabs(y * 10.0F - std::round(y * 10.0F)) < 1e-3F;
                if (!valid_left || right.currentState.y != 0.0F) {
                    ++bad_values;
                }
                ++reads;
            }
        });
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(1500));
    stop = true;
    writer.join();
    syncer.join();
    for (std::thread& reader : readers) {
        reader.join();
    }
    std::printf("    %llu concurrent reads\n", static_cast<unsigned long long>(reads.load()));
    CHECK(bad_values.load() == 0);
    CHECK(reads.load() > 1000U);
}

TEST(layer_first_read_of_a_session_is_not_lost_to_a_busy_writer) {
    // A game's very first read has no earlier block to fall back on. If it lands while the app is mid-update
    // (seq odd) the player would not move on that frame. Make that collision as likely as possible: a writer
    // that does nothing but publish, against many fresh game sessions (each one reloads the layer DLL).
    reset_world();
    std::atomic<bool> stop{false};
    std::thread writer([&] {
        while (!stop.load()) {
            publisher().publish(0.5F);
        }
    });

    int missed = 0;
    constexpr int kSessions = 150;
    for (int index = 0; index < kSessions; ++index) {
        Game game("FirstReadGame", "Custom");
        REQUIRE_XR(game.create_result);
        const XrActionSet set = game.create_set("gameplay");
        const XrAction move = game.create_action(set, "move", XR_ACTION_TYPE_VECTOR2F_INPUT);
        game.suggest(kTouchProfile, {{move, "/user/hand/left/input/thumbstick"}});
        game.attach({set});
        REQUIRE_XR(game.sync({set}));
        if (!testing::approx(game.vec2(move).currentState.y, 0.5)) {
            ++missed;
        }
    }
    stop = true;
    writer.join();
    std::printf("    %d of %d sessions missed their first read\n", missed, kSessions);
    CHECK(missed == 0);
}

TEST(layer_overhead_is_negligible) {
    reset_world();
    Game game("PerfGame", "Unity");
    REQUIRE_XR(game.create_result);
    const UnityRig rig(game);
    publisher().publish(0.5F);
    REQUIRE_XR(game.sync({rig.set}));

    XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO};
    info.action = rig.thumbstick;
    info.subactionPath = game.left;
    XrActionStateVector2f state{XR_TYPE_ACTION_STATE_VECTOR2F};

    constexpr int kIterations = 200000;
    const auto start = std::chrono::steady_clock::now();
    for (int index = 0; index < kIterations; ++index) {
        xrGetActionStateVector2f(game.session, &info, &state);
    }
    const double total_us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count();
    const double per_call_us = total_us / kIterations;
    std::printf("    %.3f us per driven xrGetActionStateVector2f (loader + layer + mock runtime)\n", per_call_us);
    // A game makes on the order of 100 of these per frame; 20 us each would still be 2 ms, so this bound is
    // deliberately loose to stay stable on shared CI machines while catching accidental per-call I/O.
    CHECK(per_call_us < 20.0);
}
