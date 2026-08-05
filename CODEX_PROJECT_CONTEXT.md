# GBFR AutoReBattle-Chiaki Project Context

This is the active source project for the GBFR AutoReBattle + Chiaki Windows tool.

## Workspace

- Source root: `C:\Users\Long\Documents\Codex\2026-07-29\ban\work\GBFR_AutoReBattle-Chiaki`
- Git branch: `main`
- Keep existing V31, V32, V33, and V34 release directories and ZIP files. Do not overwrite release versions.
- Source changes are intentionally uncommitted while the current development work is being verified.

## Current product state

- Chinese and Japanese OCR models are under `module\rapidocr_onnxruntime\models`.
- Chiaki window title capture and fallback window filtering have been implemented.
- V31/V34 packaging and diagnostics were previously completed.
- A direct EXE launch now enters the unified GUI in the latest source.

## Current unresolved issue

After battle completion, two consecutive Chinese result/continue pages were not recognized and Cross was not sent.

The latest investigation found that the right-bottom crop was too wide. It merged the countdown and the Continue label into one OCR line, for example:

`：08时8继线`

The current source already adds `继线` as a Chinese OCR variant. The next step is to finish the focused regression test and, preferably, restore a detector-based or narrower crop for the Continue control so the countdown and Continue label are separated. Verify both Chinese result pages and the Japanese `次へ` path before packaging.

## Development rules

- Inspect the current source and git diff before editing.
- Prefer focused source tests and existing screenshots/video frames.
- Do not rebuild or package until the OCR/state-machine fix is verified.
- Use the next unused sequential release version for any future package; never overwrite an older version.
