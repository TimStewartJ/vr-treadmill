# VR Treadmill

Windows Python app for turning treadmill mouse movement into a virtual Xbox 360 left-stick input.

## Current approach

- Reads vertical mouse movement while capture mode is active.
- Recenters the cursor so the treadmill can keep producing motion.
- Sends left-stick Y to a virtual Xbox 360 controller through `vgamepad`.
- Requires ViGEmBus to already be installed.
- Does not require FreePIE, vJoy, or x360ce.

This works only when SteamVR and the target game accept a normal Xbox/XInput controller as an input source.

## Setup

```powershell
cd E:\vr-treadmill
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

## Run the small Windows app

```powershell
.\.venv\Scripts\vrtread-gui.exe
```

The app has Start/Stop controls, a live stick meter, and tunable sensitivity/decay/deadzone/update-rate values. Press **F8** as an emergency stop while capture mode is active.

Closing or minimizing the window hides it to the system tray. Use the tray icon to show the window, start/stop capture, or fully exit.

Enable **Start with Windows** to register the app in the current user's Windows startup list. This also enables **Start minimized to tray** so boot does not open the full window. Settings are saved under `%APPDATA%\VRTreadmill\settings.json`.

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

## Known limitations

- The current MVP uses cursor recentering. A more polished version should switch to Windows Raw Input so the cursor does not need to be moved.
- Some VR games do not accept a separate Xbox controller at the same time as VR motion controllers.
- This only maps vertical treadmill motion to left-stick Y for now.

## Product roadmap

1. Replace cursor recentering with Raw Input or a safer explicit capture mode.
2. Add calibration for walk, jog, and sprint speeds.
3. Save per-game profiles under `%APPDATA%\VRTreadmill`.
4. Add clearer controller/ViGEmBus diagnostics.
