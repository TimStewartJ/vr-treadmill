# VR Treadmill

[![Build Windows app](https://github.com/TimStewartJ/vr-treadmill/actions/workflows/build-windows.yml/badge.svg)](https://github.com/TimStewartJ/vr-treadmill/actions/workflows/build-windows.yml)

Turn treadmill mouse movement into a virtual Xbox 360 left stick for Windows VR games.

VR Treadmill is for setups where a treadmill or mouse-like sensor reports forward/back movement. The app converts that movement into Xbox left-stick Y input through ViGEmBus, so games and SteamVR can see it as a normal Xbox controller.

## Download and install

Most users do **not** need Python.

1. Open the [latest VR Treadmill build](https://github.com/TimStewartJ/vr-treadmill/releases/tag/latest).
2. Download **`VRTreadmill-Setup.exe`**.
3. Run the installer.
4. Open **VR Treadmill** from the Start menu.
5. If the app says ViGEmBus is missing, click **Install/Open ViGEmBus Driver**, install the driver, then reopen VR Treadmill.

Advanced users can download **`VRTreadmill.exe`** instead for a portable single-file app.

## What it does at a glance

- Creates a virtual Xbox 360 controller.
- Converts treadmill/mouse forward/back movement into left-stick forward/back movement.
- Gives you beginner-friendly tuning presets.
- Runs from the system tray if you want it out of the way.
- Can start with Windows and launch minimized.
- Does **not** require FreePIE, vJoy, x360ce, or Python for normal users.

## Quick start

1. Confirm the **Driver** section says ViGEmBus is installed/running.
2. Leave tuning on **Balanced**.
3. Click **Start treadmill capture**.
4. Check that the stick meter moves when you walk/move the treadmill sensor.
5. Open SteamVR/controller bindings and bind the Xbox/Gamepad left stick to movement for your game.
6. Press **F8** any time as an emergency stop.

## Current approach

- Reads vertical mouse movement while capture mode is active.
- Recenters the cursor so the treadmill can keep producing motion.
- Sends left-stick Y to a virtual Xbox 360 controller through `vgamepad`.
- Requires ViGEmBus to already be installed.
- Does not require FreePIE, vJoy, or x360ce.

This works only when SteamVR and the target game accept a normal Xbox/XInput controller as an input source.

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

The **Driver** section shows whether ViGEmBus appears installed/running and includes an **Install/Open ViGEmBus Driver** button that opens the official ViGEmBus releases page.

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
.\.venv\Scripts\vrtread-cli.exe
```

Press **Ctrl+C** to stop.

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
- This only maps vertical treadmill motion to left-stick Y for now.

## Product roadmap

1. Replace cursor recentering with Raw Input or a safer explicit capture mode.
2. Add calibration for walk, jog, and sprint speeds.
3. Save per-game profiles under `%APPDATA%\VRTreadmill`.
4. Add clearer controller/ViGEmBus diagnostics.
