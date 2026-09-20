# The OpenXR layer: design, safety rules, evidence

`native/openxr_layer` builds `vrtread_openxr_layer.dll`, an OpenXR **implicit API layer**. The OpenXR loader inside
every OpenXR application loads it automatically. It sits between the game and the runtime (SteamVR, Meta, …) and,
while the VR Treadmill app is capturing and the belt is moving, replaces the Y axis of the game's left-thumbstick
movement input with the treadmill value.

```
treadmill sensor ─► VR Treadmill app (Python) ─► shared memory ─► layer DLL (inside the game) ─► game
     Raw Input          motion model, 100 Hz       64-byte seqlock      xrSyncActions / xrGetActionState*
```

## Rules the layer is built around

A DLL that loads into every OpenXR game on a machine has to be boring. In order of priority:

1. **Never break the host.** Every entry point contains exceptions. Every failure path degrades to "pass the call
   through untouched". Handles the layer has no record of are adopted rather than rejected. The layer hooks 13
   functions and hands every other function pointer out unwrapped.
2. **Never fail to load.** See [the loader facts](#loader-facts-that-shaped-the-design) below: a layer that cannot be
   loaded can stop a game from starting. The DLL links the CRT statically and imports only `KERNEL32.dll`; the
   build script fails if that ever changes.
3. **Never leave the player walking.** A shared-memory block older than 250 ms is ignored, so a crashed or hung
   app releases the player within a quarter of a second. Stopping capture publishes "inactive" immediately.
4. **Follow OpenXR semantics.** Shared memory is sampled once per `xrSyncActions`, so repeated reads within a frame
   agree; `changedSinceLastSync` and `lastChangeTime` are reported truthfully, including on the frame where the
   treadmill hands the stick back.

## Which input gets driven

Engines use the action system very differently, so the binding the game suggested is the primary evidence, the
**subaction path selects the hand**, and the action name is only a tie-breaker and a safety filter
(`include/vrtread_policy.h`, pure logic, unit-tested):

| Engine | What it does | Consequence |
| --- | --- | --- |
| Unity | One generic action `thumbstick` with subaction paths for **both** hands, read once per hand. | Only the subaction path tells the hands apart. (The prototype ignored it and drove the right hand too.) |
| Unreal, legacy input | Float actions such as `moveforward` bound to `…/thumbstick/y`, read with `XR_NULL_PATH`. | Float actions bound to the left stick's Y component are driven. |
| Unreal enhanced input, Godot, native | Vector2 actions bound to `…/thumbstick`, with or without subaction paths. | Driven for the left hand, or for a combined read when only the left stick is bound. |

Never driven: right-hand or other subaction reads, actions whose name says menu / turn / teleport / scroll / UI,
actions that are not bound to the left stick. `…/trackpad` counts as the stick (SteamVR serves Vive-wand bindings
from the Touch thumbstick). Filter modes *strict* and *compatibility* tighten or loosen the name requirement.

By default the treadmill is combined with the physical stick by "whichever is pushed further", the result is kept
inside the unit circle, and an action the runtime reports as inactive is left alone. The opt-in "keep walking when
the left controller is asleep" only engages when the action's set was synced, some other input in the session is
live (the session is focused), and no higher-priority active action set is bound to the left stick (that is how
OpenXR hands the stick to a pause menu).

## Loader facts that shaped the design

All of these were read in the Khronos loader source (1.0.9, 1.0.26 and 1.1.63 behave identically) and then
demonstrated by a test.

- **If no API layer could be loaded and at least one failed, `xrCreateInstance` fails** (`XR_ERROR_FILE_ACCESS_ERROR`):
  the game does not start in VR. Hence the static CRT and the import check.
- **`HKCU\Software` is not WOW64-redirected**, so 32-bit OpenXR processes see the manifest of this x64-only DLL, and
  for them it *cannot* load. The manifest therefore sets `"disable_environment": "PROCESSOR_ARCHITEW6432"`.
  Windows defines that variable in every 32-bit process, which makes every loader version skip the layer there.
  Windows also strips it from 64-bit processes even when a parent passes it explicitly, so nothing can switch the
  layer off in a 64-bit game by accident. `tests/test_installed_layer.py` runs a genuine 32-bit client with the real
  32-bit loader: it starts fine with this manifest, and fails with `-32` with the same DLL behind an unprotected
  manifest (which is what the unreleased prototype registered).
- **An implicit layer beats an explicit one of the same name.** The test harness therefore suppresses any installed
  copy, and checks which DLL the loader really loaded; and the app removes every other registration of this layer
  when it enables its own, so two copies never inject at once.
- **Implicit layers are found through the registry only** on Windows, and a relative `library_path` is resolved
  against the manifest. So the manifest is a static file next to the DLL, and the installed path is validated by
  really registering it (opt-in test, registry restored afterwards).
- **Elevated processes ignore HKCU.** A game run "as administrator" will not load the layer.

## Deployment

The DLL and manifest ship inside the app (PyInstaller data, never UPX-packed). `vrtread.openxr.enable_layer()`
copies them to `%LOCALAPPDATA%\VRTreadmill\openxr_layer\<version>-<dll hash>\` and registers that manifest. The
folder name contains the DLL's hash, so an update never overwrites a DLL that a running game has locked; old
folders are removed when nothing holds them. Uninstalling unregisters the layer and deletes the files.

## Shared memory contract (v2)

`include/vrtread_shared_memory.h` and `src/vrtread/outputs.py` define the same 64-byte little-endian block,
`Local\VRTreadmillOpenXRState_v2`; a Python test parses the C++ header to keep them in step. Seqlock: the writer
sets `seq` odd, writes, sets it even; the reader retries on odd or changed `seq`, falls back to its previous block
under contention, and is patient only on the very first read of a session (when there is no previous block).
Timestamps are `GetTickCount64` on both sides. A publisher mutex refuses a second running copy of the app.

## Environment variables

| Variable | Effect |
| --- | --- |
| `VRTREAD_OPENXR_BYPASS=1` | Layer stays loaded but passes everything through (set it for one game to rule the layer out). |
| `VRTREAD_OPENXR_LOG=0` | No per-game log file. |
| `VRTREAD_OPENXR_DEBUG=1` | Also write log lines to the debugger output. |
| `VRTREAD_OPENXR_LOG_DIR`, `VRTREAD_OPENXR_MAPPING` | Test isolation. |
| `VRTREAD_OPENXR_ALLOW_SYSTEM_PROCESSES=1` | Do not skip VR runtime helper processes. |

## How it is tested

| Level | What | Where |
| --- | --- | --- |
| Pure logic | Binding/name classification, decision table, combine modes | `tests/test_policy.cpp` |
| Real loader, mock runtime | Engine-style scenarios, safety, seqlock, threading, overhead, lifecycle; verifies which DLL got loaded | `tests/test_layer.cpp` |
| Direct, no loader | Negotiation edge cases, `xrGetInstanceProcAddr`, two simultaneous instances | `tests/test_negotiation.cpp` |
| Header ABI | `openxr_minimal.h` against the official headers, 139 sizes/offsets/constants | `tests/abi_probe.cpp` |
| Cross-language | Real Python publisher and engine → layer → native game; stop and hang behaviour | `tests/test_native_e2e.py` |
| Installed path | Registry discovery by the real loader, packaged exe (byte-identical DLL), genuine 32-bit client | `tests/test_installed_layer.py` (opt-in) |

The harness was checked by mutation: reintroducing the prototype's "ignore the subaction path" bug fails six tests.

### Checking against a real runtime without a headset

```powershell
native\openxr_layer\build\tests\bin\vrtread_layer_tests.exe --real-runtime active
```

creates an instance on the machine's active runtime (or on a given runtime JSON) with the layer build under test
loaded explicitly, and exercises every instance-level call the layer hooks: no session, so no headset is needed,
but SteamVR starts if it is not running. The printed log folder then shows whether the layer attached in
`mode=active`, i.e. whether the real runtime provided every function it needs. A runtime that is unavailable is a
valid outcome: its error must come back through the layer unchanged.
The mock runtime models the OpenXR input semantics the layer relies on (subactions, combined reads, `isActive`,
set priority, focus). It is not SteamVR. What remains for a human is in
[in-headset-checklist.md](in-headset-checklist.md).
