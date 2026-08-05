# Binary release notices

Release candidate: **v0.1.0 (Windows build V41, 2026-08-03)**.

This candidate is for private testing and archival only while the blockers in
`PUBLISH_BLOCKERS.md` remain unresolved. These notices do not create permission
to distribute code that has no license.

This file is for a binary package that contains both this automation program
and a separately obtained Chiaki Windows release.

## Chiaki

Chiaki is Free and Open Source Software under the GNU Affero General Public
License v3.0 or later. The bundled build is Chiaki 2.2.0 at revision
`89368f63c99d67cde8868c0269b66a1b0c507397`. The ordinary tag ZIP omits Git
submodule contents and is not complete corresponding source by itself. Before
distribution, provide a complete tag-and-submodule source archive at the same
download location, retain `Chiaki\LICENSE.txt`, and follow
`CORRESPONDING_SOURCE.md`. The original project is at
https://git.sr.ht/~thestr4ng3r/chiaki . Do not represent this package as an
official Chiaki release.

## Windows capture and virtual controller driver

The Windows Capture component is bundled into the GBFR executable. It is used
for background stream frames and is distributed under its upstream MIT terms;
the full OpenCV package is not required because this project only uses the
library's optional image-save compatibility path.

Background input uses a virtual DualShock 4 provided by vgamepad and the
ViGEmBus driver. The local portable package includes the official ViGEmBus
v1.22.0 setup program and `ViGEmBus-LICENSE.txt`. Windows must show a UAC
confirmation for driver installation; do not attempt to install it silently.

HidHide 1.4.202 is an optional installer from Nefarius Software Solutions e.U.
It is provided as a separate installer button and `安装HidHide.cmd`; it is not
required for every installation. Use it only when physical HID devices conflict
with the virtual DS4 path. The tool does not configure HidHide's device hiding
list or allowlist automatically. The bundled installer is Authenticode-signed;
its SHA256 is recorded in the package checksum file.

## GBFR_AutoReBattle base

The base project is https://github.com/xinbaji/GBFR_AutoReBattle . At the time
this package was prepared, its repository had no explicit license file. Public
redistribution of this derivative requires permission from the base copyright
holder. See `NOTICE.md` and `PUBLISH_BLOCKERS.md` in the source package.

Granblue Fantasy, PlayStation, PSN, Sony, Cygames, and Chiaki are trademarks or
property of their respective owners. This is an independent fan utility and is
not endorsed by any of them.
