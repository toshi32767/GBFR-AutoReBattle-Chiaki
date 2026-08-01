# Changelog

All notable user-facing changes are recorded here. The public version number
uses semantic versioning; the older local build number is retained for support.

## v0.1.0 - 2026-08-01 (Windows build V26)

- Unified the Chiaki launcher, automation controls, background-mode checks,
  PSN AccountID helper, and live log in one Windows interface.
- Added Windows Graphics Capture and virtual DualShock 4 background operation.
- Added automatic rebattle, verified result-screen continuation, and battle-only
  target recovery using the skill-state timer, turn/search arc, and one L2 press.
- Added `F3` pause/resume with immediate release of all automation inputs.
- Added session battle counts, per-battle duration statistics, and stop limits by
  battle count, elapsed minutes, or daily clock time.
- Reduced capture and OCR load, disabled ONNX idle spinning, and fixed mixed
  Windows code-page output in packaged logs.
- Added Chiaki window-recreation detection and automatic background-capture
  rebinding.

## Publishing status

This is a private release candidate, not authorization for public
redistribution. See `PUBLISH_BLOCKERS.md` before publishing source or binaries.

## Post-V26 maintenance

- Replaced the ambiguous automatic-stop labels with an explicit applyable
  settings panel: completed battles, runtime in minutes, and local-clock close
  time (`HH:MM`). Changes can be applied while a run is active.
- Removed the skill-monitor detail switch and its high-frequency diagnostic
  lines; detection remains enabled internally.
- Added clear background-mode lock text explaining that stopping the run and
  clearing the checkbox switches back to foreground operation.
- Simplified the Chiaki mapping dialog to list only the keys that must be
  changed.
- Reduced statistics persistence and GUI refresh work from sub-second polling to
  one-second updates, and removed a duplicate statistics write per cycle.
- Clarified runtime-limit semantics: the run timer starts when the automation
  process starts, while each battle's duration remains a separate statistic.
