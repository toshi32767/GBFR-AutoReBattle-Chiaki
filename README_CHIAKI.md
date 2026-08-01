# GBFR AutoReBattle - Chiaki Stream Adapter

This is a local-use adapter of `GBFR_AutoReBattle` for Chiaki PS5 Remote Play.
It keeps the original OCR battle loop, but captures the visible Chiaki stream
window and sends controller-mapped keyboard events to Chiaki.

## Chiaki settings

In Chiaki Settings, set these key mappings before starting the adapter:

- `Left Stick Up`: `W`
- `Cross`: `Return`
- `R1`: `3`
- `Touchpad`: `T`
- `L2`: `L`
- `Moon/skip`: `Backspace` (the keyboard key sent to open the skip prompt)

The other mappings can stay at their defaults. Recommended stream settings are
1080p, 60 FPS, H265, and `d3d11va` hardware decoding for an AMD GPU.

After the battle-entry OCR marker is detected, the adapter presses L2 once to
lock the target. It intentionally does not press L2 repeatedly because some
game configurations toggle lock-on each time the button is pressed. If L2 is
mapped to another keyboard key, pass it with `--l2-key`, for example
`--l2-key "2"`.

During battle, the adapter samples the four skill diamonds once per second. If
the upper skill or the right-side skill remains bright continuously for 10
seconds, it treats that as a lost target and presses L2 once more. A short
brightness drop caused by battle effects is tolerated for up to 5 seconds; the
timer resets only after the trigger skills stay dark for that grace period.
The detector uses normalized 16:9 coordinates and ignores the brightness of the
other two skills.

## Intro skip confirmation (V5)

When the intro screen shows the `跳过` prompt, V4 presses Backspace to open
the `是否跳过？` menu, then presses Up. It reads the selection bar from the
captured frame and sends Return only when the `是` row is visibly highlighted.
If the stream stutters or the selection cannot be verified, it does not
confirm the menu; this prevents accidentally choosing `否` or closing the
dialog. After sending Return, V5 keeps the input lock and checks that the
`是否跳过？` menu has actually disappeared. If it remains visible, Return is
retried up to four more times before giving up safely.

The `跳过` prompt is monitored by a separate OCR watchdog while the battle has
not started, so it is not sampled only between the normal `跳跃` checks. This
reduces missed prompts when the intro animation or stream frame changes quickly.

## Important window requirements

- Connect to the PS5 and open the game in a stream window titled `Chiaki | Stream`.
- Keep the Chiaki stream visible and unobscured. Do not minimize it. This adapter
  uses a desktop capture because QOpenGLWidget frames are not reliably returned
  by Windows `PrintWindow`.
- Use the game's Simplified Chinese UI because the OCR regions look for Chinese
  labels such as `跳过`, `是否跳过`, `跳跃`, `再次`, `挑战`, and `结算`.
- Keep the stream window at the same aspect ratio as the PS5 output. The OCR
  regions are normalized and work with ordinary 16:9 window sizes.

## Run

Open an elevated PowerShell in this directory and install the dependencies:

```powershell
py -3.10 -m pip install -r requirements.txt
py -3.10 .\main.py
```

Press `F1` to start the loop and `F2` to stop it. The `--silent` option is kept
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
.\GBFR_AutoReBattle\GBFR_AutoReBattle.exe --refocus-seconds 10
```

Do not move the EXE out of its folder; the `_internal` directory contains its
DLLs and OCR runtime files.

The automation is game-specific and should be tested with F2 ready to stop it.
It does not access PSN credentials or the Chiaki registration database.
