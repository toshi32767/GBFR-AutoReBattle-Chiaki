# GBFR AutoReBattle - Chiaki Adapter

Current private review build: **v0.1.0 (Windows build V41, 2026-08-03)**.

Windows helper for using the GBFR AutoReBattle workflow through a Chiaki PS5
Remote Play stream. The control panel brings together:

- Chiaki launch and stream-window configuration;
- GBFR OCR automation, battle lock-on, and verified result continuation;
- virtual DualShock 4 input for background operation;
- a PSN AccountID helper that copies only the resulting ID locally;
- an embedded, live runtime log with UTF-8/GB18030 compatibility.

It is an independent fan utility. It is not affiliated with or endorsed by
Cygames, Sony, PlayStation, or Chiaki. Use at your own risk and follow the
game's terms of service.

## Important publishing status

Read [PUBLISH_BLOCKERS.md](PUBLISH_BLOCKERS.md) before putting this source on a
public GitHub repository. The GBFR_AutoReBattle base project currently has no
license file, so this full derivative source must stay **private** until its
copyright holder grants permission or publishes a license. Do not add an MIT
license merely because an upstream README badge says MIT.

Chiaki is AGPL-3.0-or-later. This source repository intentionally does not
include Chiaki binaries or source. A portable package containing Chiaki must
publish the exact corresponding source at the same download location; see
[CORRESPONDING_SOURCE.md](CORRESPONDING_SOURCE.md), [NOTICE.md](NOTICE.md), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The current package is a private release candidate. Do not attach it to a
public forum post or public GitHub Release until every blocker is resolved.

## One-stop user package

For ordinary users, make one release package containing this built application,
a separately obtained Chiaki release, and optional signed component installers.
Each component has its own install button and its own `.cmd` launcher; selecting
one never starts the other. The app's **检查后台环境** button reports whether the
background input chain is ready. If it requests a driver, use the matching
install button and approve the normal Windows UAC prompt. The optional HidHide
button is only for isolating a physical HID controller when duplicate input is
observed; the app never changes its device list automatically.

Driver installation cannot be silent: signed kernel-level Windows drivers must
be approved by Windows. Do not bundle a driver installer unless its own
redistribution terms allow it. When no local installer is present, the tool
opens the official release page.

Suggested release layout:

```text
GBFR_AutoReBattle-Chiaki/
  启动工具.cmd
  GBFR_AutoReBattle/
    GBFR_AutoReBattle.exe
    _internal/
  Chiaki/
    chiaki.exe
    ...official Chiaki release files...
  Dependencies/                 # optional, only if redistribution is allowed
    ViGEmBus_1.22.0_x64_x86_arm64.exe
    HidHide_1.4.202_x64.exe
  NOTICE.md
  THIRD_PARTY_NOTICES.md
```

Keep the full Chiaki directory next to the automation EXE. Chiaki can be
covered by other windows in background mode, but must not be minimized because
the stream can stop presenting frames.

## User setup

1. Start `启动工具.cmd` and accept the administrator prompt.
2. Click **启动 Chiaki**, register the console, and connect to the PS5.
3. Click **一键同步输入配置**. Foreground mode reads the existing Chiaki
   keyboard mappings; there is no need to remap them by hand. Background mode
   does not depend on Chiaki keyboard mappings.
4. For background mode, check **后台运行**, click **检查后台环境**, and install
   the requested component if necessary.
5. Press **启动自动重战**. Use `F2` to stop automation immediately.

The AccountID helper opens Sony's sign-in page in the user's browser. Do not
share the returned redirect URL; it contains an authorization code. The app
does not store the PSN password or redirect URL.

## Source setup and build

This project targets Windows with Python 3.10 or later. Use PowerShell 7:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
.\scripts\build_release.ps1 -Python "python"
```

For a fresh checkout, the dependency setup and complete runtime check can be
run in one step:

```powershell
.\scripts\setup_dev.ps1 -Python "py -3.10"
py -3.10 .\main.py --diagnostics
```

`--diagnostics` reports missing OCR and controller packages individually. In
particular, `Shapely` is required by the vendored RapidOCR detector; it is
already listed in `requirements.txt`, but it is not present in an unrelated
Python runtime until that environment installs the project requirements. The
setup script runs this same diagnostic after installation, so it cannot report
success while a required package is still missing.

The output is `release\dist\GBFR_AutoReBattle`. Add Chiaki separately when
assembling a user package. The included PyInstaller specification uses paths
relative to the project root, so it can build from a cloned repository.

## Development run

```powershell
py -3.10 -m pip install -r requirements.txt
py -3.10 .\main.py --gui
```

Background input requires the supported virtual-controller driver to be installed;
HidHide is optional and not required by this application.

## Security and release hygiene

Never commit runtime logs, screenshots, PSN redirect URLs, authorization codes,
AccountID, console registration data, Chiaki settings, EXE/DLL files, or local
driver installers. `.gitignore` covers the normal local files. Before staging
source, run:

```powershell
.\scripts\verify_publish_tree.ps1
```

See [SECURITY.md](SECURITY.md) for private-data handling and
[CONTRIBUTING.md](CONTRIBUTING.md) for sanitized bug-report requirements.
