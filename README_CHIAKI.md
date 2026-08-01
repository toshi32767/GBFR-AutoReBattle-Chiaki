# GBFR AutoReBattle - Chiaki Stream Adapter

This is a local-use adapter of `GBFR_AutoReBattle` for Chiaki PS5 Remote Play.
It keeps the original OCR battle loop, but captures the visible Chiaki stream
window and sends controller-mapped keyboard events to Chiaki.

## Chiaki settings

In Chiaki Settings, set these key mappings before starting the adapter:

- `Left Stick Up`: `W`
- `Left Stick Down`: `S`
- `Left Stick Left`: `A`
- `Left Stick Right`: `D`
- `Right Stick Left`: `Q`
- `Right Stick Right`: `E`
- `Cross`: `Return`
- `R1`: `3`
- `L2`: `L`

The other mappings can stay at their defaults. Recommended stream settings are
1080p, 60 FPS, H265, and `d3d11va` hardware decoding for an AMD GPU.

After the battle-entry OCR marker is detected, the adapter presses L2 once to
lock the target. It intentionally does not press L2 repeatedly because some
game configurations toggle lock-on each time the button is pressed. If L2 is
mapped to another keyboard key, pass it with `--l2-key`, for example
`--l2-key "2"`.

During battle, the adapter monitors the upper and right-side skill diamonds. If
either remains bright for 15 seconds, it treats that as a possible lost target.
It releases forward movement, briefly holds Left Stick Down plus an alternating
left/right movement and camera sweep. This forms a search arc that can bring an
off-screen target back into view. It sends L2 once while the turn/search arc is
still in progress, then releases both sticks and waits 1.5 seconds before
checking two consecutive frames. The checks verify the result of that in-turn
lock; they never send a second L2, because a second press can toggle a
successful lock off. The next normal battle action resumes forward movement.
A short brightness drop caused by battle effects is tolerated
for up to 5 seconds; the timer resets only after the trigger skills stay dark
for that grace period.
The recovery is disabled outside the confirmed battle phase, so it cannot carry
movement or L2 into the result screen.
The upstream middle-mouse action is Relink's lock-on command, whose controller
equivalent is L2. It must not be mapped to the DS4 Touchpad: that mapping can
open Relink's command wheel, dim the complete HUD, and prevent reliable skill
monitoring. If Chiaki is closed and a new stream window with the same title is
opened, background capture automatically rebinds to its new window handle while
the selected background mode remains unchanged.

## Intro skipping

The adapter does not OCR or operate the battle-intro `跳过` prompt and never
sends Backspace. Enable Relink's own automatic intro-skip option instead. This
keeps unrelated combat and result inputs from interfering with a skip dialog.

## Important window requirements

- Connect to the PS5 and open the game in a stream window titled `Chiaki | Stream`.
- Keep the Chiaki stream visible and unobscured. Do not minimize it. This adapter
  uses a desktop capture because QOpenGLWidget frames are not reliably returned
  by Windows `PrintWindow`.
- Use the game's Simplified Chinese UI because the OCR regions look for Chinese
  labels such as `跳跃`, `再次`, `继续`, `挑战`, and `结算`.
- Keep the stream window at the same aspect ratio as the PS5 output. The OCR
  regions are normalized and work with ordinary 16:9 window sizes.

## Run

Open an elevated PowerShell in this directory and install the dependencies:

```powershell
py -3.10 -m pip install -r requirements.txt
py -3.10 .\main.py
```

Press `F1` to start the loop, `F2` to stop it, and `F3` to pause/resume without
resetting the current battle phase. The pause releases all automated buttons
and virtual-stick axes immediately. The `--silent` option is kept
for compatibility with the upstream project, but the Chiaki window must still
remain visible for screen capture:

```powershell
py -3.10 .\main.py --silent
```

If the stream window title differs, pass its visible title explicitly:

```powershell
py -3.10 .\main.py --window-title "Chiaki | Stream"
```

## Unified launcher and background mode

The bundled tool includes a simple control panel. It starts the included
Chiaki build, starts/stops the automation engine, and obtains PSN AccountID in
one place while keeping Chiaki's native host and stream UI. Keep the `Chiaki`
folder next to the automation executable and run:

```powershell
.\GBFR_AutoReBattle.exe --gui
```

The panel requests administrator permission once when it starts. Accept the
Windows UAC prompt; Chiaki and the automation child then inherit the same
permission level, so the panel can monitor and stop the correct process.

The panel's `获取 PSN AccountID` button opens Sony login, accepts the redirect
URL, and copies the resulting ID to the clipboard. The same flow is available
without the panel via `--account-id`.

The background mode uses Windows Graphics Capture for the Chiaki window and a
virtual DualShock 4 created through `vgamepad` / ViGEmBus. This avoids relying
on Qt window messages, which some Chiaki builds ignore while the stream is not
focused. Other windows may cover Chiaki and the automation will not call
`SetForegroundWindow`. Do not minimize Chiaki: minimized OpenGL/remote-play
windows can stop presenting frames, in which case OCR cannot observe state
changes.

Use the panel's `检查后台环境` button before enabling background mode. It verifies
that Windows Graphics Capture and the ViGEm virtual-controller driver are both
working. The driver installer requires a normal Windows UAC confirmation and
must not be installed silently. The first run should be tested with F2 ready
because controller behavior can differ between Chiaki builds.

For convenience, `启动工具.cmd` opens the panel. `启动后台自动重战.cmd`
starts both components directly with background mode, while
`启动自动重战.cmd` keeps the original attach-to-an-existing-Chiaki behavior.

## Packaged EXE

Extract the complete ZIP and keep the `GBFR_AutoReBattle` folder next to this
README. Double-click `启动自动重战.cmd`, or run the EXE directly:

```powershell
.\GBFR_AutoReBattle\GBFR_AutoReBattle.exe
.\GBFR_AutoReBattle\GBFR_AutoReBattle.exe --window-title "Chiaki | Stream"
```

The re-focus duration can be changed for testing. Values below 5 seconds are
clamped to 5 seconds:

```powershell
.\GBFR_AutoReBattle\GBFR_AutoReBattle.exe --refocus-seconds 15
```

The unified panel shows each completed battle duration and can stop after a
number of battles, a number of running minutes, or a daily `HH:MM` time. The
same limits are available through `--max-battles`, `--max-runtime-minutes`,
and `--stop-at`; when a limit is reached, the tool releases input and asks the
Chiaki process to close. Session data is written to
`%LOCALAPPDATA%\GBFR-AutoReBattle\logs\session-stats.json`.

Do not move the EXE out of its folder; the `_internal` directory contains its
DLLs and OCR runtime files.

The automation is game-specific and should be tested with F2 ready to stop it.
It does not access PSN credentials or the Chiaki registration database.
