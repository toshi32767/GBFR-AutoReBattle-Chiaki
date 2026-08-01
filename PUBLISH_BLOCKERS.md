# Public GitHub publishing checklist

This source snapshot is appropriate for private backup and code review. It is
not yet a legally publishable public fork because `xinbaji/GBFR_AutoReBattle`
has no license file or explicit redistribution grant.

Before creating a public GitHub repository:

1. Obtain written permission from the GBFR_AutoReBattle copyright holder, or
   wait for an explicit upstream license covering the revision used here.
2. Keep that license verbatim at the repository root and update `NOTICE.md`
   with the grant/revision.
3. Audit the vendored `module/rapidocr_onnxruntime` source and ONNX models;
   retain each applicable license and notice.
4. Do not commit `Chiaki/`, any Chiaki EXE/DLL, PSN redirect URL, AccountID,
   registration data, stream captures, logs, or Windows crash reports.
5. If distributing Chiaki with a release, comply with its AGPL-3.0-or-later
   obligations, including the corresponding source and all required notices.
6. Do not call the project official, affiliated with Cygames, Sony, PlayStation,
   or Chiaki; use the disclaimer in the README and release notes.
7. Run `scripts/verify_publish_tree.ps1` immediately before `git add` and
   inspect every staged file with `git diff --cached --stat`.

Until item 1 is resolved, keep the repository private. GitHub's default
"no license" status does not grant anyone permission to reuse the upstream
code, even though it is visible on GitHub.
