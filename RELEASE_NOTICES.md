# Binary release notices

This file is for a binary package that contains both this automation program
and a separately obtained Chiaki Windows release.

## Chiaki

Chiaki is Free and Open Source Software under the GNU Affero General Public
License v3.0 or later. Keep the `Chiaki\LICENSE.txt` distributed with its
official Windows package. Corresponding source and the original project are
available at https://git.sr.ht/~thestr4ng3r/chiaki . Do not represent this
package as an official Chiaki release.

## Windows capture and virtual controller driver

The Windows Capture component is bundled into the GBFR executable. It is used
for background stream frames and is distributed under its upstream MIT terms;
the full OpenCV package is not required because this project only uses the
library's optional image-save compatibility path.

Background input uses a virtual DualShock 4 provided by vgamepad and the
ViGEmBus driver. This installer includes the official ViGEmBus v1.22.0 setup
program and `ViGEmBus-LICENSE.txt`. Windows must show a UAC confirmation for
driver installation; do not attempt to install it silently.

## GBFR_AutoReBattle base

The base project is https://github.com/xinbaji/GBFR_AutoReBattle . At the time
this package was prepared, its repository had no explicit license file. Public
redistribution of this derivative requires permission from the base copyright
holder. See `NOTICE.md` and `PUBLISH_BLOCKERS.md` in the source package.

Granblue Fantasy, PlayStation, PSN, Sony, Cygames, and Chiaki are trademarks or
property of their respective owners. This is an independent fan utility and is
not endorsed by any of them.
