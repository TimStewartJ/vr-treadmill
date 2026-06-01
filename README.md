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

## Run the CLI

```powershell
.\.venv\Scripts\vrtread-cli.exe
```

Press **Ctrl+C** to stop.

## Known limitations

- The current MVP uses cursor recentering. A more polished version should switch to Windows Raw Input so the cursor does not need to be moved.
- Some VR games do not accept a separate Xbox controller at the same time as VR motion controllers.
- This only maps vertical treadmill motion to left-stick Y for now.

## Product roadmap

1. Replace cursor recentering with Raw Input or a safer explicit capture mode.
2. Add calibration for walk, jog, and sprint speeds.
3. Save per-game profiles under `%APPDATA%\vrtread`.
4. Add clearer controller/ViGEmBus diagnostics.
5. Package as a standalone Windows `.exe` with PyInstaller.
