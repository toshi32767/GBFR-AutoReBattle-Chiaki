# GBFR AutoReBattle - Chiaki

Windows automation tool for **Granblue Fantasy: Relink** through a Chiaki PS5
Remote Play stream. It captures the Chiaki stream, uses OCR and image checks to
identify the town, battle, and result phases, and sends controller input through
the configured foreground or virtual-controller backend.

This is a private fan utility. It is not affiliated with Cygames, Sony,
PlayStation, or Chiaki. Use it at your own risk and follow the game and service
terms.

## Download Or Source

End users should download a complete Windows release ZIP. Extract the entire
ZIP to a normal folder, keep the `GBFR_AutoReBattle` directory beside the
launcher, and do not run the EXE from inside the ZIP preview. Chiaki itself is
not included in this source repository; obtain a compatible Chiaki build
separately and place it where the launcher can find it.

This repository is intentionally private. The upstream project does not expose
a redistribution license, and Chiaki has separate AGPL obligations. Read
`PUBLISH_BLOCKERS.md`, `NOTICE.md`, `CORRESPONDING_SOURCE.md`, and
`THIRD_PARTY_NOTICES.md` before publishing a binary or making the repository
public. Never commit a Chiaki binary, PSN data, runtime logs, screenshots from a
private session, or a local driver installer.

## First Run

1. Start `启动工具.cmd` and accept the normal Windows administrator prompt.
2. Set the Chiaki executable path, or use the automatic search/browse button.
3. Start Chiaki, connect to the PS5, and leave the game stream open.
4. Click `一键同步输入配置` once to read the current Chiaki keyboard mapping.
5. Choose `自动识别`, `简体中文`, or `日文` for the game language. Automatic
   mode selects a reliable marker and keeps the matching OCR route.
6. For background operation, enable `后台运行` and click `检查后台环境`.
   Install the requested ViGEmBus component if it is missing. HidHide is
   optional and is only needed when a physical controller causes duplicate
   input.
7. Click `启动自动重战`. The same button becomes the stop control while the
   task is running. `F2` is the emergency stop; `F3` pauses and resumes while
   releasing automated input.

Keep Chiaki connected and preserve its 16:9 aspect ratio. Do not minimize the
stream window: some Chiaki/OpenGL configurations stop presenting frames when
minimized. Background mode allows other windows to cover Chiaki, but it still
needs a live, non-minimized stream. If the stream window is closed and reopened,
the automation can rebind to the new matching stream window.

## What The Tool Handles

- Town flow: move to the task center, interact with the NPC, accept the saved
  task, and enter battle.
- Battle flow: forward movement, skill checks, lock-on/search strategies, and
  resolution-aware frame capture.
- Result flow: enable/reconfirm rebattle, handle continue prompts in Chinese or
  Japanese, and perform the required Cross/Box/Moon actions only in result
  contexts.
- Recovery flow: reconnect Chiaki when configured, return to a recognized town,
  battle, or result phase, and hand control back to the corresponding state
  machine. Stopping the task clears the automation state so a later normal
  rebattle run does not inherit a stale phase.
- Ability reroll: an independent feature with its own configuration window,
  threshold combinations, star calculation, journal, and qualified-result
  notification.
- Session statistics: completed battles and timing are stored locally under
  `%LOCALAPPDATA%\GBFR-AutoReBattle\logs`.

## Resolution And Language

The recognition regions are normalized to the Chiaki client area. Use one of
the supported 16:9 presets when possible: 360p, 540p, 720p, or 1080p. If the
Chiaki client is resized during a run, stop or pause briefly and let the tool
rebind/calculate the new client area before continuing. Very small or distorted
windows reduce OCR accuracy even when the aspect ratio is correct.

Chinese and Japanese use the same phase transitions and input decisions, while
their OCR routes and fallback image checks differ. Do not mix a manually forced
language route with the other client language.

## Ability Reroll

Open the independent ability-reroll configuration from the main panel. Select
one to four attributes, set per-attribute minimum stars, total-star thresholds,
alternative accepted combinations, MSP stop rules, and whether a qualified
result should be automatically overwritten. The journal records recognized
attributes and calculated stars; the table view is intended for discovering
OCR variants and validating new wording before changing matching rules.

The reroll state machine is separate from normal rebattle. Stop it before
starting normal rebattle, and use its clear-history action only when old journal
data is no longer needed.

## Troubleshooting

- No frame is detected: confirm Chiaki is connected, not minimized, and that the
  captured stream title/path is correct. Use the recapture action after a
  window resize or replacement.
- Background mode fails: click `检查后台环境`, install only the missing
  component, then restart the tool if Windows requests it.
- Input goes in the wrong direction: use the reverse-movement option only after
  verifying the Chiaki mapping, then restart automation.
- The log stops at a transition: stop with `F2`, capture the relevant log and
  screenshot with PSN/account/network data removed, and include resolution,
  language, Chiaki version, Windows version, and the exact reproduction steps.

Runtime logs and settings are local machine data. Do not upload them unchanged:
they may contain account identifiers, host addresses, or authorization URLs.

## Development

Requirements: Windows 10/11, PowerShell 7, and Python 3.10 or later.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python .\main.py --diagnostics
python .\main.py --gui
```

`Shapely` is required by the vendored RapidOCR detector and is declared in
`requirements.txt`. The diagnostics command reports missing runtime packages
individually.

The main orchestration remains in `main.py`; the smaller responsibilities are
under `module/`:

- `module/ability_reroll.py`: independent ability-reroll state and matching;
- `module/controller.py`: foreground/background controller input;
- `module/rapidocr_onnxruntime/`: bundled OCR runtime and models;
- `module/psn_account.py`: PSN AccountID helper;
- `scripts/`: development, verification, and release helpers;
- `tests/`: focused state-machine and reroll tests.

Run focused checks with:

```powershell
python -m pytest -q
.\scripts\verify_publish_tree.ps1
```

When changing recognition, preserve the Chinese and Japanese phase routes,
keep actions guarded by the current phase, add a fixture or focused test, and
record the user-visible behavior in `CHANGELOG.md`. Use the existing release
script for packaging; do not commit `build/`, `dist/`, release folders, ZIPs,
or local runtime data.

## License And Notices

No public license is granted for this derivative source. Keep this repository
private unless the rights holder grants permission. See `NOTICE.md`,
`SECURITY.md`, `PUBLISH_BLOCKERS.md`, and `THIRD_PARTY_NOTICES.md` for the
current restrictions and notices.
