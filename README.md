# VR Treadmill

[![Build Windows app](https://github.com/TimStewartJ/vr-treadmill/actions/workflows/build-windows.yml/badge.svg)](https://github.com/TimStewartJ/vr-treadmill/actions/workflows/build-windows.yml)

Turn treadmill mouse movement into VR locomotion input for Windows VR games.

VR Treadmill is for setups where a treadmill or mouse-like sensor reports forward/back movement. The app converts that movement into left-stick Y input through the automatic OpenXR layer, ViGEmBus/XInput fallback, or both.

## Download and install

Most users do **not** need Python.

1. Open the [latest VR Treadmill build](https://github.com/TimStewartJ/vr-treadmill/releases/tag/latest).
2. Download **`VRTreadmill-Setup.exe`**.
3. Run the installer.
4. Open **VR Treadmill** from the Start menu.
5. If the app says ViGEmBus is missing, click **Install/Open ViGEmBus Driver**, install the driver, then reopen VR Treadmill.

Advanced users can download **`VRTreadmill.exe`** instead for a portable single-file app.

## What it does at a glance

- Publishes OpenXR locomotion input automatically, creates a virtual Xbox 360 fallback controller, or both.
- Converts treadmill/mouse forward/back movement into left-stick forward/back movement.
- Gives you beginner-friendly tuning presets.
- Runs from the system tray if you want it out of the way.
- Can start with Windows and launch minimized.
- Does **not** require FreePIE, vJoy, x360ce, or Python for normal users.

## Quick start

1. Leave output on **OpenXR automatic** for OpenXR-native games, or choose **Xbox controller** for the fallback path.
2. Leave tuning on **Balanced**.
3. Click **Start treadmill capture**.
4. Check that the stick meter moves when you walk/move the treadmill sensor.
5. Open an OpenXR-native game. The implicit OpenXR layer auto-loads after the app enables it.
6. For Xbox mode, confirm the **Xbox fallback driver** section says ViGEmBus is installed/running, then bind the Xbox/Gamepad left stick to movement for your game if needed.
7. Press **F8** any time as an emergency stop.

## Current approach

- Reads vertical mouse movement while capture mode is active.
- Recenters the cursor so the treadmill can keep producing motion.
- Sends left-stick Y through an automatic OpenXR shared-memory layer, a virtual Xbox 360 controller through `vgamepad`, or both.
- Xbox mode requires ViGEmBus to already be installed.
- Does not require FreePIE, vJoy, or x360ce.

OpenXR mode is the primary path for OpenXR-native games that read locomotion through OpenXR action state. Xbox mode works only when SteamVR and the target game accept a normal Xbox/XInput controller as an input source.

## Developer setup

```powershell
cd E:\vr-treadmill
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

## Run from source

```powershell
.\.venv\Scripts\vrtread-gui.exe
```

The app has Start/Stop controls, a live stick meter, beginner-friendly tuning presets, and sliders for the core movement settings. Press **F8** as an emergency stop while capture mode is active.

The **Xbox fallback driver** section shows whether ViGEmBus appears installed/running and includes an **Install/Open ViGEmBus Driver** button that opens the official ViGEmBus releases page.

The **Output** section chooses where treadmill movement is published:

| Mode | Use when |
| --- | --- |
| **Xbox controller** | The game accepts an XInput controller alongside VR controllers. |
| **OpenXR automatic** | The game is OpenXR-native and should receive treadmill motion through OpenXR action state. |
| **Both** | You want to test both paths at once and have ViGEmBus installed. |

The **OpenXR filter** setting controls how cautiously the layer chooses actions to override:

| Filter | Use when |
| --- | --- |
| **Strict** | Menus are being affected, or you only want locomotion-named left-stick actions overridden. |
| **Balanced** | Recommended default. Override active non-menu left-stick actions. |
| **Compatibility** | A game does not move in Balanced mode because it does not expose enough binding metadata. |

Closing or minimizing the window hides it to the system tray. Use the tray icon to show the window, start/stop capture, or fully exit.

Enable **Start with Windows** to register the app in the current user's Windows startup list. This also enables **Start minimized to tray** so boot does not open the full window. Settings are saved under `%APPDATA%\VRTreadmill\settings.json`.

## Tuning for a game

Start with the **Balanced** preset. Use **Faster** if you have to walk too much before the game moves. Use **Comfort** if the game drifts, jitters, or feels too sensitive. Use **Snappy** if the game feels laggy when you stop.

| Control | What it means | Try changing it when... |
| --- | --- | --- |
| **Movement speed** | How much virtual stick push comes from treadmill/mouse movement. | Walking feels too slow or too fast. |
| **Stop smoothness** | How quickly the virtual stick returns to center after you stop. | You want either smoother coasting or faster stopping. |
| **Noise filter** | How much tiny accidental movement is ignored. | The character drifts while standing still, or slow walking is not detected. |
| **Update rate** | How often the virtual controller sends updates. | Usually leave this at `100 Hz`; raise only if movement feels choppy. |

## Run the CLI

```powershell
.\.venv\Scripts\vrtread-cli.exe --output-mode openxr
.\.venv\Scripts\vrtread-cli.exe --output-mode openxr --openxr-filter strict
.\.venv\Scripts\vrtread-cli.exe --output-mode xbox
```

Press **Ctrl+C** to stop.

## Automatic OpenXR layer

The OpenXR prototype is split into two parts:

1. The Python app publishes `{ active, x, y, timestamp, filter_mode }` to `Local\VRTreadmillOpenXRState_v1` using a stale-safe seqlock shared-memory block.
2. The native OpenXR API layer DLL reads that block and, when fresh treadmill data is active, substitutes left-thumbstick locomotion action Y values returned by `xrGetActionStateVector2f`. It also hooks `xrGetActionStateFloat` for rare games that expose a thumbstick Y axis as a float action.
3. The layer is registered as an implicit per-user layer, so OpenXR apps load it automatically. It passes through unchanged unless capture is active, shared memory is fresh, the target process is not a known runtime/helper process, the OpenXR action is active, and the selected filter allows the action.

Build the layer and write its manifest:

```powershell
.\scripts\build-openxr-layer.ps1
```

Enable the automatic implicit OpenXR layer:

```powershell
.\scripts\register-openxr-layer.ps1
```

The GUI also enables the layer automatically when OpenXR output is selected and the native DLL is available. If anything goes wrong, disable the layer with:

```powershell
.\scripts\unregister-openxr-layer.ps1
```

Set `VRTREAD_OPENXR_DISABLE=1` before launching a target app to force the layer to pass through all OpenXR calls unchanged. Diagnostic overrides exist for development (`VRTREAD_OPENXR_OVERRIDE_ALL_VECTOR2=1`, `VRTREAD_OPENXR_ALLOW_UNBOUND_ACTIONS=1`), but the default implicit mode is conservative so menus and runtime helper processes are not broadly overridden.

## Build the executable

```powershell
.\scripts\build-exe.ps1
```

The standalone app is written to:

```text
dist\VRTreadmill.exe
```

## Build the installer

Install Inno Setup 6, then run:

```powershell
.\scripts\build-installer.ps1
```

The installer is written to:

```text
dist\VRTreadmill-Setup.exe
```

The installer is per-user, does not require admin rights, and removes the app's Windows startup registry value on uninstall. ViGEmBus remains an external prerequisite.

The build uses the upstream `vgamepad` `v0.1.3` GitHub tag because the PyPI `0.1.0` package prompts for ViGEmBus driver installation during Windows dependency installation, which is not suitable for automated CI builds.

## Automated builds

Every push to `main` runs the Windows build workflow. The workflow:

1. Runs the unit tests.
2. Builds `VRTreadmill.exe`.
3. Builds `VRTreadmill-Setup.exe`.
4. Uploads both as workflow artifacts.
5. Updates the [`latest` GitHub Release](https://github.com/TimStewartJ/vr-treadmill/releases/tag/latest) with both downloads.

Use **`VRTreadmill-Setup.exe`** as the recommended end-user download.

## Known limitations

- The current MVP uses cursor recentering. A more polished version should switch to Windows Raw Input so the cursor does not need to be moved.
- Some VR games do not accept a separate Xbox controller at the same time as VR motion controllers.
- The OpenXR layer only affects OpenXR apps. It deliberately skips inactive actions and actions that look like menus, UI, teleport, dashboard, or system controls, but a game that reuses the same locomotion action inside its own menu may still need a future per-game profile.
- This only maps vertical treadmill motion to left-stick Y for now.

## Product roadmap

1. Replace cursor recentering with Raw Input or a safer explicit capture mode.
2. Add calibration for walk, jog, and sprint speeds.
3. Save per-game profiles under `%APPDATA%\VRTreadmill`.
4. Add clearer controller/ViGEmBus diagnostics.
