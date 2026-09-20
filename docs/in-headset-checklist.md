# In-headset checklist (about five minutes)

Everything that can be checked without a headset is automated. This is the part that cannot be: a real game, on
your real runtime, with the headset on. The layer writes a log per game, so each step tells you what to look for
instead of asking you to judge by feel.

Log folder: `%LOCALAPPDATA%\VRTreadmill\logs` (one `openxr-layer-<game>.exe.log` per game). The same information
is summarised on the app's **Output** tab and under **Diagnostics…**.

## Before you put the headset on

1. Install the new build. If the old version is running, the installer asks to close it (tray icon → Exit).
2. **Output tab → OpenXR.** The status line should read *enabled*. This also removes the old development
   registration (`E:\vr-treadmill-openxr\dist\…`) if it is still there.
3. **Diagnostics…** should show: layer enabled, *"The layer DLL loads and negotiates correctly"*, and your active
   OpenXR runtime (SteamVR).
4. Press **Start** and move the belt: the meter follows. Leave capture running.

## In the headset

Pick an OpenXR game with smooth locomotion on the left stick. If you are unsure whether a game uses OpenXR, the log
settles it: no log file for that game means the OpenXR loader never loaded the layer there (OpenVR-only game, or
the game runs as administrator).

| # | Do this | Expected | If not |
| --- | --- | --- | --- |
| 1 | Start the game, stand still. | Log has `layer 0.2.0 attached: exe='…' … mode=active`. Nothing moves. | `mode=passthrough reason=…` explains itself. No log at all: not an OpenXR game. |
| 2 | Stand still, look at the log. | A line `decision action='…' hand=left … -> drive`. That is the input the treadmill will drive. | Only `skip:` lines: the game names or binds movement in a way the filter does not expect. Try **Compatibility** on the Output tab, and keep the log for a bug report. |
| 3 | Walk. | You move forward. Log gains `engaged: driving action='…'`. | Meter moves but the game does not: check step 2. |
| 4 | Walk, and push the **right** stick around. | Turning works exactly as before. There must be **no** `hand=right … -> drive` line. | This was the prototype's bug and should be impossible now. If you see it, that log is a bug report. |
| 5 | Stop walking. | You stop within a fraction of a second, no drift. The left stick works normally again. | Raise **Noise filter** slightly if the belt creeps. |
| 6 | While standing still, push the left stick. | Normal stick movement (the layer does not touch the stick while the belt is idle). | |
| 7 | Walk, then press **F8**. | You stop immediately. | |
| 8 | Walk, then open the SteamVR dashboard. | Movement stops while the dashboard is open and resumes after. | |
| 9 | Walk with a menu open in the game. | Ideally nothing. Some games reuse the movement input for menus and will scroll; that is a per-game limitation. | |
| 10 | Quit the game. | Log ends with `session destroyed: N treadmill-driven reads` and `instance destroyed`. | |

Optional: kill `VRTreadmill.exe` from Task Manager while walking. You should stop within about a quarter of a second
(the layer ignores treadmill data older than 250 ms).

## If a game misbehaves

- Set `VRTREAD_OPENXR_BYPASS=1` for that game (Steam → Properties → Launch options:
  `cmd /c "set VRTREAD_OPENXR_BYPASS=1 && %command%"`) to keep the layer passive, or press **Disable** on the Output
  tab to take it out of every game.
- The log for that game plus the text from **Diagnostics…** is everything needed to debug it.
