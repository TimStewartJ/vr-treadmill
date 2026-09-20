# VR Treadmill

[![Build Windows app](https://github.com/TimStewartJ/vr-treadmill/actions/workflows/build-windows.yml/badge.svg)](https://github.com/TimStewartJ/vr-treadmill/actions/workflows/build-windows.yml)

Walk on a treadmill to move in VR, on Windows.

VR Treadmill is for setups where a mouse-like sensor reports how fast the treadmill belt is moving. It turns that
into forward/back movement in the game in one of two ways:

| Output | How it reaches the game | Use it for |
| --- | --- | --- |
| **OpenXR** (default) | A small OpenXR API layer substitutes the treadmill for the game's left-thumbstick movement. No per-game setup, no driver. | Games that use OpenXR. |
| **Xbox controller** | A virtual Xbox 360 pad (ViGEmBus) with the treadmill on its left stick. | Games that accept a gamepad next to VR controllers, including OpenVR-only games. |

## Install

Most users do **not** need Python.

1. Open the [latest VR Treadmill build](https://github.com/TimStewartJ/vr-treadmill/releases/tag/latest).
2. Download and run **`VRTreadmill-Setup.exe`** (per-user, no admin rights). `VRTreadmill.exe` is the same app as a
   single portable file.
3. Open **VR Treadmill** from the Start menu.

Xbox controller output additionally needs the [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases);
the **Output** tab shows whether it is installed. OpenXR output needs nothing else.

## First-time setup (about two minutes)

1. **Sensor tab → Detect treadmill sensor.** Press it, then walk (or push the belt) for three seconds without
   touching your normal mouse. This selects the one device that is your treadmill, so your desk mouse never moves
   you in game and your cursor stays free.
2. Press **Start**. The meter should follow your walking. If walking forward moves you backward, tick
   **Invert** on the Sensor tab.
3. **Tuning tab → Calibrate walking speed.** Walk at a comfortable pace, press it, keep walking for five seconds.
   That pace now gives 80 % stick; walking faster reaches 100 %.
4. Start an OpenXR game. The **Output** tab shows the last game the layer saw and what it is driving.

**F8** stops capture from anywhere, including inside VR. Closing or minimizing the window hides it to the tray.

### Upgrading from 0.1

Nothing changes by itself: your settings are converted to the exact same speed and feel, output stays on the Xbox
controller, and capture stays on the old cursor-lock method. A one-time note in the window points at the two new
options, **OpenXR** output and **Raw Input** capture (recalibrate after switching capture: Raw Input reads true
sensor counts, without Windows pointer speed or acceleration mixed in).

## Tuning

| Control | Meaning |
| --- | --- |
| **Pace for full speed** | How fast the belt must move for full stick. Lower = you move faster. *Calibrate* sets it. |
| **Smoothing** | Evens out individual steps. Higher is smoother but starts and stops later. |
| **Noise filter** | Belt movement slower than this share of full speed is ignored. |
| **Update rate** | How often movement is sent. It does not affect speed; 100 Hz is plenty. |

Each control changes one thing. (In 0.1 they were coupled: doubling the update rate halved your speed, and
lowering "stop smoothness" also slowed you down.) The **Feel** presets only touch smoothing and the noise filter.

## OpenXR output

While capture is running and the belt is moving, the layer replaces the **Y axis of the game's left-thumbstick
movement input** with the treadmill. Everything else is passed through untouched: the right hand, strafing (X),
buttons, and the left stick itself whenever you are standing still.

Options on the **Output** tab:

- **Which game input to drive** – *Balanced* (default) drives the game's left-stick movement input unless its name
  says menu/turn/teleport. *Strict* additionally requires a movement-like name. *Compatibility* also guesses from
  names for the rare game that hides its bindings.
- **When you also push the thumbstick** – by default whichever is pushed further wins, so the stick keeps working
  (walk backwards, nudge forward) while capture is on.
- **Keep walking when the left controller is asleep** (advanced) – for gun stocks or one-handed play. It never
  engages while the headset is off, the dashboard is open, or a game's pause menu has taken over the stick.

**Disable** on the Output tab (or uninstalling) unregisters the layer; games stop loading it the next time they
start. `VRTREAD_OPENXR_BYPASS=1` in a game's environment keeps the layer loaded but completely passive.

**Diagnostics…** shows the registration, a load test of the layer DLL, the active OpenXR runtime, and what the layer
did in recent games. Each game also gets a small log in `%LOCALAPPDATA%\VRTreadmill\logs`.

What it cannot do:

- It only affects **OpenXR** games. OpenVR-only titles (VRChat, Half-Life: Alyx, Skyrim VR, …) need the Xbox
  controller output.
- A game that reuses its movement input inside its own menus will scroll those menus while you walk.
- If you rebind movement to a different stick in SteamVR's binding UI, the layer cannot see that.
- Games started "as administrator" ignore per-user OpenXR layers. Anti-cheat that blocks unsigned DLLs will keep the
  layer out (the game itself is unaffected).

Design, safety rules and the evidence behind them: [docs/openxr-layer.md](docs/openxr-layer.md).

## Developer setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\build-openxr-layer.ps1          # needs Visual Studio 2022 C++ tools (CMake included)
.\.venv\Scripts\vrtread-gui.exe
```

Other entry points: `vrtread-cli` (engine in a terminal, `--list-devices`), `vrtread-openxr status|enable|disable|doctor`.
Settings live in `%APPDATA%\VRTreadmill\settings.json`.

### Testing

```powershell
.\scripts\test-openxr-layer.ps1
```

builds the layer and runs everything that does not need a headset:

- **Native suite** – the test "game" is an OpenXR client on the **real Khronos loader**, which loads the layer DLL
  exactly as in a game, on top of a mock runtime that models SteamVR's input system. Scenarios mirror how Unity,
  Unreal (legacy and enhanced input), Godot and native engines use actions; plus safety, threading, lifecycle,
  negotiation, and a check of the hand-written OpenXR header against the official one.
- **Python suite** – signal model (including exact equivalence of migrated 0.1 settings), settings, capture,
  engine, GUI smoke tests, and **cross-language end-to-end tests**: the real Python publisher/engine on one side,
  the native game reading through loader + layer on the other.

Two opt-in tests touch the real machine: `VRTREAD_TEST_DESKTOP=1` (Raw Input with synthesized mouse motion, moves
the cursor a few pixels) and `VRTREAD_TEST_REGISTRY=1` (registers the layer for real so the loader discovers it
through the registry like a game does, from the sources and from the packaged exe, and runs a genuine 32-bit
client; the registry is snapshotted and restored). CI runs all of it on every pull request.

What no automated test can cover is a real game on a real runtime with a headset on:
[docs/in-headset-checklist.md](docs/in-headset-checklist.md) is the five-minute manual check.

### Building a release

```powershell
.\scripts\build-openxr-layer.ps1 -Test    # build + validate the layer
.\scripts\build-exe.ps1 -UseBuiltLayer    # dist\VRTreadmill.exe, bundling exactly the DLL that was just tested
.\scripts\build-installer.ps1             # dist\VRTreadmill-Setup.exe (Inno Setup 6)
```

Every push to `main` runs the same steps in CI and updates the
[`latest` release](https://github.com/TimStewartJ/vr-treadmill/releases/tag/latest).

`vgamepad` is pinned to the upstream `v0.1.3` tag because the PyPI `0.1.0` package prompts to install ViGEmBus while
installing, which breaks unattended builds.

## Known limitations

- Forward/back only; strafing stays on the thumbstick.
- A sensor read through Raw Input still moves the Windows cursor while the belt moves (it remains a mouse to
  Windows). It no longer locks the cursor or affects the game, but hiding it entirely would need a driver.
- ViGEmBus, which the Xbox controller output depends on, is
  [end-of-life](https://docs.nefarius.at/projects/ViGEm/End-of-Life/). Existing installs keep working.
