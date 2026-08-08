# GBFR AutoReBattle - Chiaki

Windows automation tool for Granblue Fantasy: Relink through a Chiaki PS5
Remote Play stream.

**[中文说明 / Chinese README](README.md)**

This private fan utility captures the Chiaki stream, recognizes town, battle,
result, and recovery phases, and sends controller input through foreground or
virtual-controller backends. It is not affiliated with Cygames, Sony,
PlayStation, or Chiaki. Use it at your own risk and follow the game and service
terms.

## End-user setup

Download a complete Windows ZIP, extract it to a normal folder, and run
`启动工具.cmd`. Do not run the EXE from the ZIP preview or move it away from
its `_internal` directory. Obtain Chiaki separately and configure its path in
the main panel.

1. Start Chiaki and connect to the PS5.
2. Click `一键同步输入配置` once.
3. Select automatic, Simplified Chinese, or Japanese recognition.
4. For background operation, enable `后台运行` and click `检查后台环境`.
5. Install ViGEmBus only if the environment check requests it. HidHide is
   optional and is only needed for duplicate physical-controller input.
6. Click `启动自动重战`. `F2` stops immediately and `F3` pauses/resumes.

Keep the Chiaki stream connected, visible, non-minimized, and at a 16:9 aspect
ratio. Supported resolution presets are 360p, 540p, 720p, and 1080p. OCR
stability and the user experience can be poor at 360p/540p because streamed
text is small and compression artifacts are stronger. 720p or higher is
recommended for unattended use. After resizing Chiaki, recapture the stream
window or pause briefly so the client area can be recalculated.

## Features

- Town task acceptance and battle entry;
- battle movement, skill monitoring, and selectable search strategies;
- Chinese and Japanese result/rebattle confirmation flows;
- optional Chiaki reconnect and phase recovery;
- independent ability-reroll thresholds, combinations, MSP limits, overwrite
  rules, star calculation, and journal;
- local session statistics under
  `%LOCALAPPDATA%\GBFR-AutoReBattle\logs`.

Stopping automation clears its active state machine so a later normal rebattle
run does not inherit a stale phase. Ability reroll and normal rebattle are
independent tasks and should not be run simultaneously.

## Development

Requirements: Windows 10/11, PowerShell 7, and Python 3.10 or newer.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python .\main.py --diagnostics
python .\main.py --gui
python -m pytest -q
```

`Shapely` is required by the vendored RapidOCR detector and is declared in
`requirements.txt`.

Main edit points:

- `main.py`: GUI, phase state machine, Chiaki capture, and rebattle flow;
- `module/ability_reroll.py`: ability reroll and matching;
- `module/controller.py`: input backends;
- `module/rapidocr_onnxruntime/`: OCR runtime and models;
- `module/psn_account.py`: AccountID helper;
- `scripts/`: development, verification, and release helpers;
- `tests/`: focused state-machine and reroll tests.

Recognition changes must preserve phase guards and Chinese/Japanese routing.
Add a focused test or fixture and document user-visible changes in
`CHANGELOG.md`. Do not commit build folders, release ZIPs, Chiaki binaries,
logs, screenshots, PSN data, or local driver installers.

## Legal and distribution notice

This project does not grant additional rights to third-party components. Any
use, modification, or redistribution must follow the applicable project,
Chiaki, RapidOCR, OCR model, and dependency notices. Read `NOTICE.md`,
`SECURITY.md`, `PUBLISH_BLOCKERS.md`, and `THIRD_PARTY_NOTICES.md` before
redistributing binaries. The tool is not guaranteed to work with every
resolution, Chiaki build, or game-client configuration.
